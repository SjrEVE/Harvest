# Collector durability, semantic forms and offline repair plan

Owner request: review `C:/Users/media/Downloads/collector-fixes-spec.md`; fix the findings that are correct. Baseline package c76d015, branch started from documentation-only successor 2208dbe. Work on `codex/collector-durability-fixes-20261106`; no production writes, enqueue, restart, configuration changes or live canary.

## Verified findings and design decisions

- `_hsmt` requires Table for every form. Local raw independently reproduces 2034+2102 and reversed 2102+2035. Storage skips all projections on TABLE_MISSING even when the parser/receipt counted valid goods, so projected receipt counts are not warehouse-write evidence.
- KHAC is refused before HTTP. Retain that gate; classify as unsupported capability with adapter version rather than access denial/retryable network fault.
- Coverage already performs union/subtraction of valid COMPLETE. Extend this resolver with explicit compatible-contract context and use it everywhere; do not create a second interval algorithm. Reconcile already compares identity sets; add scoped durable scheduling/reporting, not a count-only replacement.
- HTTP connect timeout is 5 seconds, retry default/cap 3. Attempt timestamps exist in the HTTP receipt but are discarded by warehouse persistence. Keep TLS verification, allowlist, source page size 10 and host pacing >=3 seconds.
- `Runner.backfill` handles single-family jobs directly. Weekly continuation currently stops on soft failures/no newly proven fraction. Add durable scoped repairs shared by both paths, preserving user stop/pause/shutdown semantics and never treating queue completion as proof.
- Full audit JSON/raw corpus is on a separate user VPS, not locally attached. Do not claim replay of all 70 payloads or exact production gap counts. Use sanitized local examples plus explicit synthetic variants; track external acceptance separately.

## Tasks and acceptance

- [ ] P0-B: failing tests for real attempt timestamps, configurable bounded timeout/retry, classified transient TLS EOF vs certificate failure, total deadlines/backoff and shared host pacing. Implement common network options and persist original timing with additive migration. No TLS downgrade.
- [ ] P0-C/P1-E: failing semantic form tests; introduce warnings/capabilities/parser version, structured 2034-2037 vs auxiliary 2102 vs document-only 3152/3153. Keep malformed/identity errors, raw/history and cross-lane ownership. Add locked parser-version replay with default dry-run and no HTTP. Unsupported processApply remains zero HTTP and is not repeatedly auto-selected at the same adapter version.
- [ ] P0-A: scoped compatible effective coverage report including unvisited/queued/active/partial/proved; durable repair table, deterministic dedup, delayed retries with jitter, cumulative limits and manual-review terminal state. Add fresh-budget continuation only on measurable progress, avoid starvation and user-stop resurrection. CLI `repair-gaps` dry-run by default, no implicit all-database scope.
- [ ] P1-D: opt-in reconciliation schedule scoped to configured collection range with quota; same-count/different-ID and content drift checks, immutable historical intervals, honest report with package-vs-lane counts. KQLCNT_FULL remains separate/unverified.
- [ ] Run targeted RED/GREEN, portable full collector regression and actual Linux container checks. Review rollback/migrations and preserve the running Windows application/data root. Report exact verified/partial/blocked acceptance evidence and branch diff.

## Evidence ledger

- Initial read-only checkout verified clean; HEAD 2208dbe changes only docs relative to package c76d015.
- Local root read only: 26 HSMT PARSE_ERROR tasks, examples IB2600599323 (2034+2102), IB2600599002 (2102+2035). These are distinct from the supplied VPS snapshot of 70 tasks.
- No raw corpus or production database will be committed. Sanitized fixtures must retain structural keys/types and have provenance explicitly describing transformations.

P0-B progress: seven regression tests first failed, then passed; request heartbeat test also RED/GREEN. Related suite 61 passed. Connect defaults 15s/configurable 15-20, retry defaults 4/max 5 with jitter, certificate/unclassified TLS fail closed, classified EOF retries, absolute caller deadline and deadline-aware host lease, original attempt timing plus sidecar telemetry. Auto deep shares its request cap across retries. New sidecar migration intentionally leaves frozen table arity intact for binary rollback. Remaining full Linux/aggregate regression follows after parser/repair integration.

Raw check found existing successful forms BD.DT.02.1957 and 1960; retain them alongside the new registry. Local auxiliary 2102 fileTech has fileId/fileName strings; sharedFiles is a string list, not objects. Do not assume shared entries contain a verified download URL.

Docker was initially stopped. Starting it for isolated Linux testing auto-started two old unrelated technical-worker containers; both were stopped cooperatively to avoid unrelated work. Only the new test image/container is used for this task. No SSH config exists locally; production corpus is still unavailable.
