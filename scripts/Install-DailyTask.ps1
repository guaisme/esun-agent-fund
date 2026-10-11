param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [string]$TaskName = 'ESUN-Agent-Daily-0700',
    [string]$Repository = (Split-Path -Parent $PSScriptRoot),
    [switch]$Live,
    [switch]$UpdateData,
    [string]$StateFile,
    [string]$OperationsFile,
    [switch]$Initial,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
if ([TimeZoneInfo]::Local.Id -ne 'Taipei Standard Time') { throw 'Scheduler clock must be Taipei Standard Time; no global timezone will be changed' }
$repoRoot = (Resolve-Path -LiteralPath $Repository).Path
$pythonPath = (Resolve-Path -LiteralPath $PythonExe).Path
if ($StateFile) { $StateFile = (Resolve-Path -LiteralPath $StateFile).Path }
if ($OperationsFile) { $OperationsFile = (Resolve-Path -LiteralPath $OperationsFile).Path }
foreach ($taskPath in @($repoRoot,$pythonPath,$StateFile,$OperationsFile)) {
    if ($taskPath -and $taskPath.Contains('"')) { throw 'Invalid quote in path' }
}
$scriptPath = Join-Path $repoRoot 'scripts\Run-Daily.ps1'
$arguments = '-NoProfile -NonInteractive -File "' + $scriptPath + '" -PythonExe "' + $pythonPath + '" -Repository "' + $repoRoot + '"'
if ($Live) { $arguments += ' -Live' }
if ($UpdateData) { $arguments += ' -UpdateData' }
if ($StateFile) { $arguments += ' -StateFile "' + $StateFile + '"' }
if ($OperationsFile) { $arguments += ' -OperationsFile "' + $OperationsFile + '"' }
if ($Initial) { $arguments += ' -Initial' }
if ($DryRun) { Write-Output $arguments; exit 0 }
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw 'Task already exists; inspect or remove it first' }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At '07:00'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
# Current user interactive token: no credentials stored. It requires a logged-in session.
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'ESUN daily preflight; no live API unless explicitly enabled; compliance BLOCKED is preserved' | Out-Null
