"""
GraphQL for Upwork's visitor job search. The endpoint accepts any field selection, so we ask for
exactly what each tier needs (research/upwork_recon/04, "how light can a poll be").
"""
from __future__ import annotations

import json

_WRAP = (
    "query VisitorJobSearch($requestVariables: VisitorJobSearchV1Request!) { search { universalSearchNuxt { "
    "visitorJobSearchV1(request: $requestVariables) { results { %s } } } } }"
)

# Tier 1: is anything new? ~330 bytes on the wire for 10 jobs.
IDS_SELECTION = "jobTile { job { ciphertext: cipherText publishTime } }"

# Tier 2: everything an alert and the jobs table need. The description is what makes this big.
DETAILS_SELECTION = (
    "title description ontologySkills { prefLabel } "
    "jobTile { job { ciphertext: cipherText jobType hourlyBudgetMin hourlyBudgetMax hourlyEngagementType "
    "contractorTier createTime publishTime hourlyEngagementDuration { label } "
    "fixedPriceAmount { amount } fixedPriceEngagementDuration { label } } }"
)


def build_body(query_text: str, *, count: int, selection: str) -> str:
    return json.dumps({
        "query": _WRAP % selection,
        "variables": {"requestVariables": {
            "userQuery": query_text,
            "sort": "recency",
            "highlight": False,  # True wraps matches in H^...^H markers
            "paging": {"offset": 0, "count": count},
        }},
    })
