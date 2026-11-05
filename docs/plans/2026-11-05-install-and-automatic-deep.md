# Local installation and automatic completion

Owner instruction: finish, reinstall on the owner's Windows machine, and enable full data updates while preserving collection in progress. This supersedes the earlier no-install/no-broad-backfill restriction for this machine only. No Git push, merge, remote deploy or source-access bypass.

1. Verify automatic CGTTRG selection/checkpoints, bounded jobs, pause semantics and queue consolidation against isolated warehouses.
2. Run regression, compile both binaries and produce a source-hashed installer.
3. Cooperatively pause and stop the old worker; wait for commits and snapshot publication. Copy the stopped data root to a separate backup.
4. Install; use the locked collection-setup maintenance action to preserve/archive the two exact duplicate queue documents and replace them with six-family weekly coverage from 2022-01-01 through the installation day. Existing coverage proofs determine missing work.
5. Enable bounded automatic public deep batches between index children and while idle. Respect pause, global pacing >=3 seconds and access circuit breakers. Only verified CGTTRG lanes are enabled.
6. Start installed app and worker. Observe real main-warehouse writes, raw/hash/provenance and app-serving reads. Record counts and state; never equate launching or tests with complete historical coverage.

File downloads, KQLCNT_FULL, BOQ and non-CGTTRG deep lanes remain unverified. Automatic deep refresh follows listing changes or the existing 30-day capture expiry; this does not promise daily refresh or full source coverage.

Completed local installation 2026-11-06: 690 tests passed/1 opt-in scheduler test skipped; isolated compiled automatic 9 requests and zero-request rerun verified; stopped-root backup verified; installer exit 0 and installed hashes matched. Controlled maintenance preserved 286047 records/402 proofs and immutable old queue hashes. Main installed worker wrote 30 successful captures, 3 lots, 54 goods, 10 document references and 10 relations; app backend read all of them from the published snapshot. The active full-update job and GUI were left running. Full historical crawl remains in progress. See docs/INSTALL-DEEP-AUTO-20261106.md.
