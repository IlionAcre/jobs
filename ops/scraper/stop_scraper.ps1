# Stops the scraper pipeline and keeps it stopped.   pwsh ops\scraper\stop_scraper.ps1
# Start it again with:                               pwsh ops\scraper\start_scraper.ps1
#
# Ending the scheduled task is not enough: it ends the launcher, but the supervisor (`scraper up`) keeps
# running, and the task would start a second pipeline next to it (this happened on 2026-10-02).
# So: disable the task (it re-runs every 5 minutes), end it, stop the start script's loop, then the
# supervisor. The roles notice the supervisor is gone and exit by themselves within a few seconds.
$ErrorActionPreference = "Stop"
Disable-ScheduledTask -TaskName "UpworkScraper" | Out-Null
Stop-ScheduledTask -TaskName "UpworkScraper"

$all = Get-CimInstance Win32_Process
$all | Where-Object { $_.Name -in "cmd.exe", "wscript.exe" -and $_.CommandLine -match "start_scraper\.cmd" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$all | Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match 'main\.py"?\s+scraper\s+up\b' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

foreach ($i in 1..15) {
    Start-Sleep -Seconds 2
    $left = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'main\.py"?\s+scraper\s+\w+' })
    if ($left.Count -eq 0) { "pipeline stopped (task UpworkScraper disabled)"; exit 0 }
}
"still running: $($left.Count) process(es): $(($left | ForEach-Object { $_.ProcessId }) -join ', ')"
exit 1
