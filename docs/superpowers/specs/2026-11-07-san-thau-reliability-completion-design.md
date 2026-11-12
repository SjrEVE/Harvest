# Săn Thầu Reliability Completion Design

## Intent

Bring the current Săn Thầu collector and local data store to an evidence backed, repeatable state before expanding live collection. The work must preserve the existing local database, respect the missing only policy, and produce a clear report that distinguishes complete coverage, partial deep parsing, metadata only documents, synthetic package smoke, and live source evidence.

The current source is the linked worktree `D:\Harvest\santhau-linux` on branch `codex/windows-calendar-year-picker`, based at RC3-8 commit `1aeaafa`. The working tree contains the RC3-9 version and raw store changes. Those changes remain part of the work and are not reset.

## Goals

1. Preserve and verify the current local Data Root at `D:\Harvest\SanThauData` before any writer activity.
2. Keep proven complete coverage out of new network work by default.
3. Make deep capture reuse, parser capability, partial forms, and provenance explicit and stable.
4. Rebuild the serving projection safely and prove that it reflects the warehouse snapshot.
5. Verify the current RC3-9 source with targeted regressions and the full suite.
6. Build and smoke test Windows and Linux artifacts from a sealed, reviewable source state.
7. Install the verified Linux artifact on the Linode collector after a server backup and canary.
8. Run a bounded historical backfill from the server using missing-only proofs and a resumable queue.
9. Produce a Vietnamese readiness report with exact counts, timestamps, evidence classes, and known limits.

## Non-goals

- Downloading PDF or other document bytes. Document rows remain metadata and references only during this phase.
- Historical backfill before the data and serving gates pass.
- Enabling reconcile or expiry refresh by default.
- Installing RC3-9 over the current Windows installation, pushing a public release, or deleting/replacing production data.
- Replacing the RC3-4/RC3-8 parser, migration, or provenance architecture with the older checkout.

## Constraints

- Do not reset, clean, or overwrite the dirty RC3-9 worktree.
- Keep `DateField.qml` and `test_date_field_navigation.py` unchanged.
- Keep the local Data Root outside build output and do not mutate it during package builds.
- Use `MISSING_ONLY` as the collection default; refresh, reconcile, and expiry refresh require an explicit choice.
- Treat `FETCHED` as a transport state. Capability and completeness determine whether data is usable.
- Treat the six verified `egp_home` families as the current source boundary. `egp_v2` remains session gated; disabled sources stay disabled.
- No PDF download work is included in implementation or acceptance.
- Linode package installation and historical backfill are explicitly authorized by the current request.

## Selected approach

Use a reliability first sequence:

1. Freeze the current data state and create a verifiable backup/snapshot without recrawling.
2. Close correctness gaps in raw recovery, coverage decisions, deep reuse, parser capability, serving currentness, and worker control.
3. Run red to green targeted regressions, then the complete suite and static gates.
4. Rebuild serving on a safe copy, compare row counts and content digests, and record the result.
5. Build RC3-9 Windows and Linux packages from a clean sealed source manifest and run offline packaged smoke.
6. Only after those gates pass, install the Linux artifact on Linode and run a bounded one-day canary.
7. If the canary is clean, run historical backfill in resumable bounded batches. Keep proven intervals and reusable deep captures out of network work.

This sequence keeps data recovery and release verification independent. It also leaves the live database untouched while code and serving changes are evaluated.

## Design details

### Data safety and audit

- Record the Data Root file sizes, schema version, source counts, raw counts, coverage counts, deep counts, and latest timestamps before changes.
- Create a content-addressed warehouse backup and a manifest containing file hashes, schema version, and source commit.
- Run read-only checks for source record integrity, raw object references, coverage proof references, deep capture references, and serving metadata.
- Do not treat a successful SQLite open as a complete health result; retain row-count, hash, and foreign-key evidence separately.

### Collection and coverage

- Keep the existing scope from 2022-01-01 through 2026-11-06 and six `egp_home` families.
- Resolve work from coverage proofs and interval status before creating requests.
- A complete proof suppresses a new request. A partial or absent proof may create bounded missing work.
- Reconcile and TTL refresh remain disabled unless a later request explicitly enables them.
- Every canary records source, family, interval, request count, attempt outcomes, proof status, and whether the result was live or reused.

### Deep capture and parsing

- Reuse a compatible capture when source record identity, listing hash, lane, parser version, and response provenance match.
- Reuse must not advance freshness or create a new network attempt.
- Keep `DETAIL`, `HSMT`, and `REOFFER` as separate lanes while merging projection evidence by capture ownership.
- Classify HSMT results as `STRUCTURED_ITEMS`, `NOTICE_METADATA`, `UNVERIFIED_FORM`, or another explicit capability. An unverified form is not silently promoted to full structured data.
- Preserve raw payloads and parser warnings so a later parser upgrade can reprocess them offline.

### Serving projection

- Treat serving as a read projection of warehouse data.
- Copy only the approved snapshot tables and keep audit/provenance reads on a read only warehouse reader.
- Use content digests over all snapshot table columns, including linkage keys, to detect silent state changes and cross swaps.
- A missing digest or manifest entry causes a rebuild. A no-op refresh must not rebuild when content is unchanged.
- Record the before/after row counts, digest values, `integrity_check`/quick check output, and source commit.

### Release verification

- Bump all RC3-9 version surfaces consistently.
- Run targeted regressions for raw recovery, missing only policy, deep reuse, parser capability, serving digest/no-op, shutdown/resume, and UI/backend read paths.
- Run the full suite, `ruff`, secret gate, and `git diff --check` on the final source state.
- Build Windows and Linux sequentially. Generate source and artifact manifests with working-tree status and hashes.
- Run offline packaged smoke twice where supported and confirm the second run adds zero records. No network client is constructed in this smoke profile.

### Live canary after release gates

- Select one missing interval and one family at a time.
- Stop on refusal, CAPTCHA, repeated timeout, malformed response, or proof inconsistency.
- Confirm the canary creates only missing work, does not recrawl a complete proof, and updates raw, observations, and coverage atomically.
- Keep the worker stopped after the canary until the report is reviewed.

### Server installation and historical backfill

- Confirm the target host, service unit, data root, current package, and free disk before stopping anything.
- Create a timestamped server backup of the warehouse and jobs. Do not delete or overwrite the existing migrated backup.
- Upload only the verified Linux package and checksums; do not upload the local serving projection or raw store.
- Stop the collector service, verify the process exited, install the package, verify the version and manifest, and run a no-network quick check.
- Start the service with the existing scope, run one bounded one-day canary, and stop on refusal, CAPTCHA, repeated timeout, malformed response, proof inconsistency, or disk/resource guard breach.
- Configure the historical range only after the canary passes. Use `MISSING_ONLY`, `reconcile_enabled=false`, explicit request quotas, resumable batches, and checkpoint evidence.
- Keep a live progress ledger with batch id, interval, family, request/attempt outcome, proof status, and remaining scope. The backfill must resume without recrawling complete proofs.
- Rebuild serving only through the installed service's normal writer path and compare warehouse/serving health after each checkpoint window.

## Acceptance evidence

- The backup manifest verifies before any writer or build action.
- No source record, raw object, coverage proof, or deep capture disappears across the verification steps.
- Targeted tests pass after each correctness fix; the full suite passes on the final source state.
- Serving row counts and content digests match the selected warehouse snapshot, with no audit table accidentally required by the desktop reader.
- RC3-9 Windows and Linux manifests are sealed and packaged smoke passes twice with zero rerun additions.
- The live canary reports its exact request/attempt/proof result and confirms missing only behavior.
- The final report explicitly lists the 528 metadata only documents and states that no PDF bytes were downloaded.

## Review focus

1. A complete proof or reusable deep capture must produce zero new network requests.
2. A malformed or unregistered HSMT form must remain `UNVERIFIED_FORM`/partial and must not delete good projections.
3. Changing one deep lane must preserve evidence from another lane.
4. A serving no-op guard must detect content flips and linkage swaps, while avoiding a rebuild when content is unchanged.
5. Worker shutdown must preserve waiting work and must not create false cancelled jobs.

