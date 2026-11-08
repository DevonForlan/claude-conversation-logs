# DEVELOPER_ENVIRONMENT

| Tool | State | Strategy |
|---|---|---|
| Git 2.54 | no ~/.gitconfig | reinstall via winget; reconfigure user name/email |
| VS Code 1.141 | 7 extensions, user settings present | settings + `code --install-extension` list (auto) |
| Python 3.13.16 and 3.12.10 | 31 pip packages (3.13) | reinstall runtimes via winget/installer; recreate venvs from requirements/lock files; do not copy interpreters |
| Node 24 LTS | 3 global npm packages | reinstall; `npm i -g` from list; projects rebuild from lockfiles |
| Docker Desktop 29.5 | 0 images / 0 containers / 0 volumes | nothing to migrate; install and sign in; compose projects come with project folders |
| WSL 2.7.8 | no Linux distribution installed | install WSL feature only if needed; nothing to export |
| Claude Code 2.1.x, Codex CLI 0.166 | installed (npm) | reinstall; re-login; settings.json / CLAUDE.md copied |
| PowerShell | no profiles/user modules | none |
| Windows Terminal | no settings file found | none |
| SSH | no ~/.ssh directory | none (re-check on the new PC side if keys are expected) |
| Others | MongoDB Compass, DBeaver, MobaXterm, Obsidian, GitHub CLI | winget (all in export); sessions/bookmarks re-created; Obsidian vault is data (Desktop copy) |
