# OLD_PC_READY_FOR_CUTOVER checklist

Status 2026-11-10: **OLD_PC_READY_FOR_CUTOVER = PENDING two Owner items below** (all automatable pre-cutover work is done). Final delta NOT run, new-PC restore NOT started.

Pre-cutover (old PC, D: not needed)
- [x] Elevated inventory (BitLocker C:, features, services, drivers, cert metadata, corporate-software presence)
- [x] Software/dev inventory, curated winget list, VS Code extensions, npm globals
- [x] Sensitive-data strategy; re-login / re-enrollment checklist
- [x] CC -> GitHub sync healthy; terminal-flash fix: no git.exe console seen since fix
- [ ] Owner: classify the .pfx / .pem / .key files (A/B/C); decide BitLocker To Go on D:
- [ ] Owner: ask IT about new-PC enrollment, VPN profile/MFA, OETH licence, machine certificate (expires 2026-12-30)
- [ ] D: BitLocker status re-check (needs D: connected; do together with cutover step 2)

Cutover day (needs D: connected)
1. OLD_PC_READY -> stop active work (close VS Code, Docker, AI CUP jobs / disable AI_CUP tasks)
2. Connect D:; confirm Healthy / NOT dirty (`Get-Volume`, `fsutil dirty query D:`); capture D: BitLocker status
3. Final delta: incremental robocopy, new/changed only, same exclusions, NO /MIR or deletions; refresh app-settings backup
4. Rebuild/update manifest (`-ManifestOnly`, ~1 h)
5. Final verify (expect source drift 0 once work has stopped)
6. Confirm D: Healthy / NOT dirty / no writers
7. Safe remove D:
8. NEW_PC_VERIFY: `02_restore\migration.ps1 verify -Source D:\Windows_Migration_Package`
9. RESTORE_DRYRUN: `restore -DryRun`
10. Elevated RESTORE, then NEW_PC_RELOGIN_CHECKLIST.md
