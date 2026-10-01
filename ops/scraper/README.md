# Running the scraper pipeline on this Windows PC

- Scheduled task **UpworkScraper** (at logon, 1 minute delay) runs `ops/scraper/start_scraper.cmd`.
- The script waits for Redis (`UpworkRedis` task, see `ops/redis/README.md`), then runs
  `python main.py scraper up` and restarts it if it ever exits.
- `up` starts one process per role (scheduler, fetcher, dispatcher, dashboard, watchdog, and the bot if
  enabled) and restarts any that exits. If `up` itself is killed, the roles notice within a few seconds and
  exit, so nothing is left orphaned.
- Status page: http://127.0.0.1:8787 . Health alerts go to your Telegram chat.
- Log: `logs/scraper.log` (rotated to `scraper.log.1` at ~20 MB on start).

```
schtasks /Run /TN UpworkScraper          # start now
schtasks /End /TN UpworkScraper          # stop (then check `python main.py scraper status`)
schtasks /Delete /TN UpworkScraper /F    # remove the autostart
python main.py scraper status
```

Recreate the task:
```
schtasks /Create /TN "UpworkScraper" /TR "<repo>\ops\scraper\start_scraper.cmd" /SC ONLOGON /DELAY 0001:00 /RL LIMITED /F
```

The pipeline is in **shadow mode** (`dispatcher.mode: shadow` in `app/config/scraper.yaml`): it records what
it would have sent but sends nothing. Compare with the legacy monitor with
`python main.py scraper shadow-report --hours 24`, then switch to `live` and stop `monitor_uw.py`.

On Linux the same thing is a systemd unit running `python main.py scraper up` (or one unit per role).
