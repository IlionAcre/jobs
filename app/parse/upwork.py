from __future__ import annotations

import html as htmlmod
import re
from dataclasses import MISSING
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from lxml import html as lhtml

from app.shared.models import JobTile

BASE_URL = "https://www.upwork.com"
_WS_RE = re.compile(r"\s+")


def clean_text(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = htmlmod.unescape(s)
    s = _WS_RE.sub(" ", str(s)).strip()
    return s or None


def xpath_text(node: Any, xpath: str) -> Optional[str]:
    found = node.xpath(xpath)
    if not found:
        return None

    item = found[0]
    if hasattr(item, "xpath"):
        raw = "".join(item.xpath(".//text()"))
    else:
        raw = str(item)

    return clean_text(raw)


def data_attrs(node: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if hasattr(node, "items"):
        for k, v in node.items():
            if k.startswith("data-"):
                out[k] = v
    return out


def parse_budget_text(info_text: Optional[str]) -> Dict[str, Optional[str]]:
    """
    Examples:
      - "Est. budget: $100.00"
      - "Hourly: $20.00 - $40.00"
    """
    if not info_text:
        return {"budget_label": None, "budget_amount": None, "hourly_range": None}

    t = info_text.strip()

    if "Est. budget:" in t:
        amount = clean_text(t.split("Est. budget:", 1)[1])
        return {"budget_label": "Est. budget", "budget_amount": amount, "hourly_range": None}

    if t.startswith("Hourly:"):
        hourly_range = clean_text(t.replace("Hourly:", "", 1))
        return {"budget_label": "Hourly", "budget_amount": None, "hourly_range": hourly_range}

    return {"budget_label": None, "budget_amount": None, "hourly_range": None}


def parse_est_time_text(info_text: Optional[str]) -> Optional[str]:
    """
    Example:
      - "Est. time: 1 to 3 months, Less than 30 hrs/week"
    """
    if not info_text:
        return None
    if "Est. time:" in info_text:
        return clean_text(info_text.split("Est. time:", 1)[1])
    return clean_text(info_text)


def best_effort_location(art: Any) -> Optional[str]:
    """
    Upwork markup changes often; this tries a few likely selectors.
    Returns None if not found.
    """
    # Common patterns seen in tiles (best-effort)
    candidates = [
        './/*[@data-test="client-location"]//text()',
        './/*[@data-test="client-country"]//text()',
        './/*[@data-test="location"]//text()',
        './/*[contains(@data-test,"location")]//text()',
    ]
    for xp in candidates:
        parts = art.xpath(xp)
        txt = clean_text(" ".join(parts)) if parts else None
        if txt:
            return txt
    return None


def make_jobtile(**candidate: Any) -> JobTile:
    """
    Adapt to the actual JobTile dataclass fields and required args.
    - Keep only known fields
    - Fill any required-but-missing fields with None / []
    """
    fields = getattr(JobTile, "__dataclass_fields__", None)
    if not fields:
        # fallback (unlikely): just try
        return JobTile(**candidate)  # type: ignore[arg-type]

    allowed = set(fields.keys())
    payload = {k: v for k, v in candidate.items() if k in allowed}

    # Fill required fields that have no defaults
    for name, f in fields.items():
        has_default = not (f.default is MISSING and f.default_factory is MISSING)  # type: ignore
        if not has_default and name not in payload:
            if name in {"tags", "tokens"}:
                payload[name] = []
            else:
                payload[name] = None

    return JobTile(**payload)  # type: ignore[arg-type]


def parse_jobs(html_text: str, *, base_url: str = BASE_URL) -> List[JobTile]:
    """
    Parse Upwork job tiles from an Upwork search HTML page (nx/search/jobs).
    Returns a list[JobTile] (generic model).
    """
    root = lhtml.fromstring(html_text)
    articles = root.xpath('//article[@data-test="JobTile"]')

    jobs: List[JobTile] = []

    for art in articles:
        job_uid = art.get("data-ev-job-uid") or art.get("data-test-key")

        # Posted: "Posted 33 seconds ago"
        posted_parts = art.xpath('.//small[@data-test="job-pubilshed-date"]//span/text()')
        posted = clean_text(" ".join(posted_parts)) if posted_parts else None

        # Title + URL
        title: Optional[str] = None
        href: Optional[str] = None
        job_url: Optional[str] = None

        title_a = art.xpath('.//a[contains(@data-test,"job-tile-title-link")]')
        if title_a:
            a = title_a[0]
            title = clean_text("".join(a.xpath(".//text()")))
            href = (a.get("href") or "").strip() or None
            job_url = urljoin(base_url, href) if href else None

        # Info list (Fixed/Hourly, level, budget/time)
        info_map: Dict[str, str] = {}
        for li in art.xpath('.//ul[@data-test="JobInfo"]/li'):
            k = (li.get("data-test") or "").strip()
            v = clean_text("".join(li.xpath(".//text()")))
            if k and v:
                info_map[k] = v

        job_type_text = info_map.get("job-type-label")
        experience_level = info_map.get("experience-level")
        fixed_price_text = info_map.get("is-fixed-price")  # "Est. budget: $100.00"
        duration_text = info_map.get("duration-label")     # "Est. time: ..."

        budget_bits = parse_budget_text(fixed_price_text or job_type_text)
        est_time = parse_est_time_text(duration_text)

        # Description (snippet)
        description = xpath_text(art, './/div[@data-test="UpCLineClamp JobDescription"]//p')

        # Tokens -> tags
        raw_tokens = [
            clean_text("".join(span.xpath(".//text()")))
            for span in art.xpath('.//div[@data-test="TokenClamp JobAttrs"]//button[@data-test="token"]//span')
        ]
        tokens = [t for t in raw_tokens if t]

        # Generic fields expected by your JobTile
        url = job_url
        snippet = description
        budget = budget_bits.get("budget_amount") or budget_bits.get("hourly_range")

        location = best_effort_location(art)
        job_type = job_type_text
        duration = est_time or duration_text
        tags = tokens

        jobs.append(
            make_jobtile(
                # generic JobTile fields (these are the ones your model complained about)
                title=title,
                url=url,
                snippet=snippet,
                budget=budget,
                location=location,
                job_type=job_type,
                duration=duration,
                tags=tags,
                # extra info (only used if your JobTile also has these fields)
                posted=posted,
                hourly_range=budget_bits.get("hourly_range"),
                experience_level=experience_level,
                # upwork-specific identifiers (kept only if your JobTile includes them)
                job_uid=job_uid,
                href=href,
                job_url=job_url,
            )
        )

    return jobs
