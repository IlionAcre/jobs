"""
Minters: each knows one way to obtain a search token. They raise the standard error types so the
chain can apply one failure policy to all of them.

  HttpMinter        GET the search page with an HTTP client, read the token cookie (~1 s, no browser)
  SubprocessMinter  run an external script in its own virtualenv (browser tools and libraries whose
                    dependencies conflict with ours); protocol: the script prints one line
                    {"result": {"ok": bool, "token": str|null, ...}}
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Protocol
from urllib.parse import urlencode

import psutil

from app.scraper.config import REPO_ROOT, MinterConfig, UpworkConfig
from app.scraper.errors import Challenged, RateLimited, Transient
from app.scraper.transports.base import Transport, raise_for_status

log = logging.getLogger("scraper.mint")

# Any search works; the token is not tied to the query.
_MINT_QUERY = {"nav_dir": "pop", "q": "python", "sort": "recency"}


def mint_url(upwork: UpworkConfig) -> str:
    return f"{upwork.search_page_url}?{urlencode(_MINT_QUERY)}"


class Minter(Protocol):
    name: str

    def mint(self) -> str: ...


def _valid(token: Optional[str]) -> bool:
    return bool(token) and token.startswith("oauth2v2_")


class HttpMinter:
    def __init__(self, name: str, transport: Transport, upwork: UpworkConfig) -> None:
        self.name = name
        self._transport = transport
        self._upwork = upwork

    def mint(self) -> str:
        resp = raise_for_status(self._transport.get_page(mint_url(self._upwork)), f"mint via {self.name}")
        token = resp.cookies.get(self._upwork.token_cookie)
        if not _valid(token):
            raise Transient(f"mint via {self.name}: page loaded but no {self._upwork.token_cookie} cookie")
        return token


def _interpreter(python: str) -> Path:
    """`python` in the config is a venv directory or an interpreter path, relative to the repo root."""
    path = Path(python)
    path = path if path.is_absolute() else REPO_ROOT / path
    if path.is_dir():
        path = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return path


class SubprocessMinter:
    def __init__(self, cfg: MinterConfig, upwork: UpworkConfig) -> None:
        self.name = cfg.name
        self._cfg = cfg
        self._upwork = upwork

    def mint(self) -> str:
        last: Exception = Transient(f"mint via {self.name}: not attempted")
        for attempt in range(1, self._cfg.attempts + 1):
            try:
                return self._run_once()
            except RateLimited:
                raise
            except (Challenged, Transient) as ex:
                last = ex
                log.info("subprocess_mint_attempt_failed", extra={"minter": self.name, "attempt": attempt, "error": str(ex)[:200]})
        raise last

    def _run_once(self) -> str:
        need = self._cfg.min_free_mem_mb
        if need is not None:
            free = psutil.virtual_memory().available // 2**20
            if free < need:
                raise Transient(f"mint via {self.name}: only {free} MB free, needs {need} MB")

        interpreter = _interpreter(self._cfg.python or sys.executable)
        script = Path(self._cfg.script or "")
        script = script if script.is_absolute() else REPO_ROOT / script
        if not interpreter.exists() or not script.exists():
            raise Transient(f"mint via {self.name}: missing interpreter or script ({interpreter}, {script})")

        cmd = [str(interpreter), "-u", str(script), "--url", mint_url(self._upwork), *self._cfg.args]
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=self._cfg.timeout_s, cwd=str(REPO_ROOT))
        except subprocess.TimeoutExpired as ex:
            raise Transient(f"mint via {self.name}: timed out after {self._cfg.timeout_s}s") from ex

        result = self._parse(done.stdout)
        if result is None:
            raise Transient(f"mint via {self.name}: no result line (exit {done.returncode}): {done.stderr[-200:]!r}")
        token = result.get("token")
        if result.get("ok") and _valid(token):
            return token
        if result.get("status") == 429:
            raise RateLimited(f"mint via {self.name}: HTTP 429")
        if result.get("status") == 403 or result.get("challenge_seen") or result.get("challenge_types"):
            raise Challenged(f"mint via {self.name}: challenged")
        raise Transient(f"mint via {self.name}: {str(result.get('error') or result)[:200]}")

    @staticmethod
    def _parse(stdout: str) -> Optional[Dict[str, Any]]:
        for line in reversed(stdout.splitlines()):
            if line.startswith('{"result"'):
                try:
                    return json.loads(line)["result"]
                except (ValueError, KeyError):
                    return None
        return None
