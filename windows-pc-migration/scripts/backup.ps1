# Thin wrapper: .$a.ps1 -Destination/-Source <path> [-DryRun] ...  (see migration.ps1)
param([string]$Destination, [string]$Source, [switch]$DryRun, [switch]$IncludeSensitive, [switch]$IncludeRebuildable, [switch]$RestoreTasks)
$p = @{}; foreach ($k in 'Destination','Source') { if ($PSBoundParameters.ContainsKey($k)) { $p[$k] = $PSBoundParameters[$k] } }
& (Join-Path $PSScriptRoot 'migration.ps1') 'backup' @p -DryRun:$DryRun -IncludeSensitive:$IncludeSensitive -IncludeRebuildable:$IncludeRebuildable -RestoreTasks:$RestoreTasks
