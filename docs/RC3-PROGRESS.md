# RC3 execution ledger

Plan: `docs/superpowers/plans/2026-11-04-rc3-completion.md`.
Working repo: `D:\Harvest\santhau-data-studio`; branch `codex/rc3-completion`.

## Verified baseline

- Original RC2 manifest: base f1c6fc5971038a4cc808b91f92a4153ae17c926a + working tree; 75 inputs match.
- All 3 RC2 artifact hashes match; manifest preserved at `docs/releases/rc2-release-manifest.json`.
- Checkpoint commit: 4940abe. Baseline suite: 519 PASS + scheduler opt-in skipped, 62.56s.
- Baseline 1M search p95 686.47ms (FAIL <=500ms), family p95 458.95ms, dashboard 22.48ms,
  record detail 12.62ms; old harness RSS is null (measurement bug to fix).
- Existing 1M corpus is a serving-only synthetic performance fixture, not a complete ingest/raw warehouse.
  The previous full-ingest benchmark removed its temporary root unless --keep; do not conflate the two.
- Source harvester is read at aa65f0a6edcb98877d705c8495ca00c79303f86e via git show only.

Ruling: use a local feature branch in the supplied dedicated repository; retain large evidence, venv and
RC2 artifacts in place. No external worktree or source-repository modification is needed.
Ruling: canonical contract stays frozen; imported evidence belongs in existing import/alias/settings
structures and durable receipt files. Legacy claims do not become modern COMPLETE proofs.
Pre-flight: importer consumes writer/rawstore and produces data read by serving/UI; query layer produces
paged DTOs consumed by asynchronous bridge; runtime/canary status feeds UI; release consumes all tasks.

## Tasks

- Task 1: CORE IMPLEMENTED — read-only SQLite v1-v5 and checked export bundles; separate version/observation
  import, deterministic event receipts, source timestamps, legacy coverage and durable catalog chunks.
  8 targeted tests PASS; backup/maintenance subset 25 PASS. UI and broader overlap/network acceptance remain in Tasks 3-5.
- Ruling: legacy channels use `legacy:<original source>` to avoid replacing newer live records. Original
  source/run/version metadata is retained in raw catalog chunks and receipts; these chunks survive backup/restore.
  Legacy coverage remains in explicit legacy claims and never receives a modern proof.
- Tasks 2–6: PENDING.

## External gates

- Live canary: BLOCKED pending explicit owner approval (do not ask while offline work remains).
- Authenticode: BLOCKED pending certificate.
- Qt distribution choice: BLOCKED pending owner legal decision; prepare concrete license package first.
- Clean Windows 10/11 VM: NOT_TESTED, discover available environment before finalizing.
## RC3 core checkpoint (in progress)

- Indexed disposable record_fields projection, query filters/unknown/zero semantics, stable sort/keyset cursors and snapshot invalidation.
- Paged record history and hash-verified raw preview; asynchronous GUI read/control tasks with generation invalidation.
- Real native UI acceptance has started on an isolated, explicitly labelled SYNTHETIC OFFLINE profile. First GUI run exposed a concurrent probe-lock false positive; fixed probes to serialize and avoid replacing holder metadata.
- 1M backend benchmark: search p95 63.66ms (RC2 686.47ms), family 433.23ms, dashboard 22.41ms, detail 19.95ms; process working set 34.2MB. Extended sort/cold/UI benchmarks still pending. Initial rebuild 235.56s; text folding optimization needs final measurement.
- Retry failure attempts retained in warehouse; target-scoped job commands; request/page/time budgets and live-canary CLI approval parsing corrected. 81 focused runtime/network/bridge checks passed.
- Import overlap test now covers importing newer archive before older history without moving current backwards: 9 importer tests pass. Manual identity link/merge/split/undo is audited with actual Windows SID and preserves source rows.
- UI filters/history/import/budgets/proof/source ladder/legacy claims/logs/license controls implemented; actual UI acceptance, large maintenance, deep canary tooling and release engineering remain open.
- Interim full suite before last fixes: 534 pass, 2 fail (failure-attempt missing response=None, fixed), 1 scheduler skip. Do not cite interim as release PASS.
