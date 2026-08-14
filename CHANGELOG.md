# Changelog

All notable changes to this plugin are documented here. Versions follow
[semantic versioning](https://semver.org).

## 1.1.1 — 2026-08-14

### Fixed
- Cloning failed when `gh` was configured for SSH git operations: the shell
  process does not inherit `SSH_AUTH_SOCK`, so git could not reach the SSH
  agent. The helper now discovers the agent socket (current env, keychain
  env file, systemd/gnome-keyring/1Password sockets) and, if SSH still
  fails, retries the clone over HTTPS using gh's own token
- Clone failure notifications now include git's actual error message

## 1.1.0 — 2026-08-14

### Added
- `Alt+Enter` opens the selected repo's pull requests page
- `Ctrl+Y` copies the SSH clone URL to the clipboard (overlay stays open)
- Folder badge on rows whose repo already exists in `cloneRoot`
- `refreshMinutes` config key: the repo list only re-fetches when the cache
  is older than this (default 15 minutes); `Ctrl+R` always force-refreshes
- Actionable empty states: distinguishes GitHub CLI missing, not
  authenticated, and network failure — each with the command that fixes it
- Test suite (`tests/`) covering filtering, ranking, config parsing, and the
  stdin/stdout protocol end to end, plus a manifest contract check
- GitHub Actions CI

### Changed
- Refresh moved from `F5` to `Ctrl+R`
- All logic now lives in a Python coprocess (`github-search-helper.py`,
  stdlib only); the QML overlay is a thin view speaking JSON lines
- `jq` is no longer required

## 1.0.0 — 2026-08-14

Initial release: themed overlay that fuzzy-searches your owned, org, and
collaborator repositories. Enter opens in browser, Ctrl+Enter clones,
cached repo list with background refresh.
