<#
.SYNOPSIS  Phase 1 READ-ONLY inventory of the old Windows PC (Issue #1). Changes nothing on the system.
.PARAMETER OutDir   Where raw local results go (NOT for GitHub). Default: $env:TEMP\win_inventory
.PARAMETER Summary  Writes a redacted summary.json (counts/metadata only) next to the raw output.
Never reads private key contents, passwords, tokens, cookies or file contents. No `exit` used.
#>
param([string]$OutDir = (Join-Path $env:TEMP 'win_inventory'), [int]$ScanDepth = 6)
$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log = Join-Path $OutDir 'inventory.log'
$sum = [ordered]@{ generated = (Get-Date -Format o); host_redacted = $true; errors = @() }
function Log($m) { ("{0} {1}" -f (Get-Date -Format s), $m) | Add-Content $log -Encoding UTF8 }
function Step($name, [scriptblock]$sb) {
  Log "START $name"
  try { & $sb } catch { $sum.errors += "$name : $($_.Exception.Message)"; Log "ERROR $name $($_.Exception.Message)" }
  Log "END $name"
}
function Save($name, $obj) { $obj | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $OutDir "$name.json") -Encoding UTF8 }
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$sum.is_admin = $isAdmin

Step 'system' {
  $os = Get-CimInstance Win32_OperatingSystem; $cs = Get-CimInstance Win32_ComputerSystem; $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
  $s = [ordered]@{ os = $os.Caption; version = $os.Version; build = $os.BuildNumber; arch = $os.OSArchitecture
    model = "$($cs.Manufacturer) $($cs.Model)"; cpu = $cpu.Name; ram_gb = [math]::Round($cs.TotalPhysicalMemory/1GB,1); domain_joined = $cs.PartOfDomain }
  $sum.system = $s; Save 'system' $s
}
Step 'disks' {
  $v = Get-Volume | Where-Object DriveLetter | ForEach-Object { [ordered]@{ drive = $_.DriveLetter; fs = $_.FileSystem; size_gb = [math]::Round($_.Size/1GB,1); free_gb = [math]::Round($_.SizeRemaining/1GB,1); type = "$($_.DriveType)" } }
  $sum.volumes = @($v); Save 'volumes' $v
  Save 'physical_disks' (Get-PhysicalDisk | Select-Object FriendlyName, MediaType, @{n='size_gb';e={[math]::Round($_.Size/1GB)}})
}
Step 'bitlocker_status_only' {   # protection status only; never recovery keys
  $b = @(Get-BitLockerVolume -ErrorAction Stop | ForEach-Object { [ordered]@{ mount = $_.MountPoint; protection = "$($_.ProtectionStatus)"; encryption = "$($_.VolumeStatus)" } })
  $sum.bitlocker = $b; Save 'bitlocker' $b
}
Step 'features_services' {
  $f = @(Get-WindowsOptionalFeature -Online -ErrorAction SilentlyContinue | Where-Object State -eq 'Enabled' | Select-Object -Expand FeatureName)
  $sum.windows_features_enabled = $f.Count; Save 'features' $f
  $svc = Get-Service | Select-Object Name, DisplayName, Status, StartType
  $sum.services = @{ total = $svc.Count; running = @($svc | Where-Object Status -eq 'Running').Count }; Save 'services' $svc
}
Step 'network_drives_odbc' {
  Save 'drive_mappings' (Get-PSDrive -PSProvider FileSystem | Select-Object Name, Root, DisplayRoot)
  $na = Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, LinkSpeed; Save 'net_adapters' $na; $sum.net_adapters = @($na).Count
  $odbc = @(Get-OdbcDsn -ErrorAction SilentlyContinue | Select-Object Name, DsnType, DriverName); Save 'odbc' $odbc; $sum.odbc_dsn = $odbc.Count
}
Step 'certificates_metadata' {
  $c = @(Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue | Select-Object Subject, Issuer, NotAfter, HasPrivateKey, @{n='store';e={$_.PSParentPath -replace '.*::',''}})
  $sum.personal_certs = $c.Count; Save 'certs_metadata' $c
}
Step 'applications' {
  $paths = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
  $apps = Get-ItemProperty $paths -ErrorAction SilentlyContinue | Where-Object DisplayName | Select-Object DisplayName, DisplayVersion, Publisher | Sort-Object DisplayName -Unique
  $apps | Export-Csv (Join-Path $OutDir 'APPLICATION_INVENTORY.csv') -NoTypeInformation -Encoding UTF8
  $sum.applications_registry = @($apps).Count
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget export -o (Join-Path $OutDir 'winget_export.json') --accept-source-agreements 2>&1 | Out-Null
    if (Test-Path (Join-Path $OutDir 'winget_export.json')) {
      $w = Get-Content (Join-Path $OutDir 'winget_export.json') -Raw | ConvertFrom-Json
      $sum.winget_exportable = @($w.Sources.Packages).Count
    }
  } else { $sum.winget = 'not installed' }
}
Step 'dev_tools' {
  $tools = 'git','code','python','py','pip','uv','conda','node','npm','pnpm','yarn','docker','wsl','java','mongosh','claude','codex','pwsh','dotnet','go','rustc'
  $found = [ordered]@{}
  foreach ($t in $tools) {
    $cmd = Get-Command $t -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { $ver = ''; try { $ver = (& $cmd.Source --version 2>&1 | Select-Object -First 1) -as [string] } catch {} ; $found[$t] = [ordered]@{ path_type = $cmd.CommandType; version = $ver } }
  }
  $sum.dev_tools = $found; Save 'dev_tools' $found
  if (Get-Command code -ErrorAction SilentlyContinue) { $ext = @(code --list-extensions 2>$null); $ext | Set-Content (Join-Path $OutDir 'vscode_extensions.txt'); $sum.vscode_extensions = $ext.Count }
  $vs = Join-Path $env:APPDATA 'Code\User\settings.json'; $sum.vscode_settings_present = Test-Path $vs
  if (Get-Command pip -ErrorAction SilentlyContinue) { $sum.pip_packages = @(pip list --format=freeze 2>$null).Count }
  if (Get-Command npm -ErrorAction SilentlyContinue) { $sum.npm_global = @((npm ls -g --depth=0 --parseable 2>$null)).Count - 1 }
}
Step 'git_ssh_shell' {
  $gc = Join-Path $HOME '.gitconfig'; $sum.gitconfig_present = Test-Path $gc
  if ($sum.gitconfig_present) { $sum.gitconfig_has_credential_helper = [bool](Select-String -Path $gc -Pattern 'credential' -Quiet) }
  $ssh = Join-Path $HOME '.ssh'
  if (Test-Path $ssh) {   # file NAMES and sizes only
    $files = Get-ChildItem $ssh -File | Select-Object Name, Length; Save 'ssh_inventory_names_only' $files
    $sum.ssh = [ordered]@{ files = @($files).Count; has_config = Test-Path (Join-Path $ssh 'config'); has_known_hosts = Test-Path (Join-Path $ssh 'known_hosts')
      private_key_like_files = @($files | Where-Object { $_.Name -match '^id_|\.pem$|\.ppk$' -and $_.Name -notmatch '\.pub$' }).Count }
  }
  $sum.powershell_profiles = @($PROFILE.AllUsersAllHosts, $PROFILE.AllUsersCurrentHost, $PROFILE.CurrentUserAllHosts, $PROFILE.CurrentUserCurrentHost | Where-Object { Test-Path $_ }).Count
  $sum.powershell_modules_user = @(Get-Module -ListAvailable | Where-Object Path -like "$HOME*" | Select-Object -Expand Name -Unique).Count
  $wt = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal*\LocalState\settings.json') -ErrorAction SilentlyContinue; $sum.windows_terminal_settings = @($wt).Count
}
Step 'docker_wsl' {
  if (Get-Command wsl -ErrorAction SilentlyContinue) { $d = @(wsl -l -v 2>$null | Where-Object { $_.Trim() }); $sum.wsl_lines = $d.Count; Save 'wsl' $d }
  if (Get-Command docker -ErrorAction SilentlyContinue) {
    $sum.docker = [ordered]@{ images = @(docker images -q 2>$null).Count; containers = @(docker ps -aq 2>$null).Count; volumes = @(docker volume ls -q 2>$null).Count }
  } else { $sum.docker = 'cli not found / daemon not reachable' }
}
Step 'scheduled_tasks' {
  $t = Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' } | Select-Object TaskName, TaskPath, State, @{n='exec';e={($_.Actions | Select-Object -First 1).Execute}}
  Save 'scheduled_tasks_non_ms' $t; $sum.scheduled_tasks_non_microsoft = @($t).Count
}
Step 'user_data_sizes' {
  $targets = 'Desktop','Documents','Downloads','Pictures','Videos','Music','OneDrive'
  $sizes = foreach ($n in $targets) {
    $p = Join-Path $HOME $n
    if (Test-Path $p) { $m = Get-ChildItem $p -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum
      [ordered]@{ folder = $n; files = $m.Count; size_gb = [math]::Round(($m.Sum)/1GB,2) } }
  }
  $sum.user_data = @($sizes); Save 'user_data_sizes' $sizes
}
Step 'project_scan' {
  $skip = 'node_modules','.venv','venv','__pycache__','AppData','.cache','site-packages','.git','$Recycle.Bin','Windows','Program Files','Program Files (x86)','ProgramData'
  $marks = 'package.json','requirements.txt','pyproject.toml','docker-compose.yml','compose.yml','docker-compose.yaml'
  $roots = @($HOME) + @(Get-Volume | Where-Object { $_.DriveLetter -and $_.DriveType -eq 'Fixed' -and "$($_.DriveLetter):\" -ne "$($env:SystemDrive)\" } | ForEach-Object { "$($_.DriveLetter):\" })
  $proj = New-Object System.Collections.ArrayList
  function Walk($dir, $d) {
    if ($d -gt $ScanDepth) { return }
    $kids = Get-ChildItem $dir -Force -ErrorAction SilentlyContinue
    $names = $kids | Select-Object -Expand Name
    $hit = @(); if ($names -contains '.git') { $hit += '.git' }; foreach ($m in $marks) { if ($names -contains $m) { $hit += $m } }
    if ($hit) { [void]$proj.Add([ordered]@{ path = $dir; markers = ($hit -join ',') }) }
    foreach ($k in $kids) { if ($k.PSIsContainer -and ($skip -notcontains $k.Name) -and -not ($k.Attributes -band [IO.FileAttributes]::ReparsePoint)) { Walk $k.FullName ($d + 1) } }
  }
  foreach ($r in $roots) { Walk $r 0 }
  Save 'projects_metadata' $proj; $sum.project_dirs = $proj.Count
  $sum.git_repos = @($proj | Where-Object { $_.markers -like '*.git*' }).Count
}
$sum | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $OutDir 'summary.json') -Encoding UTF8
Log 'DONE'
Write-Output "Inventory finished: $OutDir (errors: $($sum.errors.Count))"
