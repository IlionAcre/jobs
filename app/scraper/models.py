from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

PLATFORM_UPWORK = "upwork"


@dataclass(frozen=True, slots=True)
class JobRef:
    """What the cheap first-tier poll returns: just enough to tell whether a job is new."""

    job_id: str  # Upwork "ciphertext", e.g. "~022105189873338672110"
    publish_time: Optional[datetime]


@dataclass(frozen=True, slots=True)
class Job:
    job_id: str
    url: str
    title: Optional[str]
    description: Optional[str]
    skills: Tuple[str, ...]
    job_type: Optional[str]  # "fixed" | "hourly"
    fixed_amount: Optional[float]
    hourly_min: Optional[float]
    hourly_max: Optional[float]
    tier: Optional[str]  # "entry" | "intermediate" | "expert"
    duration: Optional[str]
    workload: Optional[str]
    create_time: Optional[datetime]
    publish_time: Optional[datetime]
    raw: Dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    platform: str = PLATFORM_UPWORK

    @property
    def ref(self) -> JobRef:
        return JobRef(self.job_id, self.publish_time)


@dataclass(frozen=True, slots=True)
class SearchSpec:
    """One distinct search, polled once however many chats subscribe to it."""

    search_id: int
    query_text: str
    platform: str = PLATFORM_UPWORK


@dataclass(frozen=True, slots=True)
class Token:
    value: str
    minted_at: float  # epoch seconds
    minter: str  # name of the chain step that produced it
    egress_id: str = "default"  # which outbound IP it was minted from

    def age_s(self, now: float) -> float:
        return now - self.minted_at
