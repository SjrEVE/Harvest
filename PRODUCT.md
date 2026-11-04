# Săn Thầu — Kho dữ liệu

<!-- impeccable:product-schema 1 -->

## Platform
Windows desktop, Python 3.12 and Qt Quick/PySide6. Existing application, not a website.

## Users and purpose
The owner collects Vietnamese public procurement records, checks missing periods, searches records and inspects provenance. Daily use requires readable dense tables and clearly distinguishable real and synthetic data.

## Confirmed constraints
- Continue RC3 in this workspace, preserve the warehouse, raw objects, versions and audit history.
- Six main E-GP families; source details/documents are separate capabilities and must not be claimed verified without evidence.
- Vietnamese product language; keyboard navigation and Windows DPI 100/125/150.
- No new server/database, no push or deployment. Uninstall/reinstall is authorized after verification; preserve the real Data Root.
- The owner delegated implementation and asked to proceed without milestone confirmations.
- Expand date controls and coverage beyond the previous seven-day view.

## Evidence
Existing installed app, source tests, real Data Root D:/Harvest/SanThauData, bounded source canaries in _evidence/rc3. Synthetic acceptance data must never be presented as live business data.

## Implementation choices
Use a light, dense desktop workspace with a dark navigation rail, readable Segoe UI text and teal action accents. Default collection and coverage to 01/01/2022 through yesterday. These are implementation choices, not additional owner requirements.
