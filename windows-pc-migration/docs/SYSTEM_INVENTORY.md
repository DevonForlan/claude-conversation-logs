# SYSTEM_INVENTORY (old PC) - updated 2026-11-10 (elevated run, is_admin=True)

Raw output is LOCAL only (`Desktop\個人\搬遷\inventory_out`); redacted summary: `inventory/summary_elevated_20261110.json`. Script: `scripts/inventory_elevated.ps1` (read-only).

| Item | Value |
|---|---|
| OS | Windows 11 Pro build 26300; LENOVO 21C1S00Q00; i7-1260P; 31.7 GB RAM |
| Identity | **Domain joined (webcomm)**, not Azure AD / workplace joined |
| BitLocker C: | **On**, FullyEncrypted 100%, XtsAes128, protectors TPM + RecoveryPassword (keys not read) |
| BitLocker D: | not captured (D: not connected); earlier check: NOT protected |
| TPM / Secure Boot | present, ready, enabled / Secure Boot on |
| Windows features | 28 enabled, 56 capabilities installed (list in raw output) |
| Services | 321 total, 163 running, 32 with non-Windows path |
| Corporate security/VPN services | CrowdStrike Falcon, FortiClient VPN (FA_Scheduler), Defender, F-Secure PSB, WebComm OETH - **device-managed, do not migrate or touch** |
| Machine certificate | 1 in LocalMachine\My, issued by internal CA (webcomm-N2-02-CA), has private key, **expires 2026-12-30** - device-bound, IT must re-issue for the new PC |
| Drivers | 262 PnP signed drivers (not migrated; new PC uses its own) |
| Non-Microsoft scheduled tasks | 51 (Adobe, Edge/Google updaters, OneDrive, Lenovo, F-Secure = vendor-managed; **5 AI_CUP_* tasks = user tasks, backed up in package**) |
| Hosts file | 3 non-comment lines (review manually on new PC if needed) |
| Env vars | User 4, Machine 17, no secret-like names. `user_license_dir` = C:\Program Files\WebComm OETH (device-bound) |
| Docker | Docker Desktop 29.5.3: 0 images / 0 containers / 0 volumes (nothing to migrate) |
| WSL | 2.7.8.0 installed, no distributions |
| Not present | PowerShell profiles, ~/.gitconfig, ~/.ssh, .npmrc, pip.ini, .wslconfig, Windows Terminal settings |
