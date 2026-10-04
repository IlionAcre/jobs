"""
Our own reading of an Upwork search query, applied to a stored job.

Used to answer: if we collected every job and matched locally, would we catch what Upwork's search returns?
Deliberately literal (whole words, no stemming, no synonyms), so the comparison shows exactly where Upwork's
engine differs from plain matching.

Syntax understood (Upwork's documented operators):
  python OR scraping      either word
  python scraping         both words (a space is AND; "AND" is accepted too)
  "web scraping"          the exact phrase
  python -wordpress       NOT (also: NOT wordpress)
  (a OR b) c              one level of parentheses
"""
from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from app.scraper.models import Job

_TOKEN = re.compile(r'"[^"]*"|\(|\)|[^\s()]+')

Term = Tuple[bool, str]          # (negated, phrase)
Clause = List[List[Term]]        # OR of AND-groups


def _terms(tokens: Sequence[str]) -> Clause:
    groups: Clause = [[]]
    negate = False
    for tok in tokens:
        upper = tok.upper()
        if upper == "OR":
            groups.append([])
            continue
        if upper == "AND":
            continue
        if upper == "NOT":
            negate = True
            continue
        if tok.startswith("-") and len(tok) > 1:
            negate, tok = True, tok[1:]
        phrase = tok.strip('"').strip().lower()
        if phrase:
            groups[-1].append((negate, phrase))
        negate = False
    return [g for g in groups if g]


def parse_query(query: str) -> List[Clause]:
    """A query is an AND of parts; each part is either a bare clause or a parenthesised clause."""
    tokens = _TOKEN.findall(query or "")
    parts: List[Clause] = []
    current: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "(":
            if current:
                parts.append(_terms(current))
                current = []
            j = tokens.index(")", i) if ")" in tokens[i:] else len(tokens)
            parts.append(_terms(tokens[i + 1:j]))
            i = j + 1
            continue
        current.append(tok)
        i += 1
    if current:
        parts.append(_terms(current))
    return [p for p in parts if p]


def job_text(job: Job) -> str:
    return " ".join(x for x in [job.title or "", job.description or "", " ".join(job.skills)] if x).lower()


def _has(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


def matches_query(query: str, text: str) -> bool:
    """True if `text` (already lower-cased) satisfies the query. An empty query matches everything."""
    for clause in parse_query(query):
        if not any(all(_has(text, p) != neg for neg, p in group) for group in clause):
            return False
    return True
