# FINAL CUTOVER REPORT (old PC -> Transcend package) - 2026-11-22

**Conclusion: SAFE TO RESTORE** (package integrity verified). Old PC becomes Frozen Source from now on; see MANUAL ACTIONs.

## Transcend
D: = Transcend StoreJet (USB, exFAT, 1863 GB, label "Transcend", Disk 1). Healthy / Online / NOT dirty, 1,467 GB free, no robocopy/migration/python process writing. Package, logs, manifest and verify logs present. D: BitLocker: **Off** (confirmed with elevated run).

## Final Delta Inventory (vs 2026-11-10 baseline)
Only version/vendor churn: Claude Code 2.1.321 -> 2.1.330, VS Code ext updates (claude-code, python-envs, remote-containers), pip +PyYAML, Chrome/Edge/Acrobat/CrowdStrike/M365 version bumps, Google updater tasks, 2 Lenovo task GUIDs, feature `Camera-CCX-Deployment` -1. No new/removed apps, no .gitconfig/.ssh/PS profile, git repos unchanged in scope. BitLocker C: On (unchanged), D: Off. Report: `00_inventory\final_delta_20261121\` (in package).

## New migration scope
Added step `Backup-ExtraConfigs` (migration.ps1): MobaXterm.ini (no credential sections), Obsidian obsidian.json, Edge Bookmarks, Claude Code settings/plugins lists + hooks/skills, Codex config.toml/AGENTS.md/rules/skills, VS Code snippets. Never copied: tokens, `.credentials.json`, `auth.json`, cookies, `.claude.json`. Obsidian vault is under Documents (already in scope).

## Final Delta numbers
- Pass 1 (11-21 09:54-10:19): Desktop 10,899 copied (2.86 GB; new+updated), Documents 1, Downloads small; failed 0.
- Pass 2 (11-22): Desktop 201 files (171 new, 30 updated, 53.9 MB), failed 0; then Outlook.pst updated (1). No deletions (no /MIR); 1,076 extra files kept in package.
- Final package: **361,561 files, ~79.8 GB logical**.

## Checksum / verification
- Full formal verify (11-21 19:26-21:39): package ok=361,390 / **mismatch 0 / missing 0 / hash failures 0**; files in package not in manifest 0.
- Source drift reported by that run (161 Desktop files) = live changes after the backup (AI CUP repo .git 136, logs, .claude conversation logs). Resolved by pass 2; re-check by robocopy /L: Desktop 0 to copy, Downloads 0, Documents 1 (Outlook.pst, copied).
- Incremental manifest (hash only new/changed): 201 + 1 entries re-hashed from D: and compared with SOURCE hashes: 0 mismatch; random 3,000-file re-hash: 0 mismatch. Manifest entries 361,561 (previous kept as `.prev_*`).
- Note: the incremental part was verified by targeted hashing, not by another 2-hour full run.

## WARNING
- D: is **unencrypted** and holds company data (BitLocker To Go recommended / keep physically secured). D: is also used by other (Mac) data; do not delete the package.
- Machine certificate (webcomm-N2-02-CA) expires 2026-12-30; device-bound, not migrated.
- AI CUP scheduled tasks and other tools kept writing to Desktop during cutover (AI CUP `.git`, logs). After you stop them, any later change is not in the package.
- Outlook.pst / live files may change if Outlook is open.

## MANUAL ACTION
1. Close Outlook, VS Code, Claude Code, Docker and disable AI_CUP scheduled tasks; if anything is changed after 2026-11-22 09:20, run one more small delta (copy 2 min + incremental manifest).
2. Classify 15 .pfx / 8 .pem/.key (SENSITIVE_DATA_STRATEGY.md); unclassified = not migrated.
3. IT: new-PC enrollment, VPN/MFA, OETH licence, machine certificate.
4. Safely remove D: via Windows.

## Not done / open
Restore, rollback and winget import never exercised on a real package (new PC step). `winget export` fails on this PC (zh-TW char): curated list `inventory/winget_export_curated.json` is used.

Next: D: -> new PC -> `02_restore\migration.ps1 verify -Source D:\Windows_Migration_Package` -> `restore -DryRun` -> elevated `restore` -> post-restore validation.
