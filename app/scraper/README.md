# Scraper pipeline

Finds new Upwork jobs for a set of searches and routes each to the Telegram chats subscribed to it.
No browser in the normal path. Why it is built this way: `docs/adr/0002-scraper-pipeline.md`.
How the site behaves: `research/upwork_recon/`.

```
scheduler -> [work queue] -> fetcher(s) -> [jobs queue] -> dispatcher -> Telegram
                                  |
                 TokenManager, RateLimiter, UpworkSearchClient
```

## Daily use

```
python main.py scraper status                 # what it is doing; exit code 1 if a search is stale
python main.py scraper seed --query "python OR scraping"            # subscribe TELEGRAM_CHAT_ID
python main.py scraper seed --query "react" --chat 12345 --include react "react native" --exclude wordpress
python main.py scraper searches               # searches and who is subscribed
python main.py scraper mint --all             # try every way of getting a token
python main.py scraper shadow-report --hours 24   # compare with the legacy monitor
python main.py scraper up                     # run everything (the UpworkScraper logon task does this)
```

Logs: `logs/scraper.log` (one JSON object per line). Useful event names: `job_new`, `job_would_deliver` /
`job_delivered`, `job_filtered_out`, `fetcher_heartbeat`, `token_minted`, `mint_challenged`,
`fetcher_rate_limited`, `fetcher_poll_failed`, `mint_chain_exhausted`.

## Changing things

Everything tunable is in `app/config/scraper.yaml`; restart the pipeline afterwards.

| I want to… | Change |
|---|---|
| use a newer Chrome fingerprint | `transports.curl_cffi.impersonate` (and upgrade the `curl_cffi` package if the profile is new) |
| poll faster / slower | `poller.interval_s` |
| change the fallback order for tokens | reorder `minters` |
| go live (send real alerts) | `dispatcher.mode: live` |
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
| `pipeline/` | scheduler, fetcher, dispatcher |
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
