# Check the team's Google Drive folder every 15 minutes and load anything new into the website.
# Runs hidden: the base Python's pythonw.exe never opens a window. Log: backend\drive_mirror\sync.log
#
#   powershell -ExecutionPolicy Bypass -File backend\scripts\schedule-drive-sync.ps1            # install / update
#   powershell -ExecutionPolicy Bypass -File backend\scripts\schedule-drive-sync.ps1 -Pause     # stop checking for now
#   powershell -ExecutionPolicy Bypass -File backend\scripts\schedule-drive-sync.ps1 -Resume    # start checking again
#   powershell -ExecutionPolicy Bypass -File backend\scripts\schedule-drive-sync.ps1 -Remove    # delete the schedule
#   powershell -ExecutionPolicy Bypass -File backend\scripts\schedule-drive-sync.ps1 -Status    # is it on? when next?
param([int]$Minutes = 15, [switch]$Remove, [switch]$Pause, [switch]$Resume, [switch]$Status)

$name = "SAFE website - Drive sync"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed '$name'. The website no longer checks the Drive on its own."
    return
}
if ($Pause) {
    Disable-ScheduledTask -TaskName $name | Out-Null
    Write-Output "Paused '$name'. Resume with -Resume."
    return
}
if ($Resume) {
    Enable-ScheduledTask -TaskName $name | Out-Null
    Write-Output "Resumed '$name': next check $((Get-ScheduledTaskInfo -TaskName $name).NextRunTime)."
    return
}
if ($Status) {
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $t) { Write-Output "'$name' is not installed."; return }
    $i = Get-ScheduledTaskInfo -TaskName $name
    Write-Output "'$name': $($t.State). Last run $($i.LastRunTime) (result $($i.LastTaskResult)), next $($i.NextRunTime)."
    return
}

$backend = Split-Path -Parent $PSScriptRoot
$script = Join-Path $backend "scripts\sync_drive_task.py"
# the base interpreter behind .venv (uv's .venv\Scripts\pythonw.exe would start a console python.exe)
$cfg = Get-Content (Join-Path $backend ".venv\pyvenv.cfg") -ErrorAction SilentlyContinue
$home_ = ($cfg | Where-Object { $_ -match '^\s*home\s*=' }) -replace '^\s*home\s*=\s*', ''
$pythonw = Join-Path $home_.Trim() "pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "Missing $pythonw - run 'uv sync' in the backend folder first." }

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $backend
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $Minutes)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -Hidden
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "Mirrors the SES Website Data Drive folder and imports new projects, team, partners, reviews." -Force | Out-Null
Write-Output "Scheduled '$name' every $Minutes minutes, hidden ($pythonw). Log: $backend\drive_mirror\sync.log"
