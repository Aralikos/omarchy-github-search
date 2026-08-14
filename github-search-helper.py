#!/usr/bin/env python3
"""Logic coprocess for the emiifont.github-search Omarchy shell plugin.

The QML overlay is a thin view; every decision lives here. Communication is
JSON-lines over stdin/stdout:

  stdin  <- {"cmd": "filter", "query": "..."}
  stdin  <- {"cmd": "refresh", "force": true|false}
  stdin  <- {"cmd": "open", "url": "https://..."}
  stdin  <- {"cmd": "clone", "name": "owner/repo"}
  stdin  <- {"cmd": "copy", "name": "owner/repo"}

  stdout -> {"event": "config", "cloneRoot": "/home/user/Development/github"}
  stdout -> {"event": "status", "refreshing": bool, "count": N, "error": ""}
  stdout -> {"event": "rows", "query": "...", "rows": [{name, desc, url, priv, cloned}]}

Status errors: "" (none), "gh-missing", "gh-auth", "fetch-failed".

The helper exits when stdin closes, so it never outlives the shell overlay.
Only the Python standard library is used.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HOME = os.path.expanduser("~")
CACHE_FILE = os.path.join(HOME, ".cache", "omarchy-github-search", "repos.json")
CONFIG_FILE = os.path.join(HOME, ".config", "omarchy", "github-search.json")
DEFAULT_CLONE_ROOT = os.path.join(HOME, "Development", "github")
DEFAULT_REFRESH_MINUTES = 15
GH_FIELDS = "{name: .full_name, desc: .description, url: .html_url, private: .private}"
GH_ENDPOINT = "user/repos?per_page=100&affiliation=owner,organization_member,collaborator"
MAX_ROWS = 200

_write_lock = threading.Lock()
_state_lock = threading.Lock()
_repos = []
_last_query = ""
_last_error = ""


def emit(payload):
    with _write_lock:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()


def gh_binary():
    return shutil.which("gh") or os.path.join(HOME, ".local", "share", "mise", "shims", "gh")


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def clone_root():
    configured = load_config().get("cloneRoot", "")
    if isinstance(configured, str) and configured:
        return os.path.expanduser(configured).rstrip("/")
    return DEFAULT_CLONE_ROOT


def refresh_minutes():
    configured = load_config().get("refreshMinutes", None)
    if isinstance(configured, (int, float)) and configured >= 0:
        return float(configured)
    return float(DEFAULT_REFRESH_MINUTES)


def cache_is_fresh():
    try:
        age = time.time() - os.path.getmtime(CACHE_FILE)
    except OSError:
        return False
    return age < refresh_minutes() * 60


def ssh_url(full_name):
    return "git@github.com:%s.git" % full_name


def normalize(rows):
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        url = str(row.get("url") or "")
        if not name or not url:
            continue
        out.append({
            "name": name,
            "desc": str(row.get("desc") or ""),
            "url": url,
            "priv": bool(row.get("private") or row.get("priv")),
        })
    return out


def load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as fh:
            return normalize(json.load(fh))
    except (OSError, ValueError):
        return []


def save_cache(rows):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CACHE_FILE))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
        os.replace(tmp, CACHE_FILE)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def classify_fetch_error(stderr):
    text = str(stderr or "").lower()
    if "auth login" in text or "not logged in" in text or "401" in text:
        return "gh-auth"
    return "fetch-failed"


def fetch_repos():
    """Returns (rows, error). One row of NDJSON per repo from gh."""
    gh = gh_binary()
    if not os.path.exists(gh):
        return None, "gh-missing"
    try:
        proc = subprocess.run(
            [gh, "api", GH_ENDPOINT, "--paginate", "--jq", ".[] | " + GH_FIELDS],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, "fetch-failed"
    if proc.returncode != 0:
        return None, classify_fetch_error(proc.stderr)
    rows = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    normalized = normalize(rows)
    if not normalized:
        return None, "fetch-failed"
    return normalized, ""


def filter_repos(repos, query):
    """Every whitespace-separated token must appear in the row's name or
    description. Name hits outrank description hits; earlier and shorter
    name matches rank higher."""
    needle = str(query or "").strip().lower()
    if not needle:
        return repos[:MAX_ROWS]

    tokens = re.split(r"\s+", needle)
    scored = []
    for order, item in enumerate(repos):
        name = item["name"].lower()
        haystack = name + " " + item["desc"].lower()
        score = 0.0
        for tok in tokens:
            at = name.find(tok)
            if at >= 0:
                score += 100 - min(at, 50)
            elif tok in haystack:
                score += 10
            else:
                break
        else:
            score -= min(len(name), 60) / 10
            scored.append((-score, order, item))
    scored.sort()
    return [item for _, _, item in scored[:MAX_ROWS]]


def cloned_dirs():
    try:
        return set(os.listdir(clone_root()))
    except OSError:
        return set()


def emit_rows(query):
    with _state_lock:
        repos = list(_repos)
    have = cloned_dirs()
    rows = []
    for item in filter_repos(repos, query):
        row = dict(item)
        row["cloned"] = item["name"].split("/")[-1] in have
        rows.append(row)
    emit({"event": "rows", "query": query, "rows": rows})


def emit_status(refreshing):
    emit({"event": "status", "refreshing": refreshing, "count": len(_repos), "error": _last_error})


def refresh(force=False):
    global _last_error
    if not force and _repos and cache_is_fresh():
        emit_status(False)
        return
    emit_status(True)
    rows, error = fetch_repos()
    _last_error = error
    if rows is not None:
        save_cache(rows)
        with _state_lock:
            _repos[:] = rows
        emit_rows(_last_query)
    emit_status(False)


def notify(summary, urgent=False):
    cmd = ["notify-send"]
    if urgent:
        cmd += ["-u", "critical"]
    subprocess.Popen(cmd + ["GitHub", summary], start_new_session=True)


def clone(full_name):
    dest = os.path.join(clone_root(), full_name.split("/")[-1])
    if os.path.exists(dest):
        notify("Already cloned: " + dest)
        return
    try:
        proc = subprocess.run(
            [gh_binary(), "repo", "clone", full_name, dest],
            capture_output=True, timeout=600,
        )
        ok = proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if ok:
        notify("Cloned %s into %s" % (full_name, dest))
    else:
        notify("Clone failed: " + full_name, urgent=True)


def copy_ssh(full_name):
    url = ssh_url(full_name)
    try:
        subprocess.run(["wl-copy", url], timeout=10)
        notify("Copied " + url)
    except (OSError, subprocess.TimeoutExpired):
        notify("Copy failed", urgent=True)


def open_url(url):
    if url.startswith("https://") or url.startswith("http://"):
        subprocess.Popen(["xdg-open", url], start_new_session=True)


def valid_repo_name(name):
    return bool(re.fullmatch(r"[\w.-]+/[\w.-]+", name))


def spawn(target, *args):
    threading.Thread(target=target, args=args, daemon=True).start()


def main():
    global _last_query

    emit({"event": "config", "cloneRoot": clone_root()})
    _repos[:] = load_cache()
    emit_rows("")
    spawn(refresh)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        cmd = msg.get("cmd")
        if cmd == "filter":
            _last_query = str(msg.get("query") or "")
            emit_rows(_last_query)
        elif cmd == "refresh":
            spawn(refresh, msg.get("force") is True)
        elif cmd == "open":
            open_url(str(msg.get("url") or ""))
        elif cmd == "clone":
            name = str(msg.get("name") or "")
            if valid_repo_name(name):
                spawn(clone, name)
        elif cmd == "copy":
            name = str(msg.get("name") or "")
            if valid_repo_name(name):
                spawn(copy_ssh, name)


if __name__ == "__main__":
    main()
