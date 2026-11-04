# RC3 UI and data-quality completion

Goal: replace the existing desktop presentation, make full historical collection/coverage accessible, verify stored source-field preservation, then uninstall/reinstall the verified build without deleting real data.

Architecture: retain Qt Quick, the worker queue, warehouse and disposable serving snapshot. New presentation components and read-only diagnostics use existing contracts. No change to canonical identities or source filtering rules.

## Tasks
1. Add source-field and quality diagnostics (desktop/data_quality.py); test unknown/null/array preservation, raw hash failures and synthetic detection. Expose complete stored original fields, with normalization kept separate.
2. Add coverage_range payload and renderer; test 2022-to-present bounds and partial/proven/monthly/day aggregation. Keep the canonical resolver as authority.
3. Replace collection page and desktop shell; share native controls, typography and spacing. Default long range and six families, collapse budgets, show worker/job results and strong synthetic warning. Preserve object names and existing actions.
4. Rework search as a table, record detail as readable source fields plus history, and apply consistent controls across the ten screens. No deletion of existing functions.
5. Run fresh regression, native UI/DPI screenshots, full real-root integrity/hash/field audit; preserve missing source values as unknown.
6. Secret scan, local checkpoint, build GUI/worker/installer, verify hashes, backup root, uninstall/reinstall, compare root state and installed manifests, open only the real-root GUI and close acceptance sessions.

## Review focus
- Synthetic data must be visibly quarantined and cannot silently become the selected live root.
- Long date ranges and month drilldown must include the last VN day without claiming future completeness.
- Null/zero/arrays/ambiguous currencies and document fields cannot be silently invented.
- All navigation, scrolling, sort and pagination remain keyboard accessible.
- Installer must preserve the entire Data Root and start the final binary on the real root.

No live canary or new large harvest is required for the redesign/audit. Existing 41/50-request evidence is reused; any additional external collection remains bounded by owner authorization.
