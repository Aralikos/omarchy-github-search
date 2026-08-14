"""Unit and protocol tests for github-search-helper.py.

Run from the repository root:  python3 -m unittest discover -s tests -v
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER_PATH = os.path.join(REPO_ROOT, "github-search-helper.py")


def load_helper():
    spec = importlib.util.spec_from_file_location("helper", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load_helper()

REPOS = [
    {"name": "acme/user-service", "desc": "Core user API", "url": "https://github.com/acme/user-service", "priv": False},
    {"name": "acme/billing", "desc": "Talks to the user service", "url": "https://github.com/acme/billing", "priv": True},
    {"name": "acme/user-service-legacy", "desc": "", "url": "https://github.com/acme/user-service-legacy", "priv": False},
    {"name": "other/tools", "desc": "Assorted scripts", "url": "https://github.com/other/tools", "priv": False},
]


class FilterTests(unittest.TestCase):
    def test_empty_query_returns_everything_in_order(self):
        out = helper.filter_repos(REPOS, "")
        self.assertEqual([r["name"] for r in out], [r["name"] for r in REPOS])

    def test_every_token_must_match(self):
        out = helper.filter_repos(REPOS, "user tools")
        self.assertEqual(out, [])

    def test_name_match_outranks_description_match(self):
        out = helper.filter_repos(REPOS, "user")
        names = [r["name"] for r in out]
        self.assertIn("acme/billing", names)
        self.assertLess(names.index("acme/user-service"), names.index("acme/billing"))

    def test_shorter_name_wins_ties(self):
        out = helper.filter_repos(REPOS, "user-service")
        self.assertEqual(out[0]["name"], "acme/user-service")

    def test_case_insensitive(self):
        out = helper.filter_repos(REPOS, "USER-SERVICE")
        self.assertEqual(out[0]["name"], "acme/user-service")

    def test_respects_max_rows(self):
        many = [dict(REPOS[0], name="acme/repo-%d" % i, url="https://github.com/acme/repo-%d" % i)
                for i in range(helper.MAX_ROWS + 50)]
        self.assertEqual(len(helper.filter_repos(many, "")), helper.MAX_ROWS)


class NormalizeTests(unittest.TestCase):
    def test_drops_rows_missing_name_or_url(self):
        rows = [{"name": "a/b", "url": "https://x"}, {"name": "", "url": "https://y"}, {"name": "c/d"}, "junk", None]
        out = helper.normalize(rows)
        self.assertEqual([r["name"] for r in out], ["a/b"])

    def test_accepts_private_and_priv_keys(self):
        out = helper.normalize([
            {"name": "a/b", "url": "https://x", "private": True},
            {"name": "c/d", "url": "https://y", "priv": True},
        ])
        self.assertTrue(all(r["priv"] for r in out))

    def test_non_list_input(self):
        self.assertEqual(helper.normalize(None), [])
        self.assertEqual(helper.normalize({"name": "a"}), [])


class ConfigTests(unittest.TestCase):
    def with_config(self, content):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        self.addCleanup(os.unlink, path)
        original = helper.CONFIG_FILE
        helper.CONFIG_FILE = path
        self.addCleanup(setattr, helper, "CONFIG_FILE", original)

    def test_clone_root_default_when_no_config(self):
        original = helper.CONFIG_FILE
        helper.CONFIG_FILE = "/nonexistent/config.json"
        try:
            self.assertEqual(helper.clone_root(), helper.DEFAULT_CLONE_ROOT)
        finally:
            helper.CONFIG_FILE = original

    def test_clone_root_expands_tilde_and_strips_slash(self):
        self.with_config('{"cloneRoot": "~/src/"}')
        self.assertEqual(helper.clone_root(), os.path.expanduser("~/src"))

    def test_clone_root_ignores_invalid_json(self):
        self.with_config("{nope")
        self.assertEqual(helper.clone_root(), helper.DEFAULT_CLONE_ROOT)

    def test_refresh_minutes_default_and_override(self):
        self.with_config('{"refreshMinutes": 60}')
        self.assertEqual(helper.refresh_minutes(), 60.0)
        self.with_config('{"refreshMinutes": "bogus"}')
        self.assertEqual(helper.refresh_minutes(), float(helper.DEFAULT_REFRESH_MINUTES))


class MiscTests(unittest.TestCase):
    def test_ssh_url(self):
        self.assertEqual(helper.ssh_url("a/b"), "git@github.com:a/b.git")

    def test_valid_repo_name(self):
        self.assertTrue(helper.valid_repo_name("owner/repo.name-x"))
        self.assertFalse(helper.valid_repo_name("owner"))
        self.assertFalse(helper.valid_repo_name("owner/repo; rm -rf /"))
        self.assertFalse(helper.valid_repo_name("../etc/passwd"))

    def test_classify_fetch_error(self):
        self.assertEqual(helper.classify_fetch_error("run: gh auth login"), "gh-auth")
        self.assertEqual(helper.classify_fetch_error("HTTP 401 Unauthorized"), "gh-auth")
        self.assertEqual(helper.classify_fetch_error("connection reset"), "fetch-failed")


class ProtocolTests(unittest.TestCase):
    """End-to-end: run the helper as a subprocess with a fake gh on PATH."""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        bindir = os.path.join(self.home.name, "bin")
        os.makedirs(bindir)
        fake_gh = os.path.join(bindir, "gh")
        rows = "\n".join(json.dumps({
            "name": r["name"], "desc": r["desc"], "url": r["url"], "private": r["priv"],
        }) for r in REPOS)
        with open(fake_gh, "w") as fh:
            fh.write("#!/bin/sh\ncat <<'EOF'\n%s\nEOF\n" % rows)
        os.chmod(fake_gh, 0o755)
        env = dict(os.environ)
        env["HOME"] = self.home.name
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        self.proc = subprocess.Popen(
            [sys.executable, HELPER_PATH],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
        )
        self.addCleanup(self.terminate)

    def terminate(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except OSError:
                pass

    def send(self, payload):
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def read_until(self, predicate, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            if predicate(msg):
                return msg
        self.fail("expected event not received in time")

    def test_startup_config_then_rows_then_filter(self):
        config = self.read_until(lambda m: m["event"] == "config")
        self.assertTrue(config["cloneRoot"].startswith(self.home.name))

        # Fresh HOME means empty cache, then the fake gh populates 4 repos.
        self.read_until(lambda m: m["event"] == "rows" and len(m["rows"]) == len(REPOS))

        self.send({"cmd": "filter", "query": "billing"})
        rows = self.read_until(lambda m: m["event"] == "rows" and m["query"] == "billing")
        self.assertEqual([r["name"] for r in rows["rows"]], ["acme/billing"])
        self.assertIn("cloned", rows["rows"][0])

    def test_exits_when_stdin_closes(self):
        self.read_until(lambda m: m["event"] == "config")
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.assertIsNotNone(self.proc.poll())


if __name__ == "__main__":
    unittest.main()
