#!/usr/bin/env python3
"""Logic coprocess for the emiifont.github-search Omarchy shell plugin.

The QML overlay is a thin view; every decision lives here. Communication is
JSON-lines over stdin/stdout:

  stdin  <- {"cmd": "filter", "query": "..."}
  stdin  <- {"cmd": "refresh"}
  stdin  <- {"cmd": "open", "url": "https://..."}
  stdin  <- {"cmd": "clone", "name": "owner/repo"}

  stdout -> {"event": "config", "cloneRoot": "/home/user/Development/github"}
  stdout -> {"event": "status", "refreshing": true|false, "count": N}
  stdout -> {"event": "rows", "query": "...", "rows": [{name, desc, url, priv}]}

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

HOME = os.path.expanduser("~")
CACHE_FILE = os.path.join(HOME, ".cache", "omarchy-github-search", "repos.json")
CONFIG_FILE = os.path.join(HOME, ".config", "omarchy", "github-search.json")
DEFAULT_CLONE_ROOT = os.path.join(HOME, "Development", "github")
GH_FIELDS = "{name: .full_name, desc: .description, url: .html_url, private: .private}"
GH_ENDPOINT = "user/repos?per_page=100&affiliation=owner,organization_member,collaborator"
MAX_ROWS = 200

_write_lock = threading.Lock()
_state_lock = threading.Lock()
_repos = []
_last_query = ""


def emit(payload):
    with _write_lock:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()


def gh_binary():
    return shutil.which("gh") or os.path.join(HOME, ".local", "share", "mise", "shims", "gh")


def clone_root():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            configured = json.load(fh).get("cloneRoot", "")
        if isinstance(configured, str) and configured:
            return os.path.expanduser(configured).rstrip("/")
    except (OSError, ValueError):
        pass
    return DEFAULT_CLONE_ROOT


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


def fetch_repos():
    """One row of NDJSON per repo from gh; a failed fetch returns None."""
    try:
        proc = subprocess.run(
            [gh_binary(), "api", GH_ENDPOINT, "--paginate", "--jq", ".[] | " + GH_FIELDS],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    rows = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return normalize(rows) if rows else None


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


def emit_rows(query):
    with _state_lock:
        repos = list(_repos)
    emit({"event": "rows", "query": query, "rows": filter_repos(repos, query)})


def refresh():
    emit({"event": "status", "refreshing": True, "count": len(_repos)})
    rows = fetch_repos()
    if rows is not None:
        save_cache(rows)
        with _state_lock:
            _repos[:] = rows
        emit_rows(_last_query)
    emit({"event": "status", "refreshing": False, "count": len(_repos)})


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


def open_url(url):
    if url.startswith("https://") or url.startswith("http://"):
        subprocess.Popen(["xdg-open", url], start_new_session=True)


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
            spawn(refresh)
        elif cmd == "open":
            open_url(str(msg.get("url") or ""))
        elif cmd == "clone":
            name = str(msg.get("name") or "")
            if re.fullmatch(r"[\w.-]+/[\w.-]+", name):
                spawn(clone, name)


if __name__ == "__main__":
    main()
