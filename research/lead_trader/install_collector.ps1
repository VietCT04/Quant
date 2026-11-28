$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$taskName = 'Quant Binance lead collector 5140488422016171264'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Get-ScheduledTask -TaskName $taskName
    return
}
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$python = (Resolve-Path (Join-Path $workspace '.venv/Scripts/pythonw.exe')).Path
$action = New-ScheduledTaskAction -Execute $python -Argument '-m research.lead_trader.collector ensure' -WorkingDirectory $workspace
$logon = New-ScheduledTaskTrigger -AtLogOn -User $account
$daily = New-ScheduledTaskTrigger -Daily -At '00:00'
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 1)
$daily.Repetition = $repeat.Repetition
$options = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($logon, $daily) -Settings $options -User $account -RunLevel Limited -Description 'Restarts the local read-only Quant Binance archive worker and respects a persisted stop.'
