# ADR 0002: Browserless scraper pipeline (`app/scraper`)

## Status
Accepted (2026-10-01). Running in shadow mode next to the legacy `monitor_uw.py`.

## Context
The legacy monitor keeps a Chrome window on the Upwork search page and reloads it every 60 s. It costs
~0.6-0.8 GB of RAM permanently, serves one query and one chat, and hangs silently if its browser dies.

Reconnaissance (`research/upwork_recon/`) established how the site actually works:
- Search results come from `POST /api/graphql/v1?alias=visitorJobSearch`; the only credential is a Bearer
  token, which is the cookie `UniversalSearchNuxt_vt` set by the search page. It is valid for 14-23 h.
- No browser is needed, even to get the token: an HTTP client with a *current and consistent* browser
  fingerprint (curl_cffi `chrome150`) gets the page in ~1 s. Cloudflare challenges inconsistent clients
  (stale profile, hand-written headers, a Python client over HTTP/2), not non-browsers as such.
- The API accepts any field selection: asking only for job ids costs ~330 bytes per poll.
- New jobs become searchable 40-75 s after publishing (sometimes sooner); polling faster than ~20-30 s
  gains little. Limits are per IP.

## Decision
A pipeline of three roles connected by queues, each a separate module and entrypoint:

    scheduler -> [work queue] -> fetcher(s) -> [jobs queue] -> dispatcher -> Telegram
    watchdog (alerts) · dashboard (status page) · bot (subscriptions)        <- supporting roles

- **Fetch once, fan out.** A `search` is a distinct query text, polled once; `subscriptions` link chats to
  searches. Request volume grows with distinct searches, not with users.
- **Two-tier poll.** Tier 1 asks for the newest ids; tier 2 fetches details only when an id is new.
- **Token as a shared resource.** One token per egress IP in Redis; minting is single-flight and happens
  early (10 h) while the old token still works.
- **Mint chain with a failure policy by cause**: challenged -> next minter; rate limited -> back off, never
  switch; transient -> retry the same minter. Order: curl_cffi, httpcloak, hrequests, patchright browser,
  SeleniumBase. Browser and conflicting-dependency minters run as subprocesses in their own virtualenvs.
- **Interfaces with two implementations** for everything shared (queue, token store, rate limiter): Redis
  for production, in-memory for tests and single-process use.
- **Postgres is the source of truth** (schema `scraper`): schedule, jobs, hits, deliveries. Redis holds only
  what can be rebuilt (queue, token, counters).
- **One YAML** (`app/config/scraper.yaml`) holds every tunable, validated at startup; secrets stay in `.env`.
- **Additive.** `monitor_uw.py`, the legacy worker and the `core.*`/`upwork.*` tables are untouched.

## Consequences
- Always-on cost drops from a browser to small Python processes; a browser exists only for seconds, and only
  if the HTTP minters are challenged.
- Scaling path without redesign: more `fetcher` processes share the queue, token and rate budget; more IPs
  means one `egress_id` (token + budget) per IP and a proxy per transport. Neither is built yet.
- Upkeep: browser fingerprints go stale (`chrome142` is already challenged). When mints start falling
  through the chain, bump `transports.curl_cffi.impersonate` and the `curl_cffi` package.
- `requests` must stay on standard urllib3 (HTTP/1.1). Never install niquests/urllib3-future in the main
  environment.
- Delivery is at-most-once per chat and job (a delivery row is written before sending).
- Health has one definition (`app/scraper/health.py`) used by three views: a watchdog role that messages
  the admin chat when a problem opens, persists or resolves; a read-only dashboard served by the pipeline
  itself (standard library, localhost, no login); and `python main.py scraper status`.
  The watchdog is silent when healthy, so it cannot report its own death; the dashboard `/healthz` endpoint
  is the hook for an external uptime check.
- A Telegram bot (`app/scraper/bot.py`) manages subscriptions and filters. Access is an `AccessPolicy`
  (allowlist or open) because there is no portal or payment system yet; a paid-subscription policy slots
  in there later. The frontend in `frontend/` is a mock-up with no backend and is not connected.
- Redis is required for multi-process operation (see `ops/redis/README.md`).

## Supersedes / relates to
ADR 0001 named `main.py bot|scheduler|worker` the production path. That worker opens a browser per query.
This ADR adds `main.py scraper ...` as its replacement for fetching; the Telegram bot signup flow of ADR 0001
is unchanged and not yet connected to the new `scraper.subscriptions` table.
