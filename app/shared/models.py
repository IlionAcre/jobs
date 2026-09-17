from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

# ============================================================
# DOMAIN MODELS
# ============================================================

_WS_RE = re.compile(r"\s+")


def clean_text(s: Optional[str]) -> Optional[str]:
    """Normalize whitespace and strip. Returns None for empty-ish values."""
    if s is None:
        return None
    s2 = _WS_RE.sub(" ", str(s)).strip()
    return s2 or None


@dataclass(frozen=True, slots=True)
class JobTile:
    """
    Minimal representation of a single Upwork search result tile.
    """

    title: Optional[str]
    url: Optional[str]
    snippet: Optional[str]
    budget: Optional[str]
    hourly_range: Optional[str]
    posted: Optional[str]
    location: Optional[str]
    job_type: Optional[str]
    experience_level: Optional[str]
    duration: Optional[str]
    tags: tuple[str, ...]

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "JobTile":
        tags_val = raw.get("tags") or ()
        if isinstance(tags_val, str):
            tags = (tags_val,)
        else:
            tags = tuple(str(x) for x in tags_val)

        return JobTile(
            title=clean_text(raw.get("title")),
            url=clean_text(raw.get("url")),
            snippet=clean_text(raw.get("snippet")),
            budget=clean_text(raw.get("budget")),
            hourly_range=clean_text(raw.get("hourly_range")),
            posted=clean_text(raw.get("posted")),
            location=clean_text(raw.get("location")),
            job_type=clean_text(raw.get("job_type")),
            experience_level=clean_text(raw.get("experience_level")),
            duration=clean_text(raw.get("duration")),
            tags=tags,
        )


__all__ = ["JobTile", "clean_text"]
