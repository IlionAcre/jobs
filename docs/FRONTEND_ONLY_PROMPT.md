# Frontend-Only Build Prompt (Ease-of-Access Priority)

## Role

You are a senior frontend product engineer and UX designer. Build only the frontend experience for a SaaS web app. Do not implement backend logic. Use realistic mocked data and clear integration hooks.

## Core Goal

Create a frictionless client experience where users can:
1. Open the Telegram bot in one click from multiple places.
2. Sign in with one click using Google as the primary option.
3. Reach query management with minimal steps.

## Product Context

- This is a SaaS dashboard for job alerts.
- Telegram is the real-time notification channel.
- Telegram access is **not** tied to account linking in this phase.
- Billing is a **single-plan** model (no upgrade/downgrade flows).

## Fixed Stack (Option 2 from proposal)

Use this exact stack and do not change it:
- Frontend: `Next.js` (App Router, TypeScript)
- Backend/API (external to this prompt): `FastAPI`
- Data services behind API: PostgreSQL + Redis Streams
- Billing provider: Stripe (frontend only consumes API status/results)
- Frontend deploy target: Vercel
- API deploy target: Render/Fly/DO

Integration rule:
- Frontend must call API contracts only.
- Do not implement server/database logic in frontend.
- Do not use Supabase/Firebase for auth or data in this phase.

## Non-Negotiable UX Rules

- Every page must have one obvious primary CTA.
- Use plain language. Avoid technical jargon.
- Keep signup/login to the shortest possible flow.
- Always show `Open Bot` in high-visibility areas.
- Mobile experience must be first-class, not an afterthought.
- No dead ends: every page needs a clear “what next” action.

## Required Pages

1. Landing page: `/`
2. Pricing: `/pricing`
3. Login: `/login`
4. Signup: `/signup`
5. Direct bot redirect route: `/bot` (frontend route that redirects to Telegram bot URL)
6. App dashboard: `/app`
7. Queries: `/app/queries`
8. Billing: `/app/billing`
9. Settings: `/app/settings`
10. Help: `/app/help`

## Required Global Navigation

Top nav (public):
- Logo
- `Pricing`
- `Open Bot`
- `Sign In`
- `Start Free Trial` (primary)

Top nav (authenticated):
- `Dashboard`
- `Queries`
- `Billing`
- `Settings`
- `Help`
- `Open Bot` (persistent button)
- `For My Account`

## Required Buttons and Labels

Landing:
- `Start Free Trial`
- `See Pricing`
- `View Demo Alerts`
- `Sign In`
- `Open Bot`
- `For My Account` (if logged in)

Auth:
- `Continue with Google` (primary)
- `Continue with Email`
- `Use Password Instead` (secondary link style)
- `For My Account`

Dashboard:
- `Create Query` (primary)
- `Manage Queries`
- `Manage Billing`
- `Open Bot`
- `Pause All Alerts`
- `Resume All Alerts`

Queries:
- `Create Query`
- `Edit`
- `Pause`
- `Resume`
- `Delete`
- `Duplicate`
- `Test Query`
- Modal actions: `Save Query`, `Save & Activate`, `Cancel`

Billing:
- `Start Trial` (if eligible)
- `Manage Plan`
- `Cancel Plan`
- `Update Payment Method`
- `View Invoices`

Settings:
- `Save Changes`
- `Delete Account`
- `Open Bot`

Help:
- `Open Bot`
- `Contact Support`
- `For My Account`

## Ease-of-Access Requirements

- `Open Bot` must be visible above the fold on landing and dashboard.
- `Continue with Google` must be the top and most visually dominant auth option.
- Auth forms must support autofill and keyboard submission.
- Any multi-step flow must show step count and allow back navigation.
- Use sticky mobile CTA bar on landing: `Start Free Trial` + `Open Bot`.

## `/bot` Route Behavior

- Immediately redirect user to `https://t.me/<YOUR_BOT_USERNAME>`.
- Optional deep link format: `https://t.me/<YOUR_BOT_USERNAME>?start=web`.
- Show a tiny fallback message only if redirect fails: “If not redirected, tap here.”

## Component Requirements

Create reusable components:
- `PrimaryButton`
- `SecondaryButton`
- `IconButton`
- `StatusChip` (Active/Paused/Trial/Past due)
- `MetricCard`
- `QueryTable`
- `EmptyState`
- `ConfirmDialog`
- `Toast`
- `AuthOptionCard`

## Empty States (Must Design)

- No queries yet:
  - Message: “No queries yet.”
  - CTA: `Create Query`
  - Secondary CTA: `Open Bot`

- Inactive billing:
  - Message: “Your plan is inactive.”
  - CTA: `Manage Plan`

- No activity yet:
  - Message: “No alerts yet. Create your first query.”
  - CTA: `Create Query`

## Accessibility Requirements

- WCAG AA contrast minimum.
- Full keyboard navigation.
- Visible focus states for all interactive elements.
- ARIA labels for icon-only actions.
- Hit area minimum: 44x44 px on mobile.

## Performance Targets

- Lighthouse mobile performance target >= 90.
- LCP under 2.5s on landing.
- Avoid heavy client-side bundles on first load.
- Lazy-load non-critical dashboard panels.

## Visual Direction

- Clean, trustworthy, modern SaaS style.
- Distinct hierarchy with strong CTA contrast.
- Avoid generic template appearance.
- Keep typography highly readable and conversion-focused.

## Responsive Behavior

- Mobile-first layout from 360px width.
- Query table transforms into stacked cards on small screens.
- Persistent bottom action bar on mobile for key actions.

## Frontend Technical Expectations

- Framework: Next.js 15+ (App Router, TypeScript, Server Components where useful).
- Styling: Tailwind CSS.
- State/data: React Query + local UI state.
- Forms: React Hook Form + Zod validation.
- Auth UX: OAuth-first UI with `Continue with Google` primary.
- API client: typed client layer in `src/lib/api` with request/response interfaces.
- Use mock API layer (`/mocks`) and typed interfaces to prepare FastAPI integration.

## API Contract Targets (for frontend integration stubs)

Prepare client calls/stubs for:
- `GET /api/me`
- `GET /api/auth/providers`
- `GET /api/auth/google/start`
- `GET /api/auth/google/callback`
- `POST /api/auth/magic-link/start`
- `POST /api/auth/magic-link/verify`
- `GET /api/subscription`
- `GET /api/queries`
- `POST /api/queries`
- `PATCH /api/queries/{id}`
- `POST /api/queries/{id}/pause`
- `POST /api/queries/{id}/resume`
- `DELETE /api/queries/{id}`
- `POST /api/queries/{id}/test`
- `GET /api/dashboard/summary`

## Deliverables

1. High-fidelity page implementations for all required routes.
2. Reusable design system primitives used consistently.
3. Interactive flows for auth, query CRUD UI, billing management UI, and bot redirect.
4. `README` section documenting where each required button is placed.
5. Short UX rationale explaining friction-reduction decisions.

## Success Criteria

- A first-time visitor can find `Open Bot` in under 3 seconds.
- A user can choose `Continue with Google` and reach app area in one flow.
- A logged-in user can create a query from dashboard in under 2 clicks.
- No page leaves users without a clear next action.
