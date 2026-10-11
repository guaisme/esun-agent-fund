param([string]$TaskName = 'ESUN-Agent-Daily-0700')
$ErrorActionPreference = 'Stop'
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
