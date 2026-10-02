# Running the scraper pipeline on this Windows PC

Three scheduled tasks (all run as the logged-in user, so they start at logon, not at boot):

| task | what it runs | when |
|---|---|---|
| **UpworkScraper** | `ops/scraper/start_scraper.cmd` → `python main.py scraper up --log-file logs\scraper.log` | at logon, and every 5 minutes (ignored while it is already running) |
| **UpworkScraperCheck** | `ops/scraper/healthcheck.py` (outside check) | every 5 minutes |
| **UpworkBackup** | `python main.py scraper backup` | daily at 03:30 (or as soon as possible if the PC was off) |

Redis comes from the `UpworkRedis` task (`ops/redis/README.md`). For Linux see `ops/linux/install.md`.

## What runs

`up` starts one process per role (scheduler, fetcher, dispatcher, dashboard, watchdog, and the bot if
`bot.enabled`) and restarts any that exits. If `up` itself is killed, the roles notice within a few seconds
and exit, and the start script launches it again about 15 seconds later.

```
python main.py scraper status                    # roles, token, queues, problems (exit 1 on a critical one)
python main.py scraper shadow-report --hours 24  # compared with monitor_uw.py; exit 1 if an alert was missed
pwsh ops\scraper\stop_scraper.ps1                # stop everything and keep it stopped
pwsh ops\scraper\start_scraper.ps1               # start (refuses if it is already running)
```

Nothing shows on the desktop: the tasks run through `ops/run_hidden.vbs`, so there is no console window to
close by accident. Ending the task in Task Scheduler does **not** stop the pipeline (it only ends the
launcher); use the stop script. `scraper up` refuses to start while another one is running.

Status page: http://127.0.0.1:8787 . Logs: `logs/scraper.log` (rotated at start-up, see `log:` in
`app/config/scraper.yaml`) and `logs/start_scraper.log` (the start script's own lines).

## Who tells you when something is wrong

1. **Watchdog** (a role of the pipeline): a Telegram message when a problem opens, a reminder every hour
   while it stays open, one when it resolves, and a summary once a day (`alerts.daily_summary_at`). A day
   without the summary means the alerting itself is broken.
2. **Outside check** (`healthcheck.py`): independent of the pipeline and of the virtual environment's
   packages. It alerts when the pipeline does not answer, or answers but has no watchdog, for two checks in a
   row (about 10 minutes), and again when it recovers. It cannot help when the PC itself is off.

Both send to `alerts.chat_id` (scraper.yaml), else `ALERTS_CHAT_ID` (.env), else `TELEGRAM_CHAT_ID`.
To use a private channel: create it, add the bot as an administrator (the bot then messages you the
channel's id), put that id in `.env` as `ALERTS_CHAT_ID=-100…`, and restart the pipeline.

## Backups

`python main.py scraper backup` writes `upwork-<date>-<time>.dump` to `backup.dir` (default: a
`upwork-backups` folder next to the repository), checks that the dump is readable, and deletes dumps older
than `backup.keep_days` (never the newest three). Set `backup.copy_to` to a folder that a cloud-drive client
syncs to keep a copy off this disk. A missing or overdue backup shows up as a health warning.

Restore into an empty database (needs a user allowed to create it; the app's user is not):
```
createdb -U postgres upwork_restored
pg_restore --no-owner -U postgres -d upwork_restored upwork-<date>-<time>.dump
```

## Opening the status page from another device

The page speaks plain HTTP and must never face the open internet. Use a private network:

1. Install Tailscale on this PC and on the phone/laptop, signed in to the same account.
2. Put a password in `.env`: `DASHBOARD_PASSWORD=…` (any user name works at the prompt).
3. In `app/config/scraper.yaml` set `dashboard.host` to this PC's Tailscale address (`tailscale ip -4`).
   The page also keeps answering on 127.0.0.1 for the outside check. Without a password the pipeline
   refuses to bind to anything but localhost.
4. Restart the pipeline and open `http://<tailscale address>:8787`.

## Recreating the tasks (PowerShell)

```powershell
$root = "<repo>"
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$every5 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

Register-ScheduledTask -TaskName UpworkScraper -Trigger @($logon, $every5) -Settings $s -Force `
  -Action (New-ScheduledTaskAction -Execute wscript.exe -WorkingDirectory $root -Argument "//B //Nologo `"$root\opsun_hidden.vbs`" `"$root\ops\scraper\start_scraper.cmd`"")
Register-ScheduledTask -TaskName UpworkRedis -Trigger @($logon, $every5) -Settings $s -Force `
  -Action (New-ScheduledTaskAction -Execute wscript.exe -WorkingDirectory $root -Argument "//B //Nologo `"$root\opsun_hidden.vbs`" `"$root\opsedis\start_redis.cmd`"")
Register-ScheduledTask -TaskName UpworkScraperCheck -Trigger @($logon, $every5) -Settings $s -Force `
  -Action (New-ScheduledTaskAction -Execute "$root\.venv\Scripts\pythonw.exe" -Argument "`"$root\ops\scraper\healthcheck.py`"" -WorkingDirectory $root)
Register-ScheduledTask -TaskName UpworkBackup -Trigger (New-ScheduledTaskTrigger -Daily -At 03:30) -Settings $s -Force `
  -Action (New-ScheduledTaskAction -Execute "$root\.venv\Scripts\pythonw.exe" -Argument "`"$root\main.py`" scraper backup" -WorkingDirectory $root)
```

Do not create these with `schtasks /Create`: it gives tasks a 72-hour execution limit.
