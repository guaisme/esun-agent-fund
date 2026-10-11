param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [string]$Repository = (Split-Path -Parent $PSScriptRoot),
    [switch]$Live,
    [switch]$UpdateData,
    [string]$StateFile,
    [string]$OperationsFile,
    [switch]$Initial,
    [string]$ResumeRunId,
    [switch]$Rerun
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath $Repository).Path
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Python executable not found' }
Set-Location -LiteralPath $repoRoot
$localDate = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, 'Taipei Standard Time').ToString('yyyy-MM-dd')
$logRoot = Join-Path $repoRoot 'outputs\scheduler'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$logPath = Join-Path $logRoot ($localDate + '-' + [Guid]::NewGuid().ToString('N') + '.log')
$runArgs = @('-m','src.pipeline.cli','daily-run','--calendar',("data/metadata/twse-calendar-" + $localDate.Substring(0,4) + '.json'))
if ($Live) { $runArgs += '--live' }
if ($UpdateData) { $runArgs += '--update-data' }
if ($StateFile) { $runArgs += @('--state',$StateFile) }
if ($OperationsFile) { $runArgs += @('--operations',$OperationsFile) }
if ($Initial) { $runArgs += '--initial' }
if ($ResumeRunId) { $runArgs += @('--resume',$ResumeRunId) }
if ($Rerun) { $runArgs += '--rerun' }
& $PythonExe @runArgs 2>&1 | Tee-Object -FilePath $logPath
$runCode = $LASTEXITCODE
# 2 is a deliberate compliance block; retrying cannot invent the missing evidence.
Write-Output "Daily runner exit=$runCode; log=$logPath"
exit $runCode
