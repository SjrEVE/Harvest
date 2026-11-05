# Public CGTTRG deep collection

User specification: attached request in this chat; evidence directory `deep-source-probe` under visualization `01a1aa49-ca2f-7592-b575-acd9d62dd330`.
Base: 641a48a, clean Windows workspace, live worker PID 10612 observed publishing a snapshot. Do not alter running binary or real warehouse. No push/merge/deploy/Computer Use.

## Execution

- [x] Parse saved public CGTTRG HSMT and reoffer responses, retaining schema, all rows, quantities and explicit source roles. Prove 3 lots/28 goods, unallotted 1/38 goods, 5 distinct offerors, invalid-row retention.
- [x] Persist raw/captures/history before derived data; add migration-backed per-record/lane checkpoint/status, lane ownership, document references and exact-code relation provenance. Prove rerun/refresh/cross-lane safety and migration compatibility.
- [x] Wire verified endpoints to normal worker dispatch with shared request budget, host pacing, stop/retry handling and bounded record selection. Update app backend status/read queries and enqueue gate for verified scope.
- [x] Verify saved payloads through warehouse, serving and app queries in isolated roots. Run bounded live batch through ordinary worker configuration on 3 samples (<=30 requests across the initial verification batch, pacing>=3 seconds). Investigate document download only from concrete source evidence; leave any unverified route explicitly blocked.
- [x] Full regression, report per feature source/sample/rows/status and source-vs-installed boundary. No changes to running installed binary or real data in this pass.

## Decisions

- Existing nullable goods lot_id is sufficient; no fake lots or destructive schema change.
- Keep original frozen DDL; additive migration for operational deep checkpoints. Capture history/raw retain historical projections, current derived rows are scoped by capture lane.
- Scope public readiness to CGTTRG; KQLCNT_FULL and other HSMT forms remain unverified. HTTP 400 is a request-contract error, not proof of missing permission.

Outcome: verified public CGTTRG path is functional in source and compiled normal worker on isolated roots; download bytes and other lanes remain BLOCKED/UNVERIFIED as detailed in docs/DEEP-PUBLIC-20261105.md. Main data root and installed runtime unchanged; no push/deploy.
