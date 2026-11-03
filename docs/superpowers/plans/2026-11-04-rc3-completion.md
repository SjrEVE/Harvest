# RC3 Completion Implementation Plan

> Execution: `executing-plans`, inline because this session exposes no subagent dispatch tool.

**Goal:** Complete the remaining v1.3 product flows, measure the existing 1M synthetic corpus,
and deliver an RC3 installer with evidence and honest external gates.

**Architecture:** Keep Python 3.12, PySide6/QML, one collector writer, SQLite/WAL, raw segments and
named-pipe IPC. Extend existing modules with narrowly scoped import, search/read-model and UI adapters.
Do not change frozen identity/proof/coverage contracts or edit the source harvester repository.

**Spec:** `C:\Users\media\WorkBuddy AI\Harvest\deliverables\gstack\master-spec-consolidated-2026-11-03.md`
plus the user's RC3 batch A–F in this chat.

## Global constraints

- RC2 inputs: 75/75 hashes and all 3 artifact hashes matched before any edit.
- RC2 checkpoint: `4940abe`, preserving original manifest under `docs/releases/`.
- Work on local `codex/rc3-completion` in this already dedicated repository. No remote/push/PR/deploy.
- Existing canonical schema is unchanged. Derived serving tables/indexes and import receipt files
  are implementation details; a change to canonical identity/status/proof semantics requires owner input.
- Live canary requires explicit owner approval. Never use exposed token A1 or bypass CAPTCHA.
- Performance: startup p95 <=5s; 50-row query p95 <=500ms; GUI event-loop stall <=200ms; RAM <=8GB.
- External checks must remain BLOCKED/NOT_TESTED when no VM/certificate/legal decision exists.

## Review focus

- Reimport the same archive from another directory, overlap a newer archive, or restart after a crash.
- Missing/corrupt/traversing raw references: preserve metadata and reject promotion, never invent source bytes.
- Legacy COMPLETE without full modern proof must remain visibly legacy/unverified.
- Large results/history cannot freeze Qt; changing Data Root invalidates in-flight results and cursors.
- Pause/stop/recovery and maintenance must preserve transactions and source evidence during cancellation.

## Task 1 — Legacy importer

Files: `storage/import_legacy.py`, `workers/maintenance.py`, `collectors/extraction.py`,
`tests/integration/test_import_legacy.py`, faithful synthetic legacy fixture generator.
Interface: `inspect_legacy(root) -> dict`; `import_legacy(source, target, progress, stop) -> dict`.
Use read-only legacy SQLite v1–v5 plus one sequential read per JSONL.gz file. Preserve original source,
raw envelope/locator, timestamps and version ledger in content-addressed import receipts. Reuse the
production writer and existing import_batches/source_aliases/settings; no schema shortcut to COMPLETE.

- [ ] RED: triple import/cardinality, moved bundle, overlap/resume, A→B→A/raw-only/missing version,
  missing raw, corrupt gzip, traversal, coverage unverified and source unchanged.
- [ ] Implement and run targeted tests, then checkpoint.

## Task 2 — Serving and query performance

Files: `storage/serving.py`, new `desktop/search.py`/`desktop/history.py` if needed, `desktop/backend.py`,
`tools/perf_probe.py`, benchmark harness and query tests.
Interface: `search(payload)` keeps existing keys and adds explicit filters/sort/cursor; history is paged.
Materialize indexed display/filter fields in serving only; preserve unknown values. Copy source through a
consistent SQLite backup and rebuild derived indexes without fetching million-row Python lists.

- [ ] RED: filter/unknown semantics, stable sorting, cursor invalidation, deep history and cold snapshot.
- [ ] Reuse `_evidence/rc/perf-1m` for before/after queries; create a separate maintenance fixture from it
  (label its synthetic/raw limitations). Measure rebuild/backup/restore, process RSS and I/O.
- [ ] Optimize until targets pass or record precise measured failures; checkpoint.

## Task 3 — Runtime and bounded source verification

Files: `workers/jobs.py`, `workers/runner.py`, `collectors/http_client.py`, `collectors/canary.py`,
`tools/canary.py`, `workers/deep_run.py`, focused runtime tests.
Interfaces: observable job status/progress, validated budgets, recovered queue; owner-gated canary plan,
probe and collection; deep manifests explicitly declare verified paths and reasons when blocked.

- [ ] RED: target-scoped pause/resume/stop, dead worker recovery, last network attempt evidence,
  request/page/time budgets, source disable and expired adapter/rule.
- [ ] Implement bounded tools and offline fixtures; no live execution; checkpoint.

## Task 4 — Complete UI-01..10

Files: `desktop/bridge.py`, `desktop/operations.py`, `desktop/backend.py`, QML modules and UI tests.
Interface: asynchronous read requests with generation IDs and result/error signals; maintenance stays
in collector processes. Extend existing screens instead of redesigning the shell.

- [ ] UI-01 root/new/import; UI-02 cached counters/unknown/stale; UI-03 sources/canary reasons;
  UI-04 budgets/progress/controls; UI-05 heatmap/resolver/proof; UI-06 filter/sort/paging;
  UI-07 raw/history/aliases/manual audited link actions; UI-08 deep queue/refresh/blocked;
  UI-09 integrity/findings/actions; UI-10 schedule/storage/backup/restore/logs/license.
- [ ] RED: asynchronous root switching, responsiveness, history limits, validation/empty/error states.
- [ ] Keyboard/accessibility names and DPI-safe layouts; checkpoint.

## Task 5 — Application acceptance and large-store proof

Use the actual native GUI with the computer-use skill for input; record observations/screenshots and
operation IDs. Explicit offline acceptance profile uses synthetic source and is visibly labelled.

- [ ] Root selection/persistence, worker start, 3 run modes, pause/resume/stop/recovery.
- [ ] Coverage/proof/dashboard/search/history/raw/deep/import/backup/restore/export via UI.
- [ ] DPI 100/125/150, keyboard, empty/error, large-data responsiveness and measured startup.
- [ ] Acceptance rows reference exact evidence and PASS/FAIL/BLOCKED/NOT_TESTED, not inference.

## Task 6 — Release engineering

Files: existing Nuitka/Inno scripts, release manifest, notices, release docs and acceptance table.

- [ ] Audit actually bundled licenses, include available official texts; record owner LGPL/commercial choice.
- [ ] Prepare signing procedure, do not sign without cert. Discover clean Windows environment; otherwise NOT_TESTED.
- [ ] Scan source/artifacts/logs, preserve source-only versus synthetic evidence boundaries.
- [ ] Commit final source, build RC3, verify all input and artifact hashes, local install/upgrade/uninstall
  and reinstall preserving Data Root. Never rebuild from a changed tree without resealing.
- [ ] Final report + installer SHA256 + before/after benchmark + UI/import evidence + acceptance matrix.
