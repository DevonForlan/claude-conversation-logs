<#
.SYNOPSIS  Windows PC migration (Issue #1): backup | restore | verify | rollback.  Source/Destination are parameters.
.EXAMPLE
  .\migration.ps1 backup  -Destination E:\Migration -DryRun
  .\migration.ps1 backup  -Destination E:\Migration
  .\migration.ps1 verify  -Source E:\Migration
  .\migration.ps1 restore -Source E:\Migration -DryRun
  .\migration.ps1 rollback -Source E:\Migration -DryRun
Rules: no `exit`; DryRun changes nothing (only a log under %TEMP%); private keys / .env / credentials are excluded
unless -IncludeSensitive (allowed ONLY when the destination is an encrypted drive); nothing is ever sent to GitHub.
Restore never touches machine identity (MDM/EDR, BitLocker state, drivers, domain/AD, certificates with private keys).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory, Position = 0)][ValidateSet('backup', 'restore', 'verify', 'rollback')][string]$Action,
  [string]$Destination, [string]$Source,
  [switch]$DryRun, [switch]$IncludeSensitive, [switch]$IncludeRebuildable, [switch]$RestoreTasks,
  [string[]]$UserFolders = @('Desktop', 'Documents', 'Downloads', 'Pictures', 'Videos', 'Music'),
  [string]$UserProfileRoot = $env:USERPROFILE, [double]$MinFreeGBMargin = 5
)
$ErrorActionPreference = 'Continue'
$script:Errors = New-Object System.Collections.ArrayList
$script:Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$Pkg = if ($Action -eq 'backup') { $Destination } else { $Source }
$script:LogFile = if ($DryRun -or -not $Pkg) { Join-Path $env:TEMP "migration_$($Action)_$script:Stamp.log" } else { Join-Path $Pkg "05_logs\$($Action)_$script:Stamp.log" }
New-Item -ItemType Directory -Force -Path (Split-Path $script:LogFile) | Out-Null

function Log([string]$m, [string]$lvl = 'INFO') { $l = "{0} [{1}] {2}" -f (Get-Date -Format s), $lvl, $m; Write-Host $l; Add-Content $script:LogFile $l -Encoding UTF8 }
function Fail([string]$m) { [void]$script:Errors.Add($m); Log $m 'ERROR' }
function Test-Admin { ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) }

# ---- exclusions -------------------------------------------------------------------------------------------------
$RebuildDirs = @('node_modules', '.venv', 'venv', '__pycache__', '.pytest_cache', '.cache', 'site-packages')   # rebuilt from lockfiles
$AlwaysDirs = @('$RECYCLE.BIN', 'System Volume Information')
$SensitiveFiles = @('.env', '.env.*', '*.pem', '*.ppk', '*.pfx', '*.p12', '*.key', 'id_rsa', 'id_ed25519', 'id_ecdsa', 'credentials', 'credentials.json', '*.kdbx')
function Get-RoboExclusions {
  $xd = @($AlwaysDirs); if (-not $IncludeRebuildable) { $xd += $RebuildDirs }
  $xf = @(); if (-not $IncludeSensitive) { $xf += $SensitiveFiles }
  return @{ XD = $xd; XF = $xf }
}
function Invoke-Robo([string]$src, [string]$dst, [switch]$ListOnly, [string]$tag) {
  $ex = Get-RoboExclusions
  $ra = @($src, $dst, '/E', '/COPY:DAT', '/DCOPY:DAT', '/R:2', '/W:5', '/XJ', '/NP', '/NFL', '/NDL', '/MT:8', ('/UNILOG+:' + $script:LogFile))
  if ($ex.XD) { $ra += '/XD'; $ra += $ex.XD }
  if ($ex.XF) { $ra += '/XF'; $ra += $ex.XF }
  if ($ListOnly) { $ra += '/L' }
  & robocopy @ra | Out-Null
  $rc = $LASTEXITCODE
  Log "robocopy[$tag] rc=$rc $(if ($rc -ge 8) { 'FAILED (>=8)' } else { 'ok' })"
  if ($rc -ge 8) { Fail "robocopy $tag failed rc=$rc" }
  return $rc
}
function Measure-Tree([string]$p) {   # bytes/files that backup WOULD copy (honours exclusions) via robocopy /L
  $ex = Get-RoboExclusions
  $a = @($p, (Join-Path $env:TEMP 'mig_measure_dummy'), '/E', '/L', '/XJ', '/NP', '/NFL', '/NDL', '/BYTES', '/R:0', '/W:0')
  if ($ex.XD) { $a += '/XD'; $a += $ex.XD }; if ($ex.XF) { $a += '/XF'; $a += $ex.XF }
  $out = & robocopy @a 2>&1 | Out-String
  $files = 0; $bytes = [int64]0; $rows = @()   # locale independent: rows 'label : total copied skipped ...' = Dirs, Files, Bytes
  foreach ($l in ($out -split "`r?`n")) { if ($l -match '^\s*[^\d\s][^:]*:\s+(\d+)\s+(\d+)') { $rows += [int64]$Matches[1] } }
  if ($rows.Count -ge 3) { $files = [int]$rows[1]; $bytes = $rows[2] }
  return [pscustomobject]@{ Files = $files; Bytes = $bytes }
}
function Get-DriveFreeGB([string]$path) {
  $q = Split-Path $path -Qualifier -ErrorAction SilentlyContinue
  if (-not $q) { return $null }
  $d = Get-PSDrive -Name $q.TrimEnd(':') -ErrorAction SilentlyContinue
  if ($d -and $null -ne $d.Free) { return [math]::Round($d.Free / 1GB, 1) } else { return $null }
}
function Test-DestinationSafe([string]$path) {
  if (-not $path) { Fail 'Destination/Source is required (no default is ever guessed)'; return $false }
  if ($path -match '(?i)github|\.git($|\\)') { Fail 'path looks like a git/GitHub location; Migration Package must never go to GitHub'; return $false }
  if ($path.StartsWith($env:SystemDrive + '\', 'OrdinalIgnoreCase') -and $Action -eq 'backup' -and -not $DryRun) {
    Log 'WARNING: destination is on the system drive (a backup on the old PC disk does not protect it)' 'WARN'
  }
  return $true
}
function Test-DriveEncrypted([string]$path) {
  try { $v = Get-BitLockerVolume -MountPoint ((Split-Path $path -Qualifier)) -ErrorAction Stop; return ($v.ProtectionStatus -eq 'On' -or "$($v.ProtectionStatus)" -eq '1') }
  catch { return $null }   # unknown (needs admin)
}
function Write-Manifest([string]$root, [string]$out) {
  $root = (Get-Item -LiteralPath $root).FullName.TrimEnd('')
  $items = Get-ChildItem $root -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notlike "$root\04_checksums*" -and $_.FullName -notlike "$root\05_logs*" }
  $lines = foreach ($f in $items) {
    $h = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256 -ErrorAction SilentlyContinue).Hash
    if ($h) { "{0}  {1}" -f $h, $f.FullName.Substring($root.Length).TrimStart('\') } else { Fail "hash failed: $($f.FullName)" }
  }
  New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
  $lines | Set-Content $out -Encoding UTF8
  return @($lines).Count
}

# ---- sections (small config/state files; data folders handled by robocopy) --------------------------------------
function Backup-AppSettings([string]$root, [bool]$dry) {
  $d = Join-Path $root '01_backup\AppSettings'
  $steps = [ordered]@{}
  $steps['winget_export.json'] = { winget export -o (Join-Path $d 'winget_export.json') --accept-source-agreements | Out-Null }
  $steps['vscode_extensions.txt'] = { code --list-extensions | Set-Content (Join-Path $d 'vscode_extensions.txt') -Encoding UTF8 }
  $steps['vscode_settings'] = { $s = Join-Path $env:APPDATA 'Code\User'; foreach ($n in 'settings.json', 'keybindings.json') { if (Test-Path "$s\$n") { Copy-Item "$s\$n" (Join-Path $d "vscode_$n") -Force } }
                                if (Test-Path "$s\snippets") { Copy-Item "$s\snippets" (Join-Path $d 'vscode_snippets') -Recurse -Force } }
  $steps['pip_freeze.txt'] = { python -m pip freeze | Set-Content (Join-Path $d 'pip_freeze.txt') -Encoding UTF8 }
  $steps['npm_global.txt'] = { npm ls -g --depth=0 | Set-Content (Join-Path $d 'npm_global.txt') -Encoding UTF8 }
  $steps['odbc_dsn.json'] = { Get-OdbcDsn | Select-Object Name, DsnType, DriverName | ConvertTo-Json | Set-Content (Join-Path $d 'odbc_dsn.json') -Encoding UTF8 }
  $steps['certs_metadata.json'] = { Get-ChildItem Cert:\CurrentUser\My | Select-Object Subject, Issuer, NotAfter, HasPrivateKey | ConvertTo-Json | Set-Content (Join-Path $d 'certs_metadata.json') -Encoding UTF8 }
  $steps['claude_settings'] = { $c = Join-Path $HOME '.claude'; if (Test-Path "$c\settings.json") { Copy-Item "$c\settings.json" (Join-Path $d 'claude_settings.json') -Force }; if (Test-Path "$c\CLAUDE.md") { Copy-Item "$c\CLAUDE.md" (Join-Path $d 'claude_CLAUDE.md') -Force } }
  $steps['git_config'] = { if (Test-Path "$HOME\.gitconfig") { Copy-Item "$HOME\.gitconfig" (Join-Path $root '01_backup\Git\.gitconfig') -Force } }
  $steps['powershell_profile'] = { if (Test-Path $PROFILE.CurrentUserAllHosts) { Copy-Item $PROFILE.CurrentUserAllHosts (Join-Path $root '01_backup\PowerShell\profile.ps1') -Force } }
  $steps['windows_terminal'] = { $w = Get-ChildItem "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal*\LocalState\settings.json" -ErrorAction SilentlyContinue | Select-Object -First 1
                                 if ($w) { Copy-Item $w.FullName (Join-Path $root '01_backup\WindowsTerminal\settings.json') -Force } }
  $steps['ssh_config'] = { $s = Join-Path $HOME '.ssh'; if (Test-Path $s) { New-Item -ItemType Directory -Force (Join-Path $root '01_backup\SSH') | Out-Null
      foreach ($n in 'config', 'known_hosts') { if (Test-Path "$s\$n") { Copy-Item "$s\$n" (Join-Path $root "01_backup\SSH\$n") -Force } }
      if ($IncludeSensitive) { Get-ChildItem $s -File | Where-Object { $_.Name -notin 'config', 'known_hosts' } | ForEach-Object { Copy-Item $_.FullName (Join-Path $root '01_backup\SSH') -Force } } } }
  foreach ($k in $steps.Keys) {
    if ($dry) { Log "[dry-run] would run AppSettings step: $k"; continue }
    try { New-Item -ItemType Directory -Force -Path $d, (Join-Path $root '01_backup\Git'), (Join-Path $root '01_backup\PowerShell'), (Join-Path $root '01_backup\WindowsTerminal') | Out-Null
          & $steps[$k]; Log "step ok: $k" } catch { Fail "AppSettings step $k : $($_.Exception.Message)" }
  }
}
function Backup-ScheduledTasks([string]$root, [bool]$dry) {
  $d = Join-Path $root '01_backup\ScheduledTasks'
  $tasks = Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' }
  Log "scheduled tasks (non-Microsoft): $(@($tasks).Count)"
  if ($dry) { return }
  New-Item -ItemType Directory -Force -Path $d | Out-Null
  foreach ($t in $tasks) { try { Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Out-File (Join-Path $d (($t.TaskName -replace '[\\/:*?"<>|]', '_') + '.xml')) -Encoding unicode } catch { Fail "task export $($t.TaskName): $($_.Exception.Message)" } }
}

# ---- actions ----------------------------------------------------------------------------------------------------
function Do-Backup {
  if (-not (Test-DestinationSafe $Destination)) { return }
  if (-not (Test-Admin)) { Log 'not elevated: BitLocker/feature capture limited (backup of user data still works)' 'WARN' }
  if ($IncludeSensitive) {
    $enc = Test-DriveEncrypted $Destination
    if ($enc -ne $true) { Fail '-IncludeSensitive refused: destination encryption is not confirmed (BitLocker To Go required)'; return }
  }
  $need = [int64]0; $plan = @()
  foreach ($f in $UserFolders) {
    $p = Join-Path $UserProfileRoot $f
    if (Test-Path $p) { $m = Measure-Tree $p; $plan += [pscustomobject]@{ Folder = $f; Files = $m.Files; GB = [math]::Round($m.Bytes / 1GB, 2) }; $need += $m.Bytes }
  }
  $plan | Format-Table -AutoSize | Out-String | ForEach-Object { Log $_ }
  $needGB = [math]::Round($need / 1GB, 2)
  Log "estimated copy size: $needGB GB (rebuildable dirs excluded: $(-not $IncludeRebuildable); sensitive excluded: $(-not $IncludeSensitive))"
  $free = Get-DriveFreeGB $Destination
  if ($null -eq $free) { if (-not $DryRun) { Fail "destination drive not found / not ready: $Destination"; return } else { Log "destination drive not available now; free-space precheck SKIPPED in dry-run" 'WARN' } }
  elseif ($free -lt ($needGB + $MinFreeGBMargin)) { Fail "insufficient space: need $needGB GB + $MinFreeGBMargin GB margin, free $free GB"; return }
  else { Log "space precheck PASS: free $free GB >= $needGB GB + margin" }
  if ($DryRun) {
    foreach ($f in $UserFolders) { if (Test-Path (Join-Path $UserProfileRoot $f)) { Log "[dry-run] would robocopy $f -> 01_backup\UserData\$f" } }
    Backup-AppSettings $Destination $true; Backup-ScheduledTasks $Destination $true
    Log '[dry-run] would write 04_checksums\SHA256SUMS.txt and README_MIGRATION.md'; return
  }
  foreach ($n in '00_inventory', '01_backup\UserData', '01_backup\Projects', '01_backup\Docker', '02_restore', '03_verify', '04_checksums', '05_logs') { New-Item -ItemType Directory -Force -Path (Join-Path $Destination $n) | Out-Null }
  foreach ($f in $UserFolders) {
    $p = Join-Path $UserProfileRoot $f
    if (Test-Path $p) { Invoke-Robo $p (Join-Path $Destination "01_backup\UserData\$f") -tag $f | Out-Null }
  }
  Backup-AppSettings $Destination $false; Backup-ScheduledTasks $Destination $false
  Copy-Item $PSCommandPath (Join-Path $Destination '02_restore\migration.ps1') -Force
  $n = Write-Manifest $Destination (Join-Path $Destination '04_checksums\SHA256SUMS.txt'); Log "checksums written: $n files"
  @{ created = (Get-Date -Format o); host = $env:COMPUTERNAME; include_sensitive = [bool]$IncludeSensitive; include_rebuildable = [bool]$IncludeRebuildable; user_folders = $UserFolders; est_gb = $needGB; files_hashed = $n; errors = $script:Errors.Count } |
    ConvertTo-Json | Set-Content (Join-Path $Destination '00_inventory\backup_meta.json') -Encoding UTF8
  "Windows Migration Package`nCreated $(Get-Date -Format s). Restore: .\02_restore\migration.ps1 restore -Source <this folder> [-DryRun]`nVerify: .\02_restore\migration.ps1 verify -Source <this folder>`nContains personal/company data: keep encrypted, never upload to GitHub." | Set-Content (Join-Path $Destination 'README_MIGRATION.md') -Encoding UTF8
}
function Do-Verify {
  if (-not $Source -or -not (Test-Path $Source)) { Fail "Source not found: $Source"; return }
  $sums = Join-Path $Source '04_checksums\SHA256SUMS.txt'
  if (-not (Test-Path $sums)) { Fail 'SHA256SUMS.txt missing'; return }
  $ok = 0; $bad = 0; $missing = 0
  foreach ($l in Get-Content $sums) {
    if ($l -notmatch '^([0-9A-Fa-f]{64})\s{2}(.+)$') { continue }
    $f = Join-Path $Source $Matches[2]
    if (-not (Test-Path -LiteralPath $f)) { $missing++; Fail "missing: $($Matches[2])"; continue }
    if ((Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash -eq $Matches[1]) { $ok++ } else { $bad++; Fail "hash mismatch: $($Matches[2])" }
  }
  Log "package verify: ok=$ok mismatch=$bad missing=$missing"
  # old-vs-new: restored target folders vs package (names + sizes) when they already exist on this machine
  foreach ($f in $UserFolders) {
    $b = Join-Path $Source "01_backup\UserData\$f"; $t = Join-Path $UserProfileRoot $f
    if ((Test-Path $b) -and (Test-Path $t)) {
      $miss = @(); Get-ChildItem $b -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object { $rel = $_.FullName.Substring($b.Length).TrimStart('\'); $x = Join-Path $t $rel; if (-not (Test-Path -LiteralPath $x) -or (Get-Item -LiteralPath $x).Length -ne $_.Length) { $miss += $rel } }
      Log "old-vs-new[$f]: $($miss.Count) differing/missing in target"; if ($miss.Count) { $miss | Select-Object -First 20 | ForEach-Object { Log "  diff: $_" 'WARN' } }
    }
  }
  $apps = Join-Path $Source '01_backup\AppSettings\winget_export.json'
  if (Test-Path $apps) { $want = @((Get-Content $apps -Raw | ConvertFrom-Json).Sources.Packages.PackageIdentifier); $have = (winget list 2>$null | Out-String)
    $absent = @($want | Where-Object { $have -notmatch [regex]::Escape($_) }); Log "winget packages not yet installed here: $($absent.Count) of $($want.Count)" }
}
function Do-Restore {
  if (-not $Source -or -not (Test-Path $Source)) { Fail "Source not found: $Source"; return }
  if (-not (Test-Admin) -and -not $DryRun) { Fail 'restore needs an elevated PowerShell'; return }
  Do-Verify
  if ($script:Errors.Count) { Fail 'package verification failed; restore refused'; return }
  $restoreLog = Join-Path $Source '05_logs\restore_created.txt'
  foreach ($f in $UserFolders) {
    $b = Join-Path $Source "01_backup\UserData\$f"; $t = Join-Path $UserProfileRoot $f
    if (-not (Test-Path $b)) { continue }
    if ($DryRun) { Log "[dry-run] would robocopy $b -> $t (no overwrite of newer files: /XO)"; Invoke-Robo $b $t -ListOnly -tag "restore-$f" | Out-Null; continue }
    # record files that do NOT exist yet (rollback may remove only those)
    Get-ChildItem $b -Recurse -File -Force | ForEach-Object { $rel = $_.FullName.Substring($b.Length).TrimStart('\'); $x = Join-Path $t $rel; if (-not (Test-Path -LiteralPath $x)) { Add-Content $restoreLog $x -Encoding UTF8 } }
    $rr = @($b, $t, '/E', '/XO', '/COPY:DAT', '/DCOPY:DAT', '/R:2', '/W:5', '/NP', '/NFL', '/NDL', '/MT:8', ('/UNILOG+:' + $script:LogFile)); & robocopy @rr | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "restore robocopy $f rc=$LASTEXITCODE" } else { Log "restored $f rc=$LASTEXITCODE" }
  }
  $a = Join-Path $Source '01_backup\AppSettings'
  if (Test-Path "$a\winget_export.json") { if ($DryRun) { Log '[dry-run] would: winget import (skips installed)' } else { winget import -i "$a\winget_export.json" --accept-package-agreements --accept-source-agreements --ignore-unavailable | Out-Null; Log "winget import rc=$LASTEXITCODE" } }
  if (Test-Path "$a\vscode_extensions.txt") { foreach ($e in Get-Content "$a\vscode_extensions.txt") { if ($DryRun) { Log "[dry-run] code --install-extension $e" } else { code --install-extension $e --force | Out-Null } } }
  if (Test-Path "$a\vscode_settings.json") { if ($DryRun) { Log '[dry-run] would copy VS Code settings' } else { New-Item -ItemType Directory -Force "$env:APPDATA\Code\User" | Out-Null; Copy-Item "$a\vscode_settings.json" "$env:APPDATA\Code\User\settings.json" -Force } }
  if (Test-Path (Join-Path $Source '01_backup\Git\.gitconfig')) { if ($DryRun) { Log '[dry-run] would restore .gitconfig (only if absent)' } elseif (-not (Test-Path "$HOME\.gitconfig")) { Copy-Item (Join-Path $Source '01_backup\Git\.gitconfig') "$HOME\.gitconfig" } }
  if ($RestoreTasks) { Get-ChildItem (Join-Path $Source '01_backup\ScheduledTasks\*.xml') -ErrorAction SilentlyContinue | ForEach-Object { if ($DryRun) { Log "[dry-run] would register task $($_.BaseName) (paths/accounts must be reviewed first)" } else { Register-ScheduledTask -Xml (Get-Content $_.FullName -Raw) -TaskName $_.BaseName -Force | Out-Null; Log "task registered: $($_.BaseName)" } } }
  else { Log 'scheduled tasks NOT restored (use -RestoreTasks after reviewing paths/accounts on the new PC)' }
  Log 'skipped by design: drivers, MDM/EDR enrollment, BitLocker state, domain identity, certificates with private keys, browser profiles'
}
function Do-Rollback {
  if (-not $Source) { Fail 'Source required'; return }
  $f = Join-Path $Source '05_logs\restore_created.txt'
  if (-not (Test-Path $f)) { Log 'no restore record found; nothing to roll back'; return }
  $n = 0; foreach ($p in Get-Content $f) { if (Test-Path -LiteralPath $p) { if ($DryRun) { Log "[dry-run] would remove $p" } else { Remove-Item -LiteralPath $p -Force; $n++ } } }
  Log "rollback: removed $n files that restore had created (pre-existing files untouched)"
}

Log "START action=$Action DryRun=$([bool]$DryRun) admin=$(Test-Admin) log=$script:LogFile"
switch ($Action) { 'backup' { Do-Backup } 'restore' { Do-Restore } 'verify' { Do-Verify } 'rollback' { Do-Rollback } }
$summary = "DONE action=$Action errors=$($script:Errors.Count)"
Log $summary
if ($script:Errors.Count) { Log 'ERROR SUMMARY:' 'ERROR'; $script:Errors | ForEach-Object { Log "  - $_" 'ERROR' } }
[pscustomobject]@{ Action = $Action; DryRun = [bool]$DryRun; Errors = $script:Errors.Count; Log = $script:LogFile }
