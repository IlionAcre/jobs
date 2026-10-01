"""
Per-subscription relevance rules.

Upwork matches search terms loosely (word stems, skill tags), so a search for "scraping" also returns a
legal job mentioning "alloy scrap". A subscription can narrow that:

  include_words  at least one must appear as a whole word (or phrase) in title + description + skills
  exclude_words  none may appear

Both empty = no filtering (deliver whatever Upwork returned). Matching is case-insensitive.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Sequence

from app.scraper.models import Job


@lru_cache(maxsize=512)
def _pattern(word: str) -> re.Pattern:
    # \b fails next to non-word characters ("c++", ".net"), so use explicit "not a word char" guards.
    return re.compile(rf"(?<!\w){re.escape(word.strip().lower())}(?!\w)")


def haystack(job: Job) -> str:
    return " \n ".join(filter(None, [job.title, job.description, " | ".join(job.skills)])).lower()


def matches(job: Job, include_words: Sequence[str] = (), exclude_words: Sequence[str] = ()) -> bool:
    include = [w for w in include_words if w and w.strip()]
    exclude = [w for w in exclude_words if w and w.strip()]
    if not include and not exclude:
        return True
    text = haystack(job)
    if include and not any(_pattern(w).search(text) for w in include):
        return False
    return not any(_pattern(w).search(text) for w in exclude)
