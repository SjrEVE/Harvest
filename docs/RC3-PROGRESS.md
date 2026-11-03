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
