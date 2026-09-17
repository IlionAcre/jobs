# ADR 0001: Canonical Runtime Path

## Status
Accepted

## Context
The repository currently includes legacy single-process scripts and a modular multi-user pipeline.

## Decision
Production runtime path is:

- `python main.py bot`
- `python main.py scheduler`
- `python main.py worker`

Legacy root scripts (`monitor_uw.py`, `monitor_uw_cm.py`) are debug/local tools only.

## Consequences
- New multi-user features should be implemented in `app/` services only.
- Deployment packaging and runbooks should target bot/scheduler/worker services.
- Legacy scripts can remain for troubleshooting, but are not deployment entrypoints.
