<#
.SYNOPSIS  READ-ONLY elevated + dev-environment inventory of the old PC (Issue #1, pre-cutover). Changes nothing.
Raw output stays LOCAL ($OutDir); only redacted counts go to GitHub. Never reads key/cookie/password contents.
Admin check uses `whoami /groups` (WindowsPrincipal.IsInRole throws on this machine: domain trust failure).
#>
param([string]$OutDir = 'C:\Users\devon.tseng\Desktop\個人\搬遷\inventory_out')
$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log = Join-Path $OutDir 'run.log'
function Log($m) { ("{0} {1}" -f (Get-Date -Format s), $m) | Add-Content $log -Encoding UTF8 }
$sum = [ordered]@{ generated = (Get-Date -Format o); errors = @() }
$sum.is_admin = [bool](whoami /groups 2>$null | Select-String 'S-1-16-12288')
Log "START is_admin=$($sum.is_admin)"
function Step($n, [scriptblock]$sb) { Log "START $n"; try { & $sb } catch { $sum.errors += "$n : $($_.Exception.Message)"; Log "ERROR $n $($_.Exception.Message)" }; Log "END $n" }
function Save($n, $o) { $o | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $OutDir "$n.json") -Encoding UTF8 }

Step 'bitlocker' {   # status only, never recovery keys
  $b = @(Get-BitLockerVolume -ErrorAction Stop | ForEach-Object { [ordered]@{ mount = $_.MountPoint; protection = "$($_.ProtectionStatus)"; volume = "$($_.VolumeStatus)"; method = "$($_.EncryptionMethod)"; pct = $_.EncryptionPercentage; protectors = @($_.KeyProtector | ForEach-Object { "$($_.KeyProtectorType)" }) } })
  $sum.bitlocker = $b; Save 'bitlocker' $b
}
Step 'bitlocker_manage_bde' { (manage-bde -status 2>&1) | Set-Content (Join-Path $OutDir 'manage_bde_status.txt') -Encoding UTF8 }
Step 'tpm_secureboot' {
  $t = Get-Tpm -ErrorAction SilentlyContinue | Select-Object TpmPresent, TpmReady, TpmEnabled, ManufacturerVersion
  $sb = $null; try { $sb = Confirm-SecureBootUEFI } catch { $sb = "n/a" }
  $sum.tpm = $t; $sum.secure_boot = "$sb"; Save 'tpm' @{ tpm = $t; secure_boot = "$sb" }
}
Step 'windows_features' {
  $f = @(Get-WindowsOptionalFeature -Online -ErrorAction Stop | Where-Object State -eq 'Enabled' | Select-Object -Expand FeatureName)
  $sum.windows_features_enabled = $f.Count; Save 'windows_features_enabled' $f
  $cap = @(Get-WindowsCapability -Online -ErrorAction SilentlyContinue | Where-Object State -eq 'Installed' | Select-Object -Expand Name)
  $sum.windows_capabilities_installed = $cap.Count; Save 'windows_capabilities' $cap
}
Step 'services' {
  $svc = Get-CimInstance Win32_Service | Select-Object Name, DisplayName, State, StartMode, StartName, @{n='path';e={$_.PathName}}
  Save 'services_all' $svc
  $nonms = @($svc | Where-Object { $_.path -and $_.path -notmatch '(?i)\\windows\\|svchost' })
  $sum.services = [ordered]@{ total = @($svc).Count; running = @($svc | Where-Object State -eq 'Running').Count; non_microsoft_path = $nonms.Count }
  Save 'services_third_party' ($nonms | Select-Object Name, DisplayName, State, StartMode)
}
Step 'drivers_devices' {
  $d = Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceName } | Select-Object DeviceName, Manufacturer, DriverVersion, DeviceClass
  Save 'drivers' $d; $sum.drivers = @($d).Count
}
Step 'mdm_edr_presence' {   # names only - corporate software is NOT to be touched
  $names = 'intune|mdm|crowdstrike|defender|sentinel|carbon|cortex|sophos|trend|vpn|globalprotect|forti|cisco|zscaler|okta|duo|authenticator'
  $hits = @(Get-CimInstance Win32_Service | Where-Object { $_.Name -match $names -or $_.DisplayName -match $names } | Select-Object Name, DisplayName, State)
  Save 'corporate_security_services' $hits; $sum.corporate_security_services = $hits.Count
  $ds = (dsregcmd /status 2>&1) | Select-String 'AzureAdJoined|DomainJoined|WorkplaceJoined|MdmUrl|TenantName' | ForEach-Object { $_.Line.Trim() }
  $ds | Set-Content (Join-Path $OutDir 'dsregcmd_summary.txt') -Encoding UTF8
}
Step 'scheduled_tasks_all_users' {
  $t = Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' } | Select-Object TaskName, TaskPath, State, @{n='exec';e={($_.Actions | Select-Object -First 1).Execute}}, @{n='user';e={$_.Principal.UserId}}
  Save 'scheduled_tasks_non_ms' $t; $sum.scheduled_tasks_non_microsoft = @($t).Count
}
Step 'firewall_hosts_network' {
  $sum.hosts_custom_lines = @(Get-Content "$env:windir\System32\drivers\etc\hosts" | Where-Object { $_ -and $_ -notmatch '^\s*#' }).Count
  Save 'proxy' (netsh winhttp show proxy 2>&1)
  Save 'wifi_profile_names' (netsh wlan show profiles 2>&1 | Select-String 'All User Profile|User Profile' | ForEach-Object { $_.Line.Trim() })
}
Step 'certs_metadata' {
  $c = @(Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue | Select-Object Subject, Issuer, NotAfter, HasPrivateKey, @{n='store';e={$_.PSParentPath -replace '.*::',''}})
  $sum.personal_certs = $c.Count; $sum.personal_certs_with_private_key = @($c | Where-Object HasPrivateKey).Count; Save 'certs_metadata' $c
}
Step 'env_and_path' {
  $u = [Environment]::GetEnvironmentVariables('User'); $m = [Environment]::GetEnvironmentVariables('Machine')
  $secretLike = 'KEY|TOKEN|SECRET|PASS|PWD|CRED|CONN'
  $rows = @()
  foreach ($scope in @(@('User', $u), @('Machine', $m))) { foreach ($k in $scope[1].Keys) {
    $val = if ($k -ieq 'Path') { "$($scope[1][$k])" } elseif ($k -match $secretLike) { '<redacted>' } else { "$($scope[1][$k])" }
    $rows += [ordered]@{ scope = $scope[0]; name = $k; value = $val } } }
  Save 'env_vars' $rows
  $sum.env = [ordered]@{ user_vars = $u.Count; machine_vars = $m.Count; secret_like_names = @($rows | Where-Object { $_.name -match $secretLike } | ForEach-Object { $_.name }) }
}
Step 'winget' {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget export -o (Join-Path $OutDir 'winget_export.json') --accept-source-agreements 2>&1 | Out-File (Join-Path $OutDir 'winget_export.log')
    if (Test-Path (Join-Path $OutDir 'winget_export.json')) { $w = Get-Content (Join-Path $OutDir 'winget_export.json') -Raw | ConvertFrom-Json; $sum.winget_exportable = @($w.Sources.Packages).Count }
    winget list --accept-source-agreements 2>&1 | Out-File (Join-Path $OutDir 'winget_list.txt') -Encoding UTF8
  } else { $sum.winget = 'not installed' }
  $paths = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
  $apps = Get-ItemProperty $paths -ErrorAction SilentlyContinue | Where-Object DisplayName | Select-Object DisplayName, DisplayVersion, Publisher | Sort-Object DisplayName -Unique
  $apps | Export-Csv (Join-Path $OutDir 'APPLICATION_INVENTORY.csv') -NoTypeInformation -Encoding UTF8; $sum.applications_registry = @($apps).Count
}
Step 'dev_tools' {
  $tools = 'git','code','python','py','pip','uv','conda','node','npm','pnpm','yarn','docker','wsl','java','mongosh','claude','codex','pwsh','dotnet','go','rustc','gh','az','aws','kubectl','terraform'
  $found = [ordered]@{}
  foreach ($t in $tools) { $cmd = Get-Command $t -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { $ver = ''; try { $ver = (& $cmd.Source --version 2>&1 | Select-Object -First 1) -as [string] } catch {}; $found[$t] = [ordered]@{ source = $cmd.Source; version = $ver } } }
  $sum.dev_tools = $found; Save 'dev_tools' $found
  if (Get-Command code -ErrorAction SilentlyContinue) { $ext = @(code --list-extensions --show-versions 2>$null); $ext | Set-Content (Join-Path $OutDir 'vscode_extensions.txt'); $sum.vscode_extensions = $ext.Count }
  if (Get-Command pip -ErrorAction SilentlyContinue) { pip list --format=freeze 2>$null | Set-Content (Join-Path $OutDir 'pip_freeze.txt'); $sum.pip_packages = @(Get-Content (Join-Path $OutDir 'pip_freeze.txt')).Count }
  if (Get-Command npm -ErrorAction SilentlyContinue) { npm ls -g --depth=0 2>$null | Set-Content (Join-Path $OutDir 'npm_global.txt') }
}
Step 'config_files_presence' {
  $items = [ordered]@{
    gitconfig = "$HOME\.gitconfig"; ssh_dir = "$HOME\.ssh"; vscode_settings = "$env:APPDATA\Code\User\settings.json"; vscode_keybindings = "$env:APPDATA\Code\User\keybindings.json"
    vscode_snippets = "$env:APPDATA\Code\User\snippets"; claude_dir = "$HOME\.claude"; claude_json = "$HOME\.claude.json"; codex_dir = "$HOME\.codex"; npmrc = "$HOME\.npmrc"; pip_ini = "$env:APPDATA\pip\pip.ini"
    gh_config = "$env:APPDATA\GitHub CLI"; docker_config = "$HOME\.docker"; aws = "$HOME\.aws"; azure = "$HOME\.azure"; kube = "$HOME\.kube"; mongodb_compass = "$env:APPDATA\MongoDB Compass"
    wsl_conf = "$HOME\.wslconfig"; ps_profile_cuch = "$PROFILE.CurrentUserCurrentHost"; ps_profile_cuah = "$PROFILE.CurrentUserAllHosts"; ps7_profile = "$HOME\Documents\PowerShell\Microsoft.PowerShell_profile.ps1"
  }
  $wt = Get-ChildItem "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal*\LocalState\settings.json" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($wt) { $items['windows_terminal_settings'] = $wt.FullName }
  $pres = foreach ($k in $items.Keys) { $p = $items[$k]; $ex = Test-Path $p; $sz = $null; if ($ex -and -not (Get-Item $p).PSIsContainer) { $sz = (Get-Item $p).Length }; [ordered]@{ item = $k; path = $p; exists = $ex; bytes = $sz } }
  Save 'config_presence' $pres
  $gc = "$HOME\.gitconfig"
  if (Test-Path $gc) { git config --global --list 2>$null | ForEach-Object { ($_ -split '=', 2)[0] } | Set-Content (Join-Path $OutDir 'git_global_keys.txt'); $sum.git_global_keys = @(Get-Content (Join-Path $OutDir 'git_global_keys.txt')).Count
    $sum.git_credential_helper = (git config --global credential.helper 2>$null) }
  $ssh = "$HOME\.ssh"; if (Test-Path $ssh) { Save 'ssh_names_only' (Get-ChildItem $ssh -File | Select-Object Name, Length) }
  $mdb = "$env:APPDATA\MongoDB Compass"; if (Test-Path $mdb) { Save 'compass_names_only' (Get-ChildItem $mdb -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 200 @{n='rel';e={$_.FullName.Substring($mdb.Length)}}, Length) }
}
Step 'docker_wsl' {
  if (Get-Command wsl -ErrorAction SilentlyContinue) { (wsl -l -v 2>&1) -replace "`0", '' | Set-Content (Join-Path $OutDir 'wsl_list.txt') -Encoding UTF8 }
  if (Get-Command docker -ErrorAction SilentlyContinue) { $sum.docker = [ordered]@{ images = @(docker images -q 2>$null).Count; containers = @(docker ps -aq 2>$null).Count; volumes = @(docker volume ls -q 2>$null).Count } } else { $sum.docker = 'cli not found' }
}
Step 'browsers_accounts_hint' {   # which browsers/profiles exist, no content
  $b = @{ chrome = "$env:LOCALAPPDATA\Google\Chrome\User Data"; edge = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"; firefox = "$env:APPDATA\Mozilla\Firefox\Profiles" }
  $r = foreach ($k in $b.Keys) { $p = $b[$k]; [ordered]@{ browser = $k; present = (Test-Path $p); profiles = if (Test-Path $p) { @(Get-ChildItem $p -Directory -ErrorAction SilentlyContinue | Where-Object Name -match '^(Default|Profile \d+)$|\.default' | Select-Object -Expand Name) } else { @() } } }
  Save 'browsers' $r
}
Step 'network_odbc' {
  Save 'net_adapters' (Get-NetAdapter | Select-Object Name, InterfaceDescription, Status)
  Save 'odbc' @(Get-OdbcDsn -ErrorAction SilentlyContinue | Select-Object Name, DsnType, DriverName)
}
$sum | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $OutDir 'summary_elevated.json') -Encoding UTF8
Log 'DONE'
