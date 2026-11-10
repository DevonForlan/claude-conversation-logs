# NEW_PC_RELOGIN_CHECKLIST

Device-bound / IT first (do NOT recreate from the old PC):
- [ ] Domain join / MDM enrollment as directed by IT; BitLocker by policy (escrow recovery key to IT)
- [ ] CrowdStrike Falcon, F-Secure, Defender policy: installed by IT
- [ ] FortiClient VPN: company build + profile from IT; re-bind MFA/token (device-bound)
- [ ] Machine certificate (webcomm-N2-02-CA): request a new one (old expires 2026-12-30)
- [ ] WebComm OETH + licence (`user_license_dir`): ask IT for a new licence
- [ ] Microsoft 365 / Outlook / Teams: company sign-in; re-register MFA if device-bound
- [ ] Lenovo Vantage / drivers: vendor tools for the new hardware

User accounts / tools (after `verify` + `restore -DryRun` + elevated `restore`):
- [ ] Chrome + Edge: sign in, enable sync; re-login sites (cookies not migrated)
- [ ] GitHub: `gh auth login`; git `user.name/user.email` (no .gitconfig existed); credential manager
- [ ] VS Code: sign in, install 7 extensions, check settings/keybindings restored
- [ ] Claude Code and Codex login; verify `~/.claude` hooks (cc_global_log) run and push to GitHub
- [ ] Docker Desktop sign-in; WSL only if needed
- [ ] MongoDB Compass / DBeaver / MobaXterm: re-create connections and credentials
- [ ] Python/Node/npm globals (see DEVELOPER_ENVIRONMENT.md); re-register AI_CUP scheduled tasks from the package, then confirm they stay headless
- [ ] `.env` files from owners; `.pfx/.pem/.key` per SENSITIVE_DATA_STRATEGY.md
- [ ] LINE, Obsidian, PotPlayer, 7-Zip, Adobe Reader, CopyTrans: winget + sign in
