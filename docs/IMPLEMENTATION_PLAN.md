# Implementation Plan Based on PROJECT_ASSESSMENT_REPORT.md

> **Superseded (2026-10-01).** This describes the old browser-based worker, scheduler and bot, now in `legacy/`. The current design is `docs/adr/0002-scraper-pipeline.md`; the product layer is `docs/adr/0003-product-layer.md`.

## Note

No COMPLETE CAPS comments were detected in the current `PROJECT_ASSESSMENT_REPORT.md` content. This plan is based on the full report text currently present.

## Goals

1. Stabilize runtime correctness for multi-user operation.
2. Standardize architecture for deployment.
3. Add minimum production readiness (migrations, tests, deploy assets, docs, observability).

## Phase 0: Alignment and Scope Freeze (0.5 day)

### Tasks

- Confirm canonical runtime path: `bot + scheduler + worker` as primary production path.
- Mark `monitor_uw.py` and `monitor_uw_cm.py` as local/debug-only for now.
- Freeze field naming decision: keep `camufoux` (backward compatible) or rename to `camoufox` with compatibility alias.

### Deliverables

- Short architecture decision note in repo (`docs/adr/0001-runtime-path.md`).
- Backlog issue list with owners and priority labels.

### Exit Criteria

- Team agrees on primary execution path and naming strategy.

## Phase 1: Runtime Correctness Fixes (Priority 0) (1-2 days)

### 1.1 Unify configuration system

### Tasks

- Choose one config loader as source of truth (recommended: Pydantic `app/config/__init__.py`).
- Refactor all runtime entrypoints to import from one loader only.
- Remove or deprecate conflicting loader logic in `app/shared/models.py`.
- Keep config schema backward compatible with current `app/config/config.yaml`.

### Files in scope

- `app/config/__init__.py`
- `app/shared/models.py`
- `app/workers/monitor_uw.py`
- `monitor_uw.py`
- `monitor_uw_cm.py`

### Exit Criteria

- All commands start without config schema mismatch.
- One canonical `load_config()` path used everywhere.

### 1.2 Fix environment loading order

### Tasks

- Move env-dependent constants into `main()` or a settings factory called after `load_dotenv()`.
- Update scheduler/worker/bot to avoid import-time env reads.

### Files in scope

- `app/workers/scheduler.py`
- `app/workers/monitor_uw.py`
- `app/notify/bot_uw.py`

### Exit Criteria

- `.env` values for stream/group/consumer/portal URL are reliably respected.

### 1.3 Fix Camoufox-specific bugs

### Tasks

- Correct URL query assembly in `app/ingest/upwork_cm.py`.
- Correct headless behavior logic in `app/shared/browser_cm.py`.
- Add focused unit tests for URL builder and headless option mapping.

### Exit Criteria

- CM path uses valid URL and honors headed/headless config.

### 1.4 Add startup health checks

### Tasks

- Add preflight checks for required env vars and service connectivity (DB, Redis, Telegram token).
- Fail fast with explicit error messages.

### Exit Criteria

- Startup fails early with actionable errors when config/services are invalid.

## Phase 2: Data and Reliability Hardening (Priority 1) (2-3 days)

### 2.1 Introduce Alembic migrations

### Tasks

- Initialize Alembic.
- Add baseline migration for existing `core` and `upwork` schemas.
- Refactor runtime table creation to minimal safety checks only (or keep idempotent guard while migrations become source of truth).

### Exit Criteria

- Fresh setup and existing DB upgrade are both supported via migrations.

### 2.2 Queue resilience and poison message policy

### Tasks

- Define retries policy for worker failures.
- Add dead-letter stream (`work:upwork:dlq`) and requeue tooling.
- Add observability fields to logs when message fails repeatedly.

### Exit Criteria

- Poison messages no longer silently loop forever.

### 2.3 Structured logging

### Tasks

- Replace print-based logs with structured logger.
- Include `query_id`, `subscription_id`, `msg_id`, `chat_id` where applicable.

### Exit Criteria

- Logs are machine-parsable and traceable per request/message.

## Phase 3: Test Coverage Baseline (Priority 2) (2-3 days)

### 3.1 Unit tests

### Targets

- Query compiler behavior (`/query` parsing logic).
- Upwork parser smoke tests using fixtures (`search.html` + minimal synthetic samples).
- Config loading and env precedence.
- CM URL builder and browser option mapping.

### 3.2 Integration tests

### Targets

- Core store functions around subscriptions/deliveries/priming.
- Scheduler due-query enqueue flow.

### Exit Criteria

- CI test run covers core critical paths and blocks regressions.

## Phase 4: Deployment Packaging (Priority 2) (1-2 days)

### Tasks

- Add `Dockerfile` for app runtime.
- Add `docker-compose.yml` for local/prod-like stack (app + postgres + redis).
- Define process split: bot, scheduler, worker (separate services).
- Add environment templates (`.env.example`).

### Exit Criteria

- One command can bring up full stack locally for end-to-end verification.

## Phase 5: Documentation and Ops Readiness (1-2 days)

### Tasks

- Replace empty `README.md` with real setup/run/deploy docs.
- Add architecture diagram and data flow notes.
- Add runbooks:
  - boot sequence,
  - migration procedure,
  - DLQ handling,
  - common incident checks.
- Define minimum metrics:
  - scheduler due count,
  - worker processed/fail counts,
  - telegram send failures,
  - queue pending/lag.

### Exit Criteria

- A new engineer can run and operate the system from docs only.

## Recommended Execution Order (Strict)

1. Phase 0 (scope freeze)
2. Phase 1 (runtime correctness)
3. Phase 2 (migrations + reliability)
4. Phase 3 (tests)
5. Phase 4 (deployment packaging)
6. Phase 5 (docs + ops)

## Risks and Mitigations

- Risk: Breaking current local scripts while unifying config.
  - Mitigation: keep temporary compatibility wrapper + smoke test both legacy and modular entrypoints.
- Risk: Migration rollout mismatch in existing DB.
  - Mitigation: baseline migration tested against snapshot DB before production rollout.
- Risk: Parser break due to Upwork markup changes.
  - Mitigation: fixture-based parser smoke tests and fallback selectors.

## Definition of Done (Project-Level)

- Multi-user bot/scheduler/worker path is canonical and stable.
- Config/env handling is deterministic and validated at startup.
- DB schema is migration-driven.
- Queue failure policy includes DLQ.
- Automated tests run in CI.
- Containerized deployment path exists.
- Docs and runbooks are complete and current.
