# SaaS Client Webpage Proposal (Frictionless-First)

## 1. Context and Constraints

This proposal is based on your current architecture in `app/` and intentionally ignores `monitor_uw.py` and `monitor_uw_cm.py` as requested.

Current production-ready direction in your repo:
- Telegram bot entrypoint and client command model: `app/notify/bot_uw.py`
- Core multi-tenant data model: `app/store/core.py`
- Scheduler and async queue processing: `app/workers/scheduler.py`, `app/queue/redis_streams.py`
- Runtime process model: `main.py` (`bot`, `scheduler`, `worker`, `migrate`)

Implication: your web app should be a **customer portal + control plane** for billing and query management, while Telegram remains your real-time delivery channel. Access to the bot conversation is a direct redirect action, not an account-linking flow.

## 2. Product Objective

Design a web experience where a new client can:
1. Understand value in less than 30 seconds.
2. Activate subscription in less than 2 minutes.
3. Create first query in less than 15 seconds.
4. Manage everything without needing command memorization.

## 3. Proposed Webpage Architecture

You should build two web surfaces:

1. **Marketing + Conversion Site (Public)**
- Purpose: explain value, show proof, capture signup.

2. **Client Portal (Authenticated)**
- Purpose: manage subscription, manage queries, monitor status.

Minimal information architecture:
- `/` Landing page
- `/pricing`
- `/login` and `/signup`
- `/bot` (immediate redirect to Telegram bot conversation)
- `/app` Dashboard
- `/app/queries`
- `/app/billing`
- `/app/settings`
- `/app/help`

## 4. Frictionless UX Principles (Must-Have)

- One primary action per screen.
- No forced tutorial; use inline hints.
- Default-first query creation: prefill examples and sensible defaults (`poll=60s`, `top_n=25`).
- Immediate feedback after every action (success/error banners + status chips).
- Keep Telegram as the notification surface; web handles setup and management.
- Never block users on technical wording (use plain language labels).

## 5. Exact UI Components and Buttons

## 5.1 Public Landing Page (`/`)

Primary sections:
- Hero: clear value proposition + CTA.
- “How it works” in 3 steps.
- Example alert cards.
- Social proof / trust strip.
- FAQ.

Buttons:
- `Start Free Trial`
- `See Pricing`
- `View Demo Alerts`
- `Sign In`
- `Open Bot` (direct Telegram redirect)
- `For My Account` (shown when already logged in)

## 5.2 Dashboard (`/app`)

Purpose: one-glance account health.

Widgets:
- Account status (Active).
- Subscription status (Active/Inactive/Trial/Past due).
- Active queries count.
- Alerts sent (last 24h/7d).
- Last successful worker run (or “No runs yet”).

Buttons:
- `Manage Queries`
- `Manage Billing`
- `Open Bot`
- `Pause All Alerts`
- `Resume All Alerts`
- `Create Query`

## 5.3 Query Management (`/app/queries`)

Main table columns:
- Query text
- Status (Active/Paused)
- Frequency
- Top results limit
- Last run
- New jobs delivered

Buttons:
- `Create Query`
- `Edit`
- `Pause`
- `Resume`
- `Delete`
- `Duplicate`
- `Test Query` (preview only, no save)

Create/Edit Query Modal:
- Input: plain query text
- Optional advanced: `poll interval`, `top N`
- Boolean toggles (optional later): strict mode / include description

Buttons inside modal:
- `Save Query`
- `Save & Activate`
- `Cancel`

## 5.4 Billing (`/app/billing`)

Content:
- Current plan
- Renewal date
- Payment status
- Invoice history
- Update payment method

Buttons:
- `Start Trial`
- `Manage Plan`
- `Cancel Plan`
- `Update Payment Method`
- `View Invoices`

## 5.5 Settings (`/app/settings`)

Content:
- Profile
- Timezone
- Notification preferences (future)
- Account security

Buttons:
- `Save Changes`
- `Delete Account`

## 5.6 Auth (`/login` and `/signup`)

Goal: fastest possible access, minimal typing.

Primary auth options:
- `Continue with Google` (primary button, one-click OAuth)
- `Continue with Email` (magic link)

Secondary option:
- `Use Password Instead` (small link for users who insist)

Buttons:
- `Continue with Google`
- `Continue with Email`
- `Use Password Instead`
- `For My Account`

## 6. End-to-End User Flows

## 6.1 New user path
1. Landing page -> `Start Free Trial`.
2. Signup/login (`Continue with Google` as default).
3. Billing activation (or free trial start).
4. First query creation wizard.
5. Success screen with “alerts now active”.

## 6.2 Returning user path
1. Login.
2. Dashboard health check.
3. Query adjustments.
4. Billing updates if needed.

## 6.3 Fallback path (user starts in Telegram first)
1. User sends `/start` to bot.
2. User can always continue in Telegram directly (no linking required).
3. If user needs billing/query management, bot points to `/login` then `/app/queries`.

## 7. Functional Requirements (Mapped to Current Backend)

Use your existing functions in `app/store/core.py` as canonical business operations:

- Subscription gating:
  - `upsert_portal_subscription(...)`
  - `is_subscription_active(...)`
- Query lifecycle:
  - `create_or_get_query_for_user(...)`
  - `subscribe_chat_to_query(...)`
  - `list_subscriptions_for_chat(...)`
  - `set_subscription_enabled(...)`
  - `remove_subscription(...)`
  - `update_query_settings_for_chat(...)`
- Scheduling state:
  - `list_due_queries(...)`
  - `bump_next_run(...)`
- Dedupe/delivery bookkeeping:
  - `try_mark_delivered(...)`

Recommended addition: create a small HTTP service layer (`app/api/`) that wraps these store calls with auth, validation, and response contracts.

## 8. Proposed API Surface for Portal

Suggested minimal endpoints:

- `GET /api/me`
- `GET /api/auth/providers`
- `GET /api/auth/google/start`
- `GET /api/auth/google/callback`
- `POST /api/auth/magic-link/start`
- `POST /api/auth/magic-link/verify`
- `GET /api/subscription`
- `POST /api/subscription/webhook` (billing provider webhook)
- `GET /api/queries`
- `POST /api/queries`
- `PATCH /api/queries/{id}`
- `POST /api/queries/{id}/pause`
- `POST /api/queries/{id}/resume`
- `DELETE /api/queries/{id}`
- `POST /api/queries/{id}/test`
- `GET /api/dashboard/summary`

## 9. UX/UI Direction

Design style:
- Clean, high-contrast, enterprise-trust visual language.
- Dense where useful (query table), simple where conversion matters (landing, onboarding).
- Mobile-first for quick edits; desktop-first for heavy query management.

Interaction guidance:
- Keep one sticky primary CTA per page.
- Use segmented controls for active/paused/all filters.
- Auto-save only for low-risk fields; require explicit save for query definitions.
- Confirm destructive actions (`Delete Query`, `Cancel Plan`).

Copy examples:
- Replace technical errors with guidance.
- Example: “Your account is active. Create your first query to start receiving alerts.”
- Example: “Want to chat with the bot now? Click Open Bot.”

## 10. Implementation Into Your Current Architecture

Proposed integration model:

1. Keep existing workers/scheduler/bot unchanged as core pipeline.
2. Add a thin API layer for web portal operations.
3. Add frontend app that consumes only API endpoints.
4. Keep Postgres and Redis as shared infra.
5. Continue using Telegram for final notifications.
6. Add `/bot` route that 302-redirects to `https://t.me/<YOUR_BOT_USERNAME>` (or deep link form `https://t.me/<YOUR_BOT_USERNAME>?start=web`).

Process model (runtime):
- `python main.py bot`
- `python main.py scheduler`
- `python main.py worker`
- `python -m app.api.main` (new API process)
- Frontend static/app process

Data model impact:
- Existing `core` schema is already suitable for v1 portal.
- Optional future table: `core.audit_events` for product analytics and compliance tracing.

## 11. Stack Options (Economy First, Scale Later)

## Option A: Python Monolith + Server-Rendered Frontend (Lowest Complexity)

Stack:
- Backend/API + pages: FastAPI + Jinja/HTMX
- DB: PostgreSQL (existing)
- Queue: Redis Streams (existing)
- Billing: Stripe
- Hosting: single VM (Hetzner/DO) + managed Postgres later

Pros:
- Lowest infra + operational overhead
- Fastest to ship with your current Python codebase
- Easy debugging

Cons:
- UI sophistication ceiling is lower than SPA

Best when:
- You need fastest path to paying customers with minimal cost.

## Option B: Python API + React/Next.js Frontend (Balanced)

Stack:
- API: FastAPI
- Frontend: Next.js (App Router)
- DB: PostgreSQL
- Queue: Redis Streams
- Billing: Stripe
- Hosting: Frontend on Vercel, API on Render/Fly/DO

Pros:
- Better UX polish and scalability for portal complexity
- Frontend and backend can scale independently

Cons:
- Slightly more moving parts and deployment complexity

Best when:
- You want stronger UI/UX and long-term product flexibility.

## Option C: Supabase-Centric Hybrid (Fast MVP + managed auth)

Stack:
- Frontend: Next.js
- Auth + DB + storage: Supabase
- Worker/scheduler/bot remain in Python service
- Billing: Stripe

Pros:
- Very fast auth and dashboard setup
- Reduces backend boilerplate for account management

Cons:
- Higher lock-in
- Requires careful ownership boundaries with current DB logic

Best when:
- You prioritize time-to-market over full stack control.

## 12. Recommended Choice

Recommend **Option B** for your case:
- You already have a robust Python domain/backend core.
- You need a client-facing portal that feels premium and frictionless.
- You can ship incrementally while preserving existing pipeline services.

If budget pressure is immediate, start with Option A and migrate UI to Option B later; backend contracts can remain mostly unchanged.

## 13. MVP Scope (What to Build First)

Phase 1 (must-have):
- Landing page + pricing + signup/login
- Direct Telegram bot access (`/bot` redirect + `Open Bot` buttons)
- Billing activation + status sync
- Query CRUD (create/list/pause/resume/delete)
- Dashboard summary

Phase 2 (high value):
- Query templates
- Test query preview
- Event/activity timeline
- Better analytics cards

Phase 3 (scale/premium):
- Multi-channel notifications (email/Slack/webhooks)
- Team workspaces
- Advanced filtering and routing rules

## 14. Non-Functional Requirements

- P95 portal API response under 300ms for read endpoints.
- End-to-end alert latency target: under 90s from detection.
- Full audit log for billing actions.
- Role-based admin controls (future if multi-seat plans).
- Health endpoints and observability for bot/scheduler/worker/API.

## 15. Risks and Mitigations

- Risk: Config inconsistency across modules.
  - Mitigation: enforce one settings loader and one env strategy before portal launch.

- Risk: Missing webhook reliability for subscription state.
  - Mitigation: idempotent webhook handlers + retry + dead-letter logging.

- Risk: UI/backend contract drift.
  - Mitigation: OpenAPI schema and generated frontend API client.

## 16. Final Checklist: “Frictionless” Standard

- Can a user finish signup + first query in under 2 minutes?
- Are default settings enough for first successful value?
- Does every page have one obvious primary action?
- Are all destructive actions guarded with confirmation?
- Does dashboard immediately explain what to do next?
- Can users reach the Telegram bot in one click from landing and dashboard?

If all answers are yes, your portal is aligned with frictionless UX.
