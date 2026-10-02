# ADR 0003: Product layer (accounts, portal API, payments)

## Status
Proposed. **Design only: nothing here is built.** Decisions were taken with the owner on 2026-10-01; items
marked *open* still need one.

## Context
The scraper pipeline (ADR 0002) fetches, stores and routes alerts, and the Telegram bot manages searches for
chats on an allowlist. To sell it, three things are missing: a way to know who a customer is, a way for them
to pay, and the web portal. A Next.js mock-up exists in `frontend/` (every call returns demo data); the
earlier proposal is `docs/SAAS_WEBPAGE_PROPOSAL.md`. The old `core.users`, `core.queries` and
`core.subscriptions` tables are empty and their bot is retired (`legacy/`).

Constraints: the owner is in Colombia; one home server and one residential IP, so every distinct search
costs requests against a shared budget; Telegram is the delivery channel.

## Decisions

### 1. One access check, many sources
The bot already asks an `AccessPolicy` (`app/scraper/bot.py`) whether a chat may use it. The product layer
adds an **entitlement**: "this account may watch N searches until date D". A new `EntitlementPolicy`
replaces the allowlist; the bot's commands, the store and the pipeline do not change. Manual grants (the
owner's `/allow`) remain as one source of entitlements, which is also how testers and free accounts work.

```
payment webhook ─┐
Telegram Stars  ─┼─► entitlements (account, plan, valid_until, source) ─► is_entitled(account) ─► bot + API
manual grant    ─┘
```

Plans (search limit, poll interval) live in `scraper.yaml`, not in code.

### 2. Accounts
- `scraper.accounts` (id, created_at, display name)
- `scraper.account_identities` (account, kind `telegram | google | email`, external id, unique per kind)
- `scraper.sessions` (opaque token hash, account, expires_at) with an HTTP-only cookie
- `scraper.subscriptions` gains `account_id`; a chat belongs to exactly one account.

Sign-in methods (all three were requested):
- **Telegram Login Widget**: the payload is verified with the bot token (HMAC-SHA256). The Telegram identity
  and the chat are the same thing, so alerts work immediately.
- **Google OAuth** and **email magic link**: familiar, but the account then has no chat. The portal shows
  "Connect Telegram", a `t.me/<bot>?start=<one-time code>` link; the bot's `/start <code>` attaches the chat.
- Magic link needs an email sender. *Open: which provider.*

Suggested build order within accounts: Telegram first (smallest, and it is the only one that works without
the connect step), then Google, then magic link.

### 3. Portal API
`app/api/` (FastAPI), implementing the endpoints the mock-up already calls
(`frontend/src/lib/api/types.ts`, proposal §8): `/api/me`, `/api/auth/*`, `/api/subscription`,
`/api/queries` (+ pause, resume, delete, test), `/api/dashboard/summary`.

It is a thin layer over `ScraperStore` (`subscriptions_for_chat`, `subscribe`, `set_filters`,
`remove_subscription`, `set_chat_enabled`, `recent_jobs`), the same functions the bot uses, so the two can
never disagree. Differences from the mock-up to settle when wiring it:
- `Query {name, interval}` becomes query text plus include/exclude words; the interval is a plan property,
  not a per-search choice.
- `POST /api/queries/{id}/test` runs one search against the shared request budget: rate-limit it per account.
- The API is a new role (`scraper api`) started by `up`, behind the same reverse proxy as the frontend.

### 4. Payments
Two channels feed the entitlement table through a `BillingProvider` interface (verify webhook → upsert
entitlement), so adding or replacing one touches nothing else.

- **Website: Paddle** (merchant of record). Stripe does not open accounts for Colombian residents, and a US
  bank account at Global66 or Payoneer does not change that: Stripe requires a legal entity in a supported
  country. Paddle sells on the owner's behalf, handles VAT and sales tax, charges about 5% + $0.50, and pays
  out to Colombia. *Open: confirm the payout route (Payoneer, PayPal or wire) when signing up.* Lemon Squeezy
  is the like-for-like alternative. If a US LLC is formed later, Stripe becomes possible and cheaper
  (~2.9% + 30c) and is a new `BillingProvider`, not a redesign.
- **Inside the bot: Telegram Stars.** Telegram requires Stars for digital goods sold through a bot, and if
  the same thing is sold on a website it must also be purchasable with Stars; otherwise the bot can be hidden
  from mobile users. The bot gets `/subscribe` (Stars invoice) and handles `successful_payment`.

Sources: Stripe global availability (stripe.com/global); Paddle supported countries (paddle.com/help);
Telegram bot developer terms (telegram.org/tos/bot-developers) and Stars payments documentation
(core.telegram.org/bots/payments-stars). Checked 2026-10-01; fees and country lists change.

### 5. Build order
1. Accounts + sessions + Telegram login; API read endpoints; frontend shows real data.
2. Query management through the API; `EntitlementPolicy` with manual grants only.
3. Telegram Stars in the bot.
4. Paddle checkout, webhook and customer portal link on `/app/billing`.
5. Google and magic-link sign-in with "Connect Telegram".
6. Open the bot to the public (`bot.access` no longer an allowlist).

## Open items
- Plans and prices; whether there is a free trial and how long.
- Email provider for magic links; domain; where the frontend and API are hosted (the scraper must stay on the
  residential IP, the portal need not).
- Terms of service and privacy policy pages (required by Paddle and by Google OAuth).
- Capacity: how many distinct searches one IP can poll at the chosen interval. This sets how many paying
  users the current server supports before a second egress is needed (`egress_id` in ADR 0002).
- What happens to a user's searches when an entitlement lapses (pause, grace period, delete after N days).

## Consequences
- No change to the pipeline's roles or tables beyond the additive tables above.
- Two billing integrations to maintain, in exchange for compliance with Telegram's rule and for payouts that
  work from Colombia.
- The portal is optional for a first sale: steps 1 to 3 are enough to charge through the bot.
