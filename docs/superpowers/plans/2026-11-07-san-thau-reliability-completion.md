# Săn Thầu Reliability Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the RC3-9 collector and local data store with evidence-backed data safety, missing-only collection, deep parsing correctness, serving projection validation, and verified Windows/Linux packages while leaving PDF download out of scope.

**Architecture:** Preserve the current RC3-8 architecture and dirty RC3-9 changes. Work proceeds from a read-only data snapshot, through correctness fixes and serving projection verification, then package builds and one bounded live canary. Warehouse remains the source of provenance and serving remains a read projection.

**Tech Stack:** Python 3.12, SQLite, pytest, ruff, PySide6/QML, Nuitka, Inno Setup, Docker Linux builder, SHA256 manifests.

**Spec:** `docs/superpowers/specs/2026-11-07-san-thau-reliability-completion-design.md`

## Global Constraints

- Do not reset, clean, or overwrite the dirty RC3-9 worktree.
- Preserve `src/santhau_studio/desktop/qml/DateField.qml` and `tests/integration/test_date_field_navigation.py`.
- Do not mutate `D:\Harvest\SanThauData` during source or package builds.
- Keep `collection_default_policy=MISSING_ONLY`; refresh, reconcile, and expiry refresh require explicit choice.
- Do not download PDF/document bytes in this plan.
- Linode package installation and historical backfill are explicitly authorized by the current request; the current Windows installation remains untouched.
- Do not broaden the source boundary beyond verified `egp_home` families.
- Every live canary must report request, attempt, proof, reuse, and failure evidence.

## Review Focus

- Complete coverage and compatible deep captures must produce zero new network requests. Test in the coverage and deep reuse tasks.
- Malformed or unregistered HSMT forms must remain partial/unverified and preserve good projections. Test in the parser task.
- Refreshing one deep lane must preserve evidence from the other lanes. Test in the projection task.
- Serving must detect content flips and key swaps while avoiding an unchanged rebuild. Test in the serving task.
- Shutdown must preserve waiting work and must not create false cancellations. Test in the worker-control task.

---

### Task 1: Freeze and verify the current Data Root

**Files:**
- Create: `tools/rc3_9_data_snapshot.py`
- Modify: `tools/verify_release_evidence.py` only if a reusable manifest helper is required
- Test: `tests/integration/test_data_root_lock.py` or a new `tests/integration/test_data_snapshot.py`

- [ ] Write a failing test for a snapshot manifest containing warehouse, serving, raw segment, jobs, schema version, row counts, and SHA256 values without opening a writer.
- [ ] Run the focused test and confirm it fails because the snapshot helper does not exist.
- [ ] Implement a read-only snapshot command that refuses to run when a live writer lock/process is detected, records file metadata and selected table counts, and writes a JSON manifest outside the Data Root.
- [ ] Add checks for source record integrity status, raw object reference counts, coverage proof references, deep capture references, and serving metadata presence.
- [ ] Run the focused test and confirm the manifest is deterministic for an unchanged fixture.
- [ ] Run the snapshot against a copy of `D:\Harvest\SanThauData`; store the evidence under `_evidence/rc3-9` and keep the original Data Root untouched.

### Task 2: Lock down raw recovery and missing-only coverage

**Files:**
- Modify: `src/santhau_studio/storage/rawstore.py`
- Modify: `src/santhau_studio/storage/coverage.py`, `src/santhau_studio/storage/coverage_contract.py`, or the owning coverage modules as found during implementation
- Modify: `src/santhau_studio/workers/runner.py` and `src/santhau_studio/workers/scoped_maintenance.py` only where the current policy path requires it
- Test: `tests/integration/test_segment_store.py`
- Test: `tests/integration/test_missing_only_policy.py`, `tests/integration/test_coverage_store.py`, `tests/integration/test_scoped_maintenance.py`

- [ ] Run the existing raw-store regression first and record the current dirty RC3-9 result.
- [ ] Keep the existing constant-time membership regression; add a failing case for a missing raw row plus an unreadable present object so both findings are classified independently.
- [ ] Implement the smallest raw recovery change that preserves the missing-row set outside the write loop and records the correct repair finding.
- [ ] Add a failing coverage test showing a complete proof suppresses a request, while a partial proof creates only the missing interval.
- [ ] Implement the policy decision using persisted proof and interval status, with explicit refresh as the only bypass.
- [ ] Verify quota exhaustion does not rescan all windows per task and preserves interrupt handling.
- [ ] Run the focused raw, coverage, policy, and scheduler tests; then run the full suite before moving on.

### Task 3: Complete deep reuse and parser capability handling

**Files:**
- Modify: `src/santhau_studio/workers/deep_reuse.py`
- Modify: `src/santhau_studio/workers/public_deep_run.py`
- Modify: `src/santhau_studio/collectors/cgttrg.py`
- Modify: `src/santhau_studio/storage/public_deep.py`
- Modify: `src/santhau_studio/storage/projection_evidence.py`
- Test: `tests/integration/test_deep_reuse.py`
- Test: `tests/integration/test_public_deep_refresh.py`
- Test: `tests/integration/test_auto_deep.py`
- Test: `tests/integration/test_hsmt_semantics.py`

- [ ] Add failing tests for parser-version reuse with zero network calls, malformed nested shapes, unregistered form codes, and explicit force refresh.
- [ ] Add a failing test that a `DETAIL` refresh cannot remove a `REOFFER` confirmation and that a genuine projection collision rolls back atomically.
- [ ] Implement reuse checks for record identity, listing hash, lane, parser version, source version, and raw readability; keep freshness unchanged on reuse.
- [ ] Implement explicit capability states: `NOTICE_METADATA`, `STRUCTURED_ITEMS`, `UNVERIFIED_FORM`, and parse-error provenance. Do not mark invalid nested data as full.
- [ ] Implement projection ownership merge/retirement with savepoints and preserved raw/history.
- [ ] Run deep parser, auto-deep, replay, metadata, and projection tests. Record fetch counts separately from logical task counts.

### Task 4: Validate and slim the serving projection

**Files:**
- Modify: `src/santhau_studio/storage/serving.py`
- Modify: `src/santhau_studio/desktop/warehouse_reader.py`
- Modify: `src/santhau_studio/desktop/backend.py`
- Modify: `src/santhau_studio/desktop/diagnostics.py`
- Modify: `src/santhau_studio/desktop/history.py`
- Test: `tests/integration/test_serving_snapshot.py`
- Test: `tests/integration/test_noop_snapshot.py`
- Test: relevant desktop backend/history tests

- [ ] Run the serving RED tests for audit-table exclusion, deep-state retention, reader closing, and content-flip detection.
- [ ] Implement table allow-listing so serving remains a projection and warehouse-only provenance reads go through `WarehouseReader`.
- [ ] Implement a full snapshot digest over all snapshot columns and linkage keys; missing manifest digest must force rebuild.
- [ ] Keep the dirty-path fast path free of an expensive no-op digest when content is already known changed.
- [ ] Add a copy-on-write rebuild command that operates on a warehouse copy and emits before/after row counts, digest, quick check, and integrity evidence.
- [ ] Rebuild serving on a Data Root copy, compare against the frozen manifest, and only then prepare a production rebuild command. Do not run the production rebuild in this task.

### Task 5: Finish worker control and bounded read behavior

**Files:**
- Modify: `src/santhau_studio/workers/jobs.py`
- Modify: `src/santhau_studio/workers/scoped_maintenance.py`
- Modify: `src/santhau_studio/desktop/collection_status.py`
- Modify: `src/santhau_studio/desktop/deep_status.py`
- Modify: `src/santhau_studio/desktop/supervisor.py`
- Modify: `src/santhau_studio/desktop/backend.py` for bounded record/deep reads
- Test: `tests/integration/test_hardening_control.py`
- Test: `tests/integration/test_runtime_status.py`, `tests/integration/test_deep_status.py`, `tests/integration/test_supervisor_handshake.py`

- [ ] Add failing tests for shutdown preserving `WAITING`, maintenance visibility, bounded deep queue reads, stale reply guards, and supervisor readiness handshake.
- [ ] Implement shutdown/restart semantics that preserve waiting work and reserve cancellation for an explicit user action.
- [ ] Bound deep queue and record reads with stable pagination and one read snapshot.
- [ ] Add serial/record guards for asynchronous detail/raw replies so an old record cannot overwrite a new selection.
- [ ] Make supervisor startup report `starting`, `ready`, or `failed` only after the bounded IPC handshake.
- [ ] Run focused UI/backend/control tests without launching the installed production app.

### Task 6: Verify source and build RC3-9 artifacts

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, `src/santhau_studio/desktop/backend.py`, `src/santhau_studio/linux/cli.py`
- Modify: `tools/build_linux.py`, `packaging/inno/santhau-data-studio.iss`, `packaging/nuitka/build_standalone.ps1`
- Test: release manifest and packaging verification tests

- [ ] Confirm all existing RC3-9 version changes and raw-store changes are intentional; preserve unrelated dirty files.
- [ ] Run `pytest` targeted tests, then the full suite, `ruff check .`, secret gate, and `git diff --check` on the final source tree.
- [ ] Build Linux sequentially with the existing offline builder and produce `.deb`, tarball, source manifest, and SHA256SUMS.
- [ ] Build Windows sequentially from the same sealed source state and produce GUI, collector, installer, manifest, and SHA256SUMS.
- [ ] Run packaged offline smoke twice on each platform and assert zero rerun additions, schema compatibility, and no network client construction.
- [ ] Record that the current Windows RC3-7 installation remains untouched.

### Task 7: Build, install, and canary the Linux server artifact

**Files / evidence:**
- Use: `D:/Harvest/releases/SanThau-RC3-7-20261107/SERVER-HANDOFF-RC3-8.md`
- Create: `D:/Harvest/santhau-linux/_evidence/rc3-9/server-install/`
- Use: existing `packaging/linux/install.sh`, service unit, and release manifest

- [ ] Confirm the Linode target, Cloudflare proxy command, service unit, data root, free disk, current version, and running process without changing state.
- [ ] Create a timestamped server backup of warehouse and jobs and verify its checksums before stopping the service.
- [ ] Build the final Linux artifact from a sealed source manifest and verify its SHA256 locally.
- [ ] Upload only the package, checksum, and required manifest to the server.
- [ ] Stop the collector service, verify it exited, install the package, verify `--version`, schema compatibility, and package manifest.
- [ ] Run a no-network server smoke/quick check against the existing data root, then start the service and capture readiness/health evidence.
- [ ] Run one bounded one-day live canary. Stop and restore the prior package if the canary violates proof, request, resource, or service-health gates.

### Task 8: Run resumable historical backfill

**Files / evidence:**
- Create: `D:/Harvest/santhau-linux/_evidence/rc3-9/server-backfill/PROGRESS.md`
- Use: server collector CLI and existing scope/auto-pull commands

- [ ] Configure the server scope from the approved historical start through the current cutoff while preserving the existing proof records.
- [ ] Keep `MISSING_ONLY`, `reconcile_enabled=false`, bounded daily quotas, and explicit refresh disabled.
- [ ] Start the backfill in small family/interval batches after the canary, recording job id, interval, request count, attempts, proof outcome, and resource metrics.
- [ ] Between batches verify that complete proofs generate zero new requests and that workers resume after restart without recrawling completed intervals.
- [ ] Stop the backfill on source refusal/CAPTCHA, repeated timeout, malformed payload, disk guard, service crash, or proof inconsistency. Preserve the failed batch evidence.
- [ ] Periodically rebuild/verify serving through the service path and record warehouse/serving row counts and digests.
- [ ] Do not claim full historical completion until every configured interval has a terminal proof or an explicit documented blocker.

### Task 9: Publish the final report

**Files:**
- Create: `D:/Harvest/santhau-linux/_evidence/rc3-9/README.md`
- Create: `D:/Harvest/santhau-linux/_evidence/rc3-9/data-readiness-report.md`
- Use: existing canary/CLI commands; do not add PDF behavior

- [ ] Select one interval/family with partial or missing proof and document why it is eligible.
- [ ] Run one bounded canary with the verified `egp_home` source and stop on refusal, CAPTCHA, repeated timeout, malformed response, or proof inconsistency.
- [ ] Verify complete proofs and compatible deep captures generated zero new network requests.
- [ ] Verify atomic updates to raw, observations, coverage, and deep provenance on the canary copy.
- [ ] Stop the worker after the canary and capture job state, process state, and Data Root timestamps.
- [ ] Write the final Vietnamese report with local and server counts, coverage, deep capability, raw/document byte status, serving result, package evidence, backfill progress, and explicit limitations.

## Verification order

1. Snapshot fixture and Data Root copy.
2. Raw/coverage tests.
3. Deep/parser/projection tests.
4. Serving tests and copy rebuild.
5. Worker/UI bounded-read tests.
6. Full suite, ruff, secret gate, diff check.
7. Linux build/smoke, then Windows build/smoke.
8. One live canary on the server Data Root.
9. Resumable historical backfill with a progress ledger.

## Rollback

- Restore the frozen warehouse and raw manifest if a verification step changes data unexpectedly.
- Do not replace the installed RC3-7 Windows runtime.
- Keep RC3-9 artifacts separate until manifests and smoke evidence pass.
- If a live canary violates missing-only or proof rules, stop the worker and retain the failed evidence without retrying broadly.

