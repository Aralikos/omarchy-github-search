#!/usr/bin/env python3
"""Validates manifest.json against the Omarchy plugin contract in CI,
mirroring what `omarchy plugin validate` enforces on install."""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = ["schemaVersion", "id", "name", "version", "kinds", "entryPoints"]


def fail(message):
    print("manifest check failed: " + message)
    sys.exit(1)


def main():
    path = os.path.join(REPO_ROOT, "manifest.json")
    try:
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        fail("cannot parse manifest.json: %s" % exc)

    for key in REQUIRED:
        if key not in manifest:
            fail("missing required field: " + key)

    if manifest["id"].startswith("omarchy."):
        fail("third-party plugins cannot use the omarchy.* namespace")

    if not isinstance(manifest["kinds"], list) or not manifest["kinds"]:
        fail("kinds must be a non-empty list")

    for kind, entry in manifest["entryPoints"].items():
        if entry.startswith("/") or ".." in entry:
            fail("entry point is not a safe relative path: " + entry)
        if not os.path.isfile(os.path.join(REPO_ROOT, entry)):
            fail("entry point file not found: " + entry)

    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                fail("plugin folders cannot contain symlinks: " + full)

    print("manifest check passed: %s v%s (%s)" % (
        manifest["id"], manifest["version"], ", ".join(manifest["kinds"])))


if __name__ == "__main__":
    main()
