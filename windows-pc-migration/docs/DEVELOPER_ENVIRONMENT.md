# DEVELOPER_ENVIRONMENT (2026-11-10)

| Tool | Version | Reinstall |
|---|---|---|
| Git | 2.54.0 | winget Git.Git (no global .gitconfig exists -> set user.name/email on new PC) |
| GitHub CLI | 2.96.0 | winget GitHub.cli; `gh auth login` (token in Credential Manager keyring) |
| VS Code | 1.141 (user install) | winget Microsoft.VisualStudioCode; settings.json + keybindings.json + snippets are in the package |
| Python | 3.13.16 (+3.12.10 legacy), pip 26.2.1, 31 pip packages | winget Python.Python.3.13; `pip install -r pip_freeze.txt` (raw output, local) |
| Node / npm | v24.18.0 / 11.16.0 | winget OpenJS.NodeJS.LTS; globals: `@anthropic-ai/claude-code`, `@openai/codex`, `ccusage` |
| Claude Code / Codex | 2.1.321 / 0.166.0 | `npm i -g @anthropic-ai/claude-code @openai/codex ccusage`; `~/.claude` hooks+settings are in the package; re-login required |
| Docker Desktop | 29.5.3 | winget Docker.DockerDesktop (no images/volumes to move) |
| WSL | 2.7.8.0, no distro | `wsl --install` only if needed |
| Other winget apps | 7zip, Adobe Reader, DBeaver, MobaXterm, MongoDB Compass, Obsidian, PotPlayer, CopyTrans, Teams, Outlook | `inventory/winget_export_curated.json` (19 ids). `winget export` fails on this PC (zh-TW unmappable character), so the list is hand-built from `winget list -s winget`. |

VS Code extensions (7): anthropic.claude-code, ms-azuretools.vscode-containers, ms-python.debugpy, ms-python.python, ms-python.vscode-pylance, ms-python.vscode-python-envs, ms-vscode-remote.remote-containers.

Manual / not winget: LINE, FortiClient VPN (company), CrowdStrike, WebComm OETH, Lenovo tools, Microsoft 365 Apps (company licence), Septerra Core (GOG), OpenSSL Light.
