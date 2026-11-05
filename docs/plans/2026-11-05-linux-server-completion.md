# Linux server completion — implementation plan

> Execution: use executing-plans inline in the existing isolated Linux worktree.

**Goal:** deliver updated, tested Ubuntu 22.04/24.04 x86-64 collector packages and push source for the user's server installation.
**Architecture:** retain the collector-only Python/Nuitka runtime, SQLite warehouse, gzip segments and Unix control socket. Port proven Windows core fixes without Qt, read-model imports or serving generation. Keep the existing data format and source request limits.
**Spec:** user's request in this chat plus `docs/linux-collector.md`; current Linux base `12c0b55`, Windows fixes `641a48a`.

## Constraints and decisions

- Use `D:/Harvest/santhau-linux` / `codex/linux-collector`; no changes to the running Windows collector or real Data Root.
- User authorized completion and push. Build/test through the existing GitHub Actions workflow; no server deployment or new live source canary.
- Keep RC3; introduce an ordered package revision for reliable upgrades rather than overwriting indistinguishable installer versions.
- Preserve proof/can_skip, records, histories, queue recovery, pause/resume and single-writer protection.
- No database/hash migration in this package: preserve data compatibility. Keep collector-only storage savings; remove unnecessary bundled dependencies only when packaged self-test proves they are unnecessary.

## Review focus

1. Midnight crossing must re-fetch the formerly open day before claiming COMPLETE.
2. Budget split and STOP after a child must not certify unvisited children; counters retain parent work.
3. Linux shutdown must still requeue unfinished work, and idle status must not show stale progress.
4. Headless operation must never import Qt/serving or generate serving files, including startup and shutdown.
5. Upgrade/install failure must preserve the Data Root; packages must install without Python/Qt or network at runtime.

## Task 1 — port collection and control fixes

Files: collectors/{budget,lane_source,live_builder}.py, domain/coverage.py, workers/{runner,weekly,jobs}.py, linux/cli.py; tests/integration/{test_budget_windowing,test_weekly_open_day,test_collector_runtime,test_live_worker_builder,test_boundary_contract}.py.

- [x] Run current portable collector baseline; retain Linux-specific acceptance for CI.
- [x] Port failing budget/midnight tests from `641a48a`, replacing Windows-only fixture imports. Add Linux runtime/real-builder regression tests and observe RED.
- [x] Port core changes while retaining publish_serving=False and resume_on_shutdown=True. Expose current runtime in Unix status and hide finished progress.
- [ ] Run targeted and full collector regression, lint and secret scan; review diff and commit.

## Task 2 — package and server instructions

Files: tools/build_linux.py, packaging/linux/{smoke.sh,install.sh}, .github/workflows/linux-collector.yml, docs/linux-collector.md, LINUX-READINESS.md.

- [ ] Review and test install lifecycle; keep shutdown/upgrade instructions explicit and Data Root external.
- [ ] Remove unused zstandard runtime packaging while retaining the build dependency; assert absent from bundle and verify compiled self-test.
- [ ] Assign package revision, report installed bytes, update source identity/version, and add new tests to Linux CI.
- [x] Document storage, service setup, chosen Data Root and safe upgrade for Ubuntu x86-64.

## Task 3 — build, verify, deliver

- [ ] Push tested source to codex/linux-collector and follow the exact-SHA Actions build to completion.
- [ ] Require collector suite plus offline install/run/upgrade/remove on Ubuntu 22.04 and 24.04 to pass; fix failures and rerun as needed.
- [ ] Download packages, verify checksums/source manifest, inspect contents and size, retain local release directory.
- [ ] Commit evidence-qualified readiness and download/install instructions, push, verify remote SHA and clean worktree.

## Execution ledger

Ruling: use existing GitHub Actions for actual Linux runtime/build because Docker is not running locally; no new infrastructure needed. Cost: wait for hosted build.
Ruling: retain schema and raw layout rather than introduce an unvalidated storage migration during a server-install release. Additional database compression remains separate work.
