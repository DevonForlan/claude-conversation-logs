# MANUAL_ACTIONS

1. Run `scripts/inventory.ps1` once in an Administrator PowerShell (BitLocker, Windows features, services were not readable).
2. Decide the backup Destination (BitLocker To Go SSD or approved file server, >= ~82 GB free). Nothing runs for real until then.
3. Before the real backup, close apps that lock files (Docker Desktop, VS Code, Obsidian, MongoDB tools).
4. On the new PC: let IT enroll the machine first; sign in to M365, Teams, Chrome/Edge, GitHub, Docker, Claude Code, Codex.
5. Re-install/activate licensed software; re-create VPN profile through the company process; request a new company certificate.
6. Review scheduled-task XMLs (paths and accounts) before `restore -RestoreTasks`.
