# Botni Windows Task Scheduler'ga qo'shadi: har kuni 08, 11, 14, 17, 20, 23 da ishlaydi.
# Kompyuter o'chiq bo'lib qolsa, yoqilganda o'tkazib yuborilgan ishni bir marta bajaradi.
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (Get-Command python.exe).Source
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$dir\bot.py`"" -WorkingDirectory $dir
$triggers = 8, 11, 14, 17, 20, 23 | ForEach-Object { New-ScheduledTaskTrigger -Daily -At ("{0:00}:00" -f $_) }
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "Yangiliklar bot" -Action $action -Trigger $triggers -Settings $settings `
    -Description "AI, texnologiya va o'sish yangiliklarini Telegram'ga yuboradi" -Force | Out-Null
Write-Host "Tayyor: 'Yangiliklar bot' vazifasi qo'shildi ($pythonw)"

# Telegram'dagi "Hozirgi yangiliklar" tugmasini kutadigan fon jarayoni.
# Kompyuterga kirganda ishga tushadi; to'xtab qolsa, 30 daqiqa ichida qayta ishga tushiriladi.
$listen = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$dir\bot.py`" --tinglash" -WorkingDirectory $dir
$listenTriggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650))
)
$listenSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "Yangiliklar bot tugma" -Action $listen -Trigger $listenTriggers -Settings $listenSettings `
    -Description "Telegram'dagi 'Hozirgi yangiliklar' tugmasini kutadi" -Force | Out-Null
Start-ScheduledTask -TaskName "Yangiliklar bot tugma"
Write-Host "Tayyor: 'Yangiliklar bot tugma' vazifasi qo'shildi va ishga tushirildi"
