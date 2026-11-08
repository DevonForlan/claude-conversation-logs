# MIGRATION_PLAN

Order: Inventory -> Backup -> Restore script -> Verify. Entry point: `scripts/migration.ps1 <backup|restore|verify|rollback>`.

| Category | Items | How |
|---|---|---|
| Fully automatic | user folders (Desktop, Documents, Downloads, Pictures, Videos, Music), winget packages (33), VS Code settings + extensions, pip/npm lists, ODBC and certificate metadata, Claude settings, SHA-256 manifest | `backup` / `restore` |
| Partially automatic | scheduled tasks (XML exported; restore only with `-RestoreTasks` after reviewing paths/accounts), Python/Node runtimes + venvs (rebuild), Docker (install only) | script + manual review |
| Needs login / MFA | Microsoft 365 / Teams / Outlook, Chrome and Edge sync, GitHub CLI, Docker, Claude Code, Codex, LINE, Obsidian sync | user |
| Needs license re-activation | Microsoft 365 apps, Adobe Acrobat (if licensed), PotPlayer/others if licensed | user |
| New PC native / company managed | drivers, Lenovo tools, CrowdStrike, WithSecure, WebComm OETH, FortiClient VPN profile, BitLocker, MDM/EDR, domain join, company certificate | IT / company enrollment |
| Do not migrate | machine identity, drivers, BitLocker state, registry hives, browser profiles (DPAPI), private keys unless encrypted package + explicit flag | excluded |

Steps: (1) run inventory as Administrator to fill gaps; (2) provide Destination (encrypted external SSD or file server, >= ~82 GB free); (3) `backup -DryRun`, then `backup`; (4) `verify`; (5) on the new PC: `restore -DryRun`, `restore`, `verify`; (6) `rollback` removes only files restore created.
Backup package never goes to GitHub.
