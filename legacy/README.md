# legacy/

Code that the scraper pipeline (`app/scraper/`) replaced. Nothing imports it and nothing runs it. It is kept
for reference; paths mirror where each file used to live, so restoring one is `git mv` back.

These files still contain their old imports (`from app.workers…`, `from app.settings…`), so they do not run
from here as they are.

| file | what it was | replaced by |
|---|---|---|
| `app/workers/monitor_uw.py` | Redis-stream worker that opened a browser per query | `app/scraper/pipeline/fetcher.py`, `dispatcher.py` |
| `app/workers/scheduler.py` | pushed due `core.queries` to Redis | `app/scraper/pipeline/scheduler.py` |
| `app/notify/bot_uw.py` | Telegram bot gated by a portal link and a paid subscription that never existed | `app/scraper/bot.py` |
| `app/notify/old_telegram.py` | earlier Telegram sender | `app/notify/telegram.py` |
| `app/settings.py`, `app/shared/health.py` | settings and start-up checks for the three above | `app/scraper/config.py`, `app/scraper/health.py` |
| `monitor_uw_cm.py`, `app/ingest/upwork_cm.py`, `app/shared/browser_cm.py`, `app/shared/captcha_handle_cm.py`, `debug_captcha.py` | Camoufox variant of the single-process monitor | no browser is needed any more |

## Not moved yet

`monitor_uw.py` (repo root) is still the production monitor until the switch to the pipeline. It imports
`app/ingest/upwork.py`, `app/parse/`, `app/shared/browser.py`, `app/shared/captcha_handle.py`,
`app/shared/models.py`, `app/store/jobs.py` and `app/config/`, so those stay in place. After the switch,
`monitor_uw.py`, `app/ingest/`, `app/parse/`, `app/shared/browser.py` and `app/shared/models.py` can move here
too. `app/shared/captcha_handle.py` stays: the last-resort SeleniumBase minter uses it.

`app/store/core.py` and `init_core.py` stay because the `core` schema (`core.chats`) is still used by the
pipeline's subscriptions.
