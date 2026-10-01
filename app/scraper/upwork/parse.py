"""JSON from the search API -> JobRef / Job. Pure functions; no I/O."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.scraper.errors import AuthExpired, Transient
from app.scraper.models import Job, JobRef

_RESULTS_PATH = ("data", "search", "universalSearchNuxt", "visitorJobSearchV1", "results")
_AUTH_WORDS = ("auth", "token", "unauthor", "permission", "forbidden")
_WS = re.compile(r"\s+")
_HIGHLIGHT = re.compile(r"H\^|\^H")
_TIERS = {"EntryLevel": "entry", "IntermediateLevel": "intermediate", "ExpertLevel": "expert"}
_WORKLOAD = {"PART_TIME": "Less than 30 hrs/week", "FULL_TIME": "More than 30 hrs/week"}


def _results(text: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(text)
    except ValueError as ex:
        raise Transient(f"search response is not JSON: {text[:120]!r}") from ex
    node: Any = data
    for key in _RESULTS_PATH:
        node = node.get(key) if isinstance(node, dict) else None
    if isinstance(node, list):
        return node
    detail = json.dumps(data.get("errors") if isinstance(data, dict) and data.get("errors") else data)[:300]
    if any(word in detail.lower() for word in _AUTH_WORDS):
        raise AuthExpired(detail)
    raise Transient(f"search response has no results: {detail}")


def _time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _WS.sub(" ", _HIGHLIGHT.sub("", value)).strip() or None


def parse_refs(text: str) -> List[JobRef]:
    refs = []
    for item in _results(text):
        job = (item.get("jobTile") or {}).get("job") or {}
        if job.get("ciphertext"):
            refs.append(JobRef(job["ciphertext"], _time(job.get("publishTime"))))
    return refs


def parse_jobs(text: str, *, job_url_template: str) -> List[Job]:
    jobs = []
    for item in _results(text):
        job = (item.get("jobTile") or {}).get("job") or {}
        job_id = job.get("ciphertext")
        if not job_id:
            continue
        hourly = job.get("jobType") == "HOURLY"
        duration = (job.get("hourlyEngagementDuration" if hourly else "fixedPriceEngagementDuration") or {}).get("label")
        jobs.append(Job(
            job_id=job_id,
            url=job_url_template.format(job_id=job_id),
            title=_clean(item.get("title")),
            description=_clean(item.get("description")),
            skills=tuple(s["prefLabel"] for s in (item.get("ontologySkills") or []) if s.get("prefLabel")),
            job_type="hourly" if hourly else "fixed",
            fixed_amount=None if hourly else _num((job.get("fixedPriceAmount") or {}).get("amount")),
            hourly_min=_num(job.get("hourlyBudgetMin")) if hourly else None,
            hourly_max=_num(job.get("hourlyBudgetMax")) if hourly else None,
            tier=_TIERS.get(job.get("contractorTier") or ""),
            duration=duration,
            workload=_WORKLOAD.get(job.get("hourlyEngagementType") or ""),
            create_time=_time(job.get("createTime")),
            publish_time=_time(job.get("publishTime")),
            raw=item,
        ))
    return jobs
