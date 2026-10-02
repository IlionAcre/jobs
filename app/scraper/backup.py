"""
Database backups: `python main.py scraper backup`

One compressed `pg_dump` of the whole database per run (run daily by the OS scheduler), old dumps pruned
after `backup.keep_days`. Optionally each dump is also copied to a second folder (`backup.copy_to`), e.g. one
that a cloud-drive client syncs, which is what protects against losing this disk.

Restore into an empty database:
    pg_restore --clean --if-exists --no-owner -d <database> <file>.dump
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from app.scraper.config import REPO_ROOT, BackupConfig

log = logging.getLogger("scraper.backup")

PREFIX, SUFFIX = "upwork-", ".dump"
KEEP_AT_LEAST = 3  # never prune below this many dumps, however old they are

Run = Callable[..., "subprocess.CompletedProcess[str]"]


def backup_dir(config: BackupConfig) -> Path:
    return Path(config.dir).expanduser() if config.dir else REPO_ROOT.parent / f"{REPO_ROOT.name}-backups"


def list_dumps(directory: Path) -> List[Path]:
    """Finished dumps, oldest first."""
    if not directory.is_dir():
        return []
    return sorted((p for p in directory.glob(f"{PREFIX}*{SUFFIX}") if p.is_file()), key=lambda p: p.stat().st_mtime)


def latest_backup_age_s(config: BackupConfig, now: Optional[float] = None) -> Optional[float]:
    """Seconds since the newest dump was written; None if there is none."""
    dumps = list_dumps(backup_dir(config))
    return None if not dumps else (now if now is not None else time.time()) - dumps[-1].stat().st_mtime


def prune(directory: Path, keep_days: float, now: Optional[float] = None) -> List[Path]:
    """Delete dumps older than `keep_days`, always keeping the newest KEEP_AT_LEAST. Returns what was deleted."""
    now = now if now is not None else time.time()
    dumps = list_dumps(directory)
    candidates = dumps[:-KEEP_AT_LEAST] if len(dumps) > KEEP_AT_LEAST else []
    deleted = []
    for path in candidates:
        if now - path.stat().st_mtime > keep_days * 86400:
            path.unlink()
            deleted.append(path)
    return deleted


def _pg_tool(name: str, config: BackupConfig) -> str:
    return str(Path(config.pg_bin_dir) / name) if config.pg_bin_dir else name


def run_backup(config: BackupConfig, env: Mapping[str, str], *, run: Run = subprocess.run,
               now: Optional[datetime] = None) -> Dict[str, object]:
    """Dump, verify, prune, copy. Raises on failure (the caller logs and exits non-zero)."""
    directory = backup_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    final = directory / f"{PREFIX}{stamp}{SUFFIX}"
    partial = final.with_suffix(".part")  # an interrupted run never leaves something that looks like a backup

    pg_env = {**os.environ, "PGPASSWORD": env["DB_PASSWORD"]}
    connection: Sequence[str] = ["-h", env.get("DB_HOST", "localhost"), "-p", env.get("DB_PORT", "5432"), "-U", env["DB_USER"]]
    try:
        run([_pg_tool("pg_dump", config), *connection, "-Fc", "--no-password", "-f", str(partial), env["DB_NAME"]],
            env=pg_env, check=True, capture_output=True, text=True, timeout=config.timeout_s)
        # A dump that pg_restore cannot list is not a backup.
        run([_pg_tool("pg_restore", config), "--list", str(partial)],
            env=pg_env, check=True, capture_output=True, text=True, timeout=config.timeout_s)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(final)

    deleted = prune(directory, config.keep_days)
    copied = None
    if config.copy_to:
        target = Path(config.copy_to).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        copied = target / final.name
        shutil.copy2(final, copied)
        prune(target, config.keep_days)
    result = {"file": str(final), "bytes": final.stat().st_size, "pruned": len(deleted), "copied_to": str(copied) if copied else None}
    log.info("backup_done", extra=result)
    return result
