# Windows PC Migration - status (session 30e5b156-8771-498c-a5a7-ff50b6e35047)

Updated: 2026-11-09T21:26:35+08:00
Issue: DevonForlan/claude-conversation-logs#1

## Current phase: BACKUP_VERIFIED (package integrity OK) - new-PC restore NOT started (per Owner directive)

## 1. D: drive
- Transcend (StoreJet USB), exFAT, 1863 GB total, ~1,585 GB free, allocation unit 512 KB (cause of the huge small-file slack/slowness).
- BitLocker: NOT protected -> `-IncludeSensitive` never used; sensitive files stay excluded.
- 2026-11-09 08:27 the drive dropped mid-run (dirty volume bitmap). Admin `chkdsk D: /F /X` (16:10-16:20): "Windows has made corrections", EXIT=1. Afterwards: HealthStatus Healthy, OperationalStatus OK, `fsutil dirty` = NOT dirty, 0 KB bad sectors.
- `D:\FOUND.000\FILE0000.CHK` (11,010,048 bytes) = orphan fragment from the interrupted write; matches no package file; left untouched.

## 2. Elevated inventory
- NOT done: BitLocker (C:/D:), Windows Features, Services still missing (shell is not elevated). The admin command remains posted in Issue #1.

## 3. Package
- `D:\Windows_Migration_Package` (00_inventory, 01_backup, 02_restore, 03_verify, 04_checksums, 05_logs).

## 4. Size / files
- 350,256 files, 76.89 GB logical (physical use on D: is far higher because of 512 KB clusters).

## 5. Checksums / verify (formal verify, 2026-11-09 18:44 -> 21:03)
- SHA-256 manifest: 350,256 entries, written atomically; FILELIST.tsv (size+mtime) alongside.
- Package vs manifest: ok=350,256 mismatch=0 missing=0; files in package not in manifest = 0 (README/backup_meta exempt).
- Source-vs-package (C: Desktop): 347,468 checked; 1,144 source files not in package + 82 differ. Analysed: ALL are changes made on live projects after the snapshot (1,134 created after robocopy ended 17:03, 10 created 16:58 during the run; the 82 modified after backup start). Concentrated in AI CUP repo (1,202), 搬遷 (14), mongodb-dba-trainer (10). No file that existed at backup time is missing.
- verify exit reports errors=1 only because of that drift (expected for a snapshot of a live PC).

## 6. Exclusions / unresolved
- Excluded: .env*, *.pem/*.ppk/*.pfx/*.p12/*.key, id_*, credentials*, *.kdbx; node_modules/.venv/venv/__pycache__/.pytest_cache/.cache/site-packages; directories literally named browser_profile / browser_profiles (cookies, sessions, caches; login state is rebuilt on the new PC).
- Earlier robocopy rc=9 = 17 files, ALL browser-profile temp files (vanished / locked) - now excluded.
- Unresolved: elevated inventory gaps; D: is unencrypted and holds company work files (enable BitLocker To Go or keep physically secured); real restore / rollback / winget import never run on a real package; package is a point-in-time snapshot (needs a final delta pass before the old PC is retired).

## 7. Old PC migration-ready?
- Package integrity: YES (all hashes verified). Overall: CONDITIONAL - run a final delta pass immediately before cut-over, and close the unresolved items above.

## 8. Next step for the new PC (do not run until Owner decides)
1. Connect D:, run `D:\Windows_Migration_Package\02_restore\migration.ps1 verify -Source D:\Windows_Migration_Package` (read-only).
2. `restore -Source ... -DryRun`, review, then elevated `restore` (never touches MDM/EDR, BitLocker, drivers, domain, private-key certs).
3. Re-login to browsers / tools; re-create excluded secrets from their owners.

## Root cause of the 2026-11-08 -> 11-09 delays
- 512 KB exFAT clusters + 342k small files made copying take ~18 h; per-file `Get-FileHash` (single thread, no long-path support) made the manifest slow and produced 60 false `hash failed` (57 destination paths > 260 chars).
- Fixes in migration.ps1: `\\?\` long paths, 12-thread SHA-256 (full integrity kept; ~1 h for 350k files), browser_profile(s) exclusion limited to those directory names, enumeration errors retried and fatal (never silently truncated manifest), atomic manifest write, `-ManifestOnly`, resilient logging, stronger verify (manifest completeness, source-vs-package).
