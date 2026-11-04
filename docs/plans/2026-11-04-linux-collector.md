# Linux Collector Implementation Plan

> Execute inline with the existing collector; preserve Windows defaults.

**Goal:** A Linux x86-64 collector-only installer, with no GUI, statistics, FTS or serving rebuild.

**Architecture:** Reuse WorkerLoop, Runner, SQLite/raw storage and proof resolver. Add Linux CLI and owner-only Unix-domain control socket. Linux selects no-serving mode and requeues in-flight work on service shutdown; Windows defaults remain unchanged.

**Deliverables:** A standalone Nuitka directory, .deb, tar.gz with install/uninstall scripts, SHA256 manifest, and Linux test/install evidence from GitHub Actions. No live E-GP calls in tests or package installation.

**Compatibility:** Build on Ubuntu 22.04 x86-64, test on Ubuntu 22.04/24.04. No claim about ARM, Alpine/musl, every distro, or a real systemd boot without evidence.

**Constraints:** No new database or HTTP server. Keep canonical schema. Real Data Root stays outside installation and survives uninstall. Do not copy real records or raw responses into the repository. Imported source rules retain their verification provenance; fresh roots get no fabricated coverage. CAPTCHA stops collection. Installing does not start live collection.

## Tasks

- [ ] Add tested headless flags to WorkerLoop: no serving/stale files, graceful shutdown recovery; Windows regression.
- [ ] Implement CLI init/enqueue/run/status/pause/resume/cancel/stop and protected Unix IPC; test wrong owner, duplicate worker, malformed/oversized requests, crash and signals.
- [ ] Supply only verified source configuration, keep collection proof separate, and build an explicit isolated offline self-test.
- [ ] Package standalone .deb/tar.gz and user service; test installing/upgrading/removing while preserving Data Root.
- [ ] Run Linux core tests and artifact smoke checks on GitHub Actions; download artifacts, verify SHA256, document PASS/FAIL/BLOCKED/NOT_TESTED.

## Review focus

- Same root opened by two workers, stale socket after SIGKILL, and IPC from another UID.
- SIGTERM during a page, deliberate pause versus cancel, and resume without duplicating proven days.
- Unverified/mismatched source profile, synthetic marker removal and malformed day ranges.
- Paths containing spaces; removing installation must never remove the Data Root.
- Artifact imports no Qt/desktop/statistics module and runs without system Python installed.

## Ledger

- Base: aeca6a5d63d2e8212df7be99e0a87125f2803911; isolated worktree D:/Harvest/santhau-linux, branch codex/linux-collector.
- Native worktree registration cannot attach this nested repo; Git worktree is used explicitly. Docker CLI is not usable; GitHub-hosted Ubuntu is the test environment.
