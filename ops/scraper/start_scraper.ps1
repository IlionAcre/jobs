# Starts the scraper pipeline (and re-enables its task).   pwsh ops\scraper\start_scraper.ps1
# Refuses to start a second copy next to one that is already running.
$running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'main\.py"?\s+scraper\s+up\b' })
if ($running.Count -gt 0) { "already running (supervisor pid $($running[0].ProcessId)); use stop_scraper.ps1 first to restart"; exit 1 }
Enable-ScheduledTask -TaskName "UpworkScraper" | Out-Null
Start-ScheduledTask -TaskName "UpworkScraper"
"started; check with: python main.py scraper status"
