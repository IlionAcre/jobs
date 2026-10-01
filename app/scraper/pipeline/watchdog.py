from __future__ import annotations

import logging
import time
from typing import Callable, Dict, List

from app.scraper.config import ScraperConfig
from app.scraper.health import CRITICAL, HealthSnapshot, Problem, evaluate

log = logging.getLogger("scraper.watchdog")

Send = Callable[[str], None]


def _duration(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60} min"


class Watchdog:
    """Tells a human when the pipeline is unhealthy, and when it is healthy again.

    One message when a problem opens, a reminder every `repeat_after_minutes` while it stays open, and one
    message when it resolves. Nothing is sent while everything is fine, so silence means "working" only as
    long as the watchdog itself is running (the dashboard and `scraper status` show whether it is).
    """

    def __init__(self, collect: Callable[[], HealthSnapshot], config: ScraperConfig, send: Send, *,
                 clock: Callable[[], float] = time.time) -> None:
        self._collect = collect
        self._cfg = config
        self._send = send
        self._clock = clock
        self._open: Dict[str, Dict] = {}  # key -> {"problem", "since", "notified_at"}

    def tick(self) -> List[str]:
        """One check. Returns the messages sent (for tests and logs)."""
        now = self._clock()
        current = {p.key: p for p in evaluate(self._collect(), self._cfg)}
        opened: List[Problem] = []
        reminders: List[Problem] = []
        resolved: List[str] = []

        for key, problem in current.items():
            state = self._open.get(key)
            if state is None:
                self._open[key] = {"problem": problem, "since": now, "notified_at": now}
                opened.append(problem)
            else:
                state["problem"] = problem
                if now - state["notified_at"] >= self._cfg.alerts.repeat_after_minutes * 60:
                    state["notified_at"] = now
                    reminders.append(problem)
        for key in [k for k in self._open if k not in current]:
            state = self._open.pop(key)
            problem = state["problem"]
            resolved.append(f"{problem.title or problem.message} (was open for {_duration(now - state['since'])})")

        messages: List[str] = []
        if opened:
            icon = "🚨" if any(p.severity == CRITICAL for p in opened) else "⚠️"
            messages.append(f"{icon} Scraper problem\n" + "\n".join(f"• {p.message}" for p in opened))
        if reminders:
            lines = [f"• {p.message} (open for {_duration(now - self._open[p.key]['since'])})" for p in reminders]
            messages.append("⏰ Still open\n" + "\n".join(lines))
        if resolved:
            messages.append("✅ Resolved\n" + "\n".join(f"• {r}" for r in resolved))

        for text in messages:
            try:
                self._send(text)
            except Exception:  # noqa: BLE001
                log.exception("watchdog_send_failed")
        for p in opened:
            log.warning("health_problem_opened", extra={"key": p.key, "severity": p.severity, "problem": p.message})
        if resolved:
            log.info("health_problems_resolved", extra={"resolved": resolved})
        return messages

    def run_forever(self, stop: Callable[[], bool] = lambda: False, on_loop: Callable[[], None] = lambda: None) -> None:
        log.info("watchdog_ready", extra={"check_interval_s": self._cfg.alerts.check_interval_s})
        # Give the other roles time to start before judging them.
        next_check = time.monotonic() + min(45.0, self._cfg.alerts.check_interval_s)
        while not stop():
            on_loop()
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + self._cfg.alerts.check_interval_s
                try:
                    self.tick()
                except Exception:  # noqa: BLE001
                    log.exception("watchdog_tick_failed")
            time.sleep(2)
