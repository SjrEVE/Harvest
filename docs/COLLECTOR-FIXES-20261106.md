# Collector repair and semantic HSMT parsing

This change follows the owner-approved review of `collector-fixes-spec.md` against the RC3-3 source (`c76d015`, documentation successor `2208dbe`). It does not deploy a binary, modify a running warehouse, enqueue production jobs, or call new e-GP endpoints. The Windows installed application and the Linux VPS remain separate installations and data roots.

## What the review confirmed

The previous HSMT parser required `Table` on every form, including auxiliary form 2102 and document forms 3152/3153. Valid goods reported by the parser could therefore fail to reach the warehouse. The parser now has explicit form capabilities, warnings separate from fatal errors, and parser-version metadata. Forms 1957/1960 remain supported; saved samples also verify 2034/2035/2036/2037. Document-only results retain file references and remain PARTIAL for structured goods. References are not downloaded files. Unknown 3150 content remains UNVERIFIED_FORM; no nameless goods are manufactured.

KHAC is an unsupported adapter, not an HTTP access denial. It still makes zero requests, is counted separately as one package with three lane tasks, and is parked until the adapter/parser or listing changes. Identity/version mismatches stay quarantined.

The coverage resolver already merged COMPLETE intervals, and reconciliation already compared identities. This change extends those existing mechanisms with explicit source/family/adapter/endpoint/boundary/query compatibility, full configured-scope reporting, durable bounded maintenance, and content-hash drift detection. Historical PARTIAL rows and drift findings remain in the warehouse.

## Read-only VPS evidence

The authorized SSH connection worked after the owner installed the supplied public key. Only the active September 2026 Linode warehouse was read; the historical backup was not used. No passwords or private keys were copied into the repository.

At **09:02:57 Vietnam time on 6 November 2026**, the current HSMT error set had grown from the spec's earlier 70 records to **137**. All exported raw objects passed SHA256 verification. An isolated local shadow replay produced:

| Result | Packages |
| --- | ---: |
| Structured items (2034–2037 plus auxiliary 2102) | 121 |
| Document references only (3152/3153) | 13 |
| Unverified 3150 shape | 1 |
| UUID mismatch, retained in quarantine | 2 |

The shadow warehouse held 794 goods, 102 lots and 148 document references after replay. A second replay applied zero records; FK checks were empty and `quick_check` was `ok`. It contained no coverage proofs. These are local replay results, not production writes or proof that file-only documents have been extracted.

At **09:18:29 Vietnam time**, the exported coverage ledger contained 344 proofs and 352 interval rows. Resolving all six families across all 30 September days yielded **180/180 proved day-family windows**, zero never-attempted windows and zero unresolved list-coverage gaps. All eight historical PARTIAL intervals were covered by the proof union. The source was not queried again: this verifies stored proof coverage, not current source parity. The export contained **zero RECONCILE runs**, so the audit status is NOT_CHECKED.

Seven sanitized representative fixtures cover every observed form-code combination, including both forms 2036/2037 and the unexpected 3150/UUID mismatch cases. Their manifest identifies transformations and hashes. The private raw corpus and scratch databases remain ignored under `_evidence`; they are not release assets. The original exact 70-record snapshot and the separate Box H2/2025 database were not available, so the Box's five reported gaps are not independently certified here.

## Maintenance behavior

`configure-scope` pins dates, families and verified query contexts in the selected warehouse. Automatic repair and reconciliation are off unless enabled. `coverage-status` resolves every expected VN day/family, reports queued/active separately from proof, and marks historical rows superseded by a proof union without changing their stored status.

`repair-gaps` previews by default. Execution records approved tasks; the sole worker owns HTTP and warehouse writes. Tasks have deterministic keys, persisted due times, exponential backoff with jitter, finite attempts and cumulative HTTP/time budgets. Reservations are committed before HTTP; a crash conservatively consumes an unsettled reservation. User stop/pause holds survive discovery and restart. Explicit cancellation cannot be reset by running discovery again. Paused work resumes through the normal controller.

Repairs resolve remaining gaps with a fresh per-day budget. They never join page cursors from separate runs into a proof: source ordering is insufficient to justify that claim. Trusted totals can stop an undersized probe early; verified finer time windows handle large days. A soft network failure is recorded and other weekly families continue. A parent remains PARTIAL while any assigned gap lacks proof. HTTP access denial, certificate errors and unknown failures require review rather than automatic retries.

Default repair limits: 4 task attempts, 6,000 cumulative requests and 14,400 seconds per task; each day attempt is bounded by 1,200 pages, 1,500 requests and 3,600 seconds; 24 attempts per UTC quota day. Opt-in reconciliation starts after the scope is proved, allows two audits per UTC day, and subsequently rechecks the final three days inside that configured scope at seven-day intervals. It never invents a recent window outside a historical scope. Audit results distinguish CHECKED, PARTIAL and NOT_CHECKED. Count-preserving ID changes and content changes are detected without deleting records.

Transport connect timeout defaults to 15 seconds (configurable 15–20), retries default to four (maximum five), and total deadlines include pacing/backoff. Classified transient TLS EOF/timeouts can retry; certificate errors and unclassified TLS failures cannot. TLS verification, allowlist, page size 10 and at least three seconds between host requests remain enforced. Exhausted transient attempts defer subsequent host work; attempt timestamps and monotonic elapsed/wait measurements are preserved.

## Operator commands after an authorized installation

These examples are documentation, not commands executed against the VPS during this review. Configure while the writer is stopped cleanly; normal runtime control remains available through the collector CLI.

```sh
santhau-collector configure-scope --data-root /home/santhau/SanThauData \
  --from 2026-09-01 --to 2026-09-30
santhau-collector coverage-status --data-root /home/santhau/SanThauData
santhau-collector repair-gaps --data-root /home/santhau/SanThauData --dry-run
santhau-collector replay-deep --data-root /home/santhau/SanThauData --dry-run --limit 200
```

`repair-gaps --execute` authorizes the previewed scoped maintenance work after re-evaluation under the writer lock. `replay-deep --execute` reparses saved raw without HTTP, preserves original capture/refresh times, appends parser history, and is idempotent at the current parser version. Replays should be reviewed by capability, not just by a repaired-task count. `deep-status` reports package and lane counts separately.

For continuing automation, explicitly configure `--enable-repair` and/or `--enable-reconcile`, with the desired `--quota-per-day` and `--reconcile-quota-per-day`. Use the Box's own root and H2/2025 range on the Box; do not reuse the Linode September configuration there.

## Schema, rollback and remaining boundaries

Migrations 003–005 add sidecar telemetry, versioned deep-parser metadata and maintenance ledgers. They do not change the arity of existing frozen tables or add SUPERSEDED to a closed status enum. Backups include the new tables. A binary rollback leaves additive tables in place; do not replace a live database with an older snapshot and lose subsequent captures. Before a production rollout, stop at a safe boundary and take a separate backup for each root.

KQLCNT_FULL, KHAC fetching, file downloads and extraction from file-only documents remain separate unverified capabilities. No OCR/LLM service is invoked. Production replay/repair and new installer deployment are outside this offline change's evidence.

## Verification ledger

- Network commit `d40968a`: actual Linux container regression 476 passed, 4 skipped.
- Aggregate snapshot v1: Linux JUnit 528 tests, zero errors/failures, 4 skips (524 passed), 168.569 seconds; this preceded the final stop/recovery refinements.
- Final snapshot v3: actual Linux container with network disabled **530 passed, 4 skipped**, exit 0, 152.97 seconds; full native Windows suite **755 passed, 9 skipped**, zero failures/errors, 241.63 seconds. Source/test hashes were frozen before execution and match the committed code. Ruff, secret scan (zero findings), and `git diff --check` passed.
- The Windows aggregate exposed a pre-existing stale ping test at baseline 2208dbe: it expected only the weekly capability although the worker already returned runtime-status-v1. Its expectation was corrected against the existing protocol, then the full suite passed.
- A RED/GREEN regression additionally prevents discovery from automatically retrying a known HTTP access refusal. Another test prevents a one-day audit from claiming a two-day scope fully checked. Stop, pause and cancellation have durable holds independent of task discovery.
- Evidence remains local under `_evidence/collector-fixes-20261106`: final-linux-v3.xml, final-windows-v3.xml and final-snapshot-v3-manifest.json; private raw exports are not committed.
- Targeted tests cover auxiliary form ordering, document-only capabilities, malformed/identity quarantine, raw replay twice, old form compatibility, KHAC zero HTTP, scoped proof union, separate Box/Linode scopes, eight-page fresh-budget recovery, crash reservation, duplicate discovery, stop-before-discovery, pause/resume, weekly-family fairness, and identity/content drift.
- Self-review completed against the spec and actual call graph. No independent reviewer is claimed.
