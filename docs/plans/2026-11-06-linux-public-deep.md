# Linux public deep collection implementation plan

> For agentic workers: execute inline with executing-plans; verify before completion.

Goal: ship Ubuntu 22.04/24.04 x86_64 packages containing the tested public CGTTRG collector and automatic completion, then push the completed source and verify GitHub build artifacts.

Architecture: preserve the existing collector-only Linux runtime, Unix control socket, single writer, systemd service and no-serving behavior. Port the Windows-tested shared deep path; add explicit CLI configuration and offline packaged acceptance. Do not touch the running Windows installation/data root.

Spec: owner requests this Linux counterpart after the completed Windows deep task and explicitly authorizes GitHub push. Existing docs/linux-collector.md defines Linux operation; docs/DEEP-PUBLIC-20261105.md defines source contracts and limits once ported.

Constraints: Ubuntu 22.04/24.04 amd64; no Qt/desktop/serving in package; host pacing >=3s; <=30 deep requests per batch; preserve raw/history/checkpoints; references are not downloaded files; only verified CGTTRG lanes. No server access/deployment requested. No new access bypass or speculative endpoints.

Review focus: existing v1 warehouse upgrade; synthetic roots must not call live network; pause and SIGTERM preserve work; headless binary includes migration v2 but excludes desktop imports; source manifest covers all runtime inputs.

- [x] Commit the verified Windows implementation, cherry-pick to existing isolated Linux worktree, preserve Linux runtime differences. Verify portable baseline and shared regressions.
- [x] Write CLI/normal-runtime tests that fail without deep support; add deep-config/deep-status, wire the live factory, isolated offline deep self-test and fail-closed synthetic-root handling. Test normal queue, bounded execution, idempotency and no serving/Qt.
- [x] Bump packages to RC3-3, include v2 migration, extend offline CI and installer smoke to deep self-test and configuration. Update operational/upgrade instructions.
- [x] Run lint/secret scan/portable regression, review diffs, commit and push authorized branches. CI builds on Linux; inspect Ubuntu 22.04/24.04 install/upgrade/remove and systemd results. Fix any failures and rerun affected gates.
- [x] Download final artifacts, verify hashes/source commit, write qualified release evidence, push documentation and hand over install commands and GitHub artifact link.

Ruling: use the existing Linux worktree; no new platform decisions need user clarification. Build through the existing GitHub Actions pipeline because local Docker daemon is stopped. Push is already authorized; no additional approval pause.

Local evidence before push: baseline 23 passed/1 Linux-specific skip; new CLI tests observed failing before implementation, then passed. Portable CI selection on Windows: 465 passed/5 Linux-specific skips in 63.85s with three external saved-payload contracts enabled. Ruff, secret scan and diff checks pass. Author self-review; no callable subagent reviewer available. Shared Windows implementation is 2a47b16, ported as 014555f; Linux collector-only switches and shutdown requeue retained.

Final: source c76d015; CI 42080908185 SUCCESS with Linux 466 passed/4 explained skips and both Ubuntu install/systemd jobs PASS. Downloaded artifact 13440368320; both package hashes, 113 source inputs and 692 standalone files verified. Deb 22424976 bytes, tar 29524175 bytes, runtime files 76019238 bytes. No Qt/serving, no live Linode verification or server deployment. Qualified evidence in docs/releases/linux-rc3-3-verification.json and LINUX-READINESS.md. Documentation-only followup commit uses [skip ci]; package source remains c76d015.
