# Moving the pipeline to Linux

Written for Debian/Ubuntu on the same machine and home connection as today (dual boot). **Not yet run on a
real Linux install**: treat the first pass as a test and fix this file as you go. The unit files assume the
code lives in `/opt/upwork` and runs as a user `upwork`; change both consistently if you prefer otherwise.

What changes compared with Windows: everything starts at boot (no logon needed), Redis runs natively (no
Podman VM), and systemd replaces Task Scheduler and `start_scraper.cmd`.

## 0. Before leaving Windows

1. `python main.py scraper backup` and copy the newest `upwork-*.dump` and `.env` somewhere the Linux side
   can read (USB stick, cloud drive). `.env` is not in git.
2. Stop the Windows tasks so two pipelines never poll from the same IP at once:
   `schtasks /End /TN UpworkScraper` (and disable `UpworkScraper`, `UpworkScraperCheck`, `UpworkBackup`).

## 1. Packages

```bash
sudo apt update
sudo apt install -y git curl postgresql redis-server
curl -LsSf https://astral.sh/uv/install.sh | sh          # uv installs the Python version the project pins
```

Redis: the Debian default already binds to localhost only. Turn on the append-only file so the token and
queues survive a restart (same as the Windows container):

```bash
sudo sed -i 's/^appendonly no/appendonly yes/' /etc/redis/redis.conf
sudo systemctl enable --now redis-server
redis-cli ping                                            # PONG
```

## 2. Code and environment

```bash
sudo useradd --system --create-home --home-dir /opt/upwork --shell /bin/bash upwork
sudo -iu upwork
git clone https://github.com/IlionAcre/jobs /opt/upwork && cd /opt/upwork && git checkout experiment
uv sync --frozen
cp /path/to/.env .env && chmod 600 .env
```

In `.env`: `DB_HOST=localhost`, `REDIS_URL=redis://127.0.0.1:6379/0`, and the same Telegram values as on
Windows. The external minters keep their own environments; recreate the ones you use:

```bash
for m in hrequests patchright; do
  (cd app/scraper/minters_ext/$m && uv venv .venv && uv pip install -p .venv -r requirements.txt)
done
```

`app/config/scraper.yaml` names each minter's environment folder (`python: …/.venv`), so it needs no change.
Patchright and SeleniumBase also need a browser (`patchright install chromium`, Google Chrome for
SeleniumBase) and a display or `xvfb-run` for headed mode.

## 3. Database

```bash
sudo -u postgres psql -c "CREATE USER upwork_app PASSWORD '<DB_PASSWORD from .env>'"
sudo -u postgres createdb -O upwork_app <DB_NAME from .env>
sudo -u postgres pg_restore --no-owner --role=upwork_app -d <DB_NAME> /path/to/upwork-<date>.dump
cd /opt/upwork && uv run python main.py migrate          # no-op if the dump was current
```

## 4. Does this OS still pass Cloudflare?  (do this before enabling anything)

The fingerprint results were measured on Windows. Same IP, different TLS/OS details, so check:

```bash
uv run python main.py scraper mint --all                  # each minter: OK or FAIL
uv run python main.py scraper status
```

`curl_cffi` should say OK. If it does not, stop here and investigate (`research/upwork_recon/10_tools.md`);
do not let the service loop on a failing minter. Keep to a handful of requests: the limit is per IP.

## 5. Services

```bash
sudo cp ops/linux/*.service ops/linux/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now upwork-scraper.service
sudo systemctl enable --now upwork-scraper-check.timer upwork-backup.timer
```

(Old rows are deleted by the scheduler role itself, once a day; no timer is needed for that.)

```bash
systemctl status upwork-scraper            # the supervisor and its role processes
journalctl -u upwork-scraper -f            # start-up errors (the pipeline's own log is logs/scraper.log)
uv run python main.py scraper status
curl -s localhost:8787/healthz
systemctl list-timers 'upwork-*'
```

If your Redis unit is called `redis.service` rather than `redis-server.service`, edit the `After=`/`Requires=`
lines in `upwork-scraper.service`.

## 6. Checks after the move

- Reboot once: `systemctl status upwork-scraper` is active without anyone logging in.
- `sudo systemctl kill -s KILL upwork-scraper`: it is back within ~15 s and no role is left orphaned.
- Stop Redis for a minute: one Telegram alert, then "Resolved".
- The daily summary arrives the next morning.

## 7. Status page over Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
tailscale ip -4
```

Then follow "Opening the status page from another device" in `ops/scraper/README.md`.
