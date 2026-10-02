# Scraper pipeline

Finds new Upwork jobs for a set of searches and routes each to the Telegram chats subscribed to it.
No browser in the normal path. Why it is built this way: `docs/adr/0002-scraper-pipeline.md`.
How the site behaves: `research/upwork_recon/`.

```
scheduler -> [work queue] -> fetcher(s) -> [jobs queue] -> dispatcher -> Telegram
                                  |
                 TokenManager, RateLimiter, UpworkSearchClient

watchdog   -> Telegram message to the admin chat when something is wrong, and when it recovers
dashboard  -> http://127.0.0.1:8787  read-only status page (/api/status, /healthz)
bot        -> /search, /searches, /filter, /remove, /pause, /resume in Telegram (off by default)
```

## Daily use

```
python main.py scraper status                 # what it is doing; exit code 1 if a search is stale
python main.py scraper seed --query "python OR scraping"            # subscribe TELEGRAM_CHAT_ID
python main.py scraper seed --query "react" --chat 12345 --include react "react native" --exclude wordpress
python main.py scraper searches               # searches and who is subscribed
python main.py scraper filter 1 --include python scraping scraper scrape   # whole words; drops "alloy scrap"
python main.py scraper filter 1 --clear
python main.py scraper mint --all             # try every way of getting a token
python main.py scraper shadow-report --hours 24   # compare with the legacy monitor; exit 1 if an alert was missed
python main.py scraper backup                 # dump the database now (the UpworkBackup task does this daily)
python main.py scraper up                     # run everything (the UpworkScraper logon task does this)
```

Logs: `logs/scraper.log` (one JSON object per line). Useful event names: `job_new`, `job_would_deliver` /
`job_delivered`, `job_filtered_out`, `fetcher_heartbeat`, `token_minted`, `mint_challenged`,
`fetcher_rate_limited`, `fetcher_poll_failed`, `mint_chain_exhausted`, `health_problem_opened`,
`health_problems_resolved`.

## Health: alerts, dashboard, status

All three use the same rules (`health.py::evaluate`), so they cannot disagree.

| problem | when |
|---|---|
| a role is not running | scheduler, fetcher or dispatcher has not reported for ~90 s |
| a search is not being polled | subscribed search with no successful poll for `alerts.stale_search_minutes` |
| a search is failing | `alerts.failing_search_after` failed polls in a row |
| Redis / Postgres unreachable | |
| token from a fallback minter (warning) | the primary minter is being challenged: time to bump the Chrome profile |
| token not refreshed, polling paused, queue backlog (warnings) | |
| no database backup, or the newest is older than `backup.max_age_hours` (warning) | |

- **Watchdog**: one Telegram message when a problem opens, a reminder every `alerts.repeat_after_minutes`,
  one when it resolves, and a summary once a day at `alerts.daily_summary_at`. Goes to `alerts.chat_id`,
  else `ALERTS_CHAT_ID` from `.env`, else `TELEGRAM_CHAT_ID`. A day without the summary means the alerting
  itself is broken.
- **Outside check** (`ops/scraper/healthcheck.py`, run by the OS scheduler): alerts when the pipeline does
  not answer or has no watchdog. It is what covers the watchdog being dead.
- **Dashboard**: `http://127.0.0.1:8787`, shows no secrets. `/healthz` returns 503 while a critical problem
  is open. Set `DASHBOARD_PASSWORD` to require a password; binding to another address requires one
  (see `ops/scraper/README.md` for remote access over Tailscale).
- **`scraper status`**: the same in the terminal; exit code 1 on a critical problem.

## The Telegram bot

`bot.enabled: true` makes `up` start it (or run `python main.py scraper bot`). Commands: `/search <words>`,
`/searches`, `/filter <id> include: a, b; exclude: c`, `/filter <id> clear`, `/remove <id>`, `/pause`,
`/resume`, `/status`, `/help`.

Access: `bot.access: allowlist` admits `TELEGRAM_CHAT_ID` (the owner), `bot.allowed_chat_ids`, and the chats
the owner admitted from Telegram with `/allow <chat id> <name>` (`/deny <chat id>`, `/allowed`). Anyone else
is told the bot is private and shown their chat id, and the owner is told once that they tried. `open`
admits everyone. Each chat may watch `bot.max_searches_per_chat` searches and send
`bot.commands_per_minute` commands. Payments will replace the allowlist with another `AccessPolicy`
(`docs/adr/0003-product-layer.md`); nothing else changes. The old bot (now `legacy/app/notify/bot_uw.py`)
was a different flow that required a portal link; it is retired.

## Changing things

Everything tunable is in `app/config/scraper.yaml`; restart the pipeline afterwards.

| I want to… | Change |
|---|---|
| use a newer Chrome fingerprint | `transports.curl_cffi.impersonate` (and upgrade the `curl_cffi` package if the profile is new) |
| poll faster / slower | `poller.interval_s` |
| change the fallback order for tokens | reorder `minters` |
| go live (send real alerts) | `dispatcher.mode: live` |
| be told sooner / later about problems | `alerts.stale_search_minutes`, `alerts.repeat_after_minutes` |
| let someone else use the bot | add their chat id to `bot.allowed_chat_ids` |
| get shadow alerts copied to me | `dispatcher.admin_chat_id: <chat id>` |
| keep data longer | `retention_days` |
| run without Redis (one process) | `backend: memory` |

Unknown keys and broken references are rejected at startup with the reason.

## Where things are

| path | what |
|---|---|
| `config.py`, `models.py`, `errors.py` | settings, data types, the four failure types |
| `transports/` | one file per HTTP client (curl_cffi, httpcloak, requests) |
| `upwork/` | GraphQL queries, JSON parser, search client |
| `tokens/` | minters, fallback chain, shared store, manager |
| `pipeline/` | scheduler, fetcher, dispatcher, watchdog |
| `health.py`, `presence.py` | what counts as a problem; which roles are alive |
| `dashboard.py` | the status page |
| `bot.py` | Telegram commands and who may use them |
| `queue.py`, `ratelimit.py` | shared backends (Redis + in-memory) |
| `filters.py` | per-subscription include/exclude words |
| `store.py` | Postgres tables (schema `scraper`) and queries |
| `factory.py`, `cli.py` | wiring and commands |
| `minters_ext/` | minters that run as subprocesses in their own virtualenvs |
| `shadow.py` | comparison with the legacy monitor |

## Adding things

- **A new HTTP client**: a class with `get_page`, `post_api`, `close` in `transports/`, one branch in
  `transports/__init__.py::build_transport`, one `kind` in `config.py::TransportConfig`.
- **A new minter with awkward dependencies**: a script under `minters_ext/<name>/` that accepts `--url` and
  prints `{"result": {"ok": true, "token": "..."}}`, a `requirements.txt`, and a `kind: subprocess` entry in
  the YAML. No Python changes.
- **More fetchers**: start more `python main.py scraper fetcher` processes. They share the queue, the token
  and the rate budget.

## External minter environments

Created once (they are gitignored):

```
uv venv app/scraper/minters_ext/patchright/.venv --python 3.13
uv pip install --python app/scraper/minters_ext/patchright/.venv/Scripts/python.exe -r app/scraper/minters_ext/patchright/requirements.txt
uv venv app/scraper/minters_ext/hrequests/.venv --python 3.13
uv pip install --python app/scraper/minters_ext/hrequests/.venv/Scripts/python.exe -r app/scraper/minters_ext/hrequests/requirements.txt
```

patchright also needs its Chromium build (`patchright install chromium`).

## Tests

```
python -m pytest            # unit tests + Postgres (throwaway schemas) + Redis; nothing touches Upwork
```

Database and Redis tests skip themselves if the service is not reachable.
When testing against the real site, stay well under ~40 search-page requests per 5 minutes.
