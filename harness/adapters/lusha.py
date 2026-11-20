"""Lusha adapter. Not on the board: the lookalike endpoint needs 5-100 seeds per
request, and the benchmark scores one seed at a time. Kept for a future
multi-seed design."""
from __future__ import annotations

from typing import Any

from ..models import RunResult, Seed
from ..spec_runner import run_from_spec
from ..tool_specs import build_spec

SPEC = build_spec("lusha", {
    "base_url": "https://api.lusha.com",
    "requires_seed_domain": True,
    "auth": [{"header": "api_key", "env": "LUSHA_API_KEY"}],
    "request": {
        "method": "POST",
        "path": "/v3/companies/lookalike",
        "body": {"seeds": {"domains": ["$seed_domain"]}, "limit": "$k"},
    },
    "response": {
        "candidates_path": ["results", "companies"],
        "item": {
            "name": ["name", "company_name"],
            "domain": ["domain", "website"],
            "description": ["description", "about"],
            "rank": None,
            "extra": {
                "lusha_id": "id",
                "industry": "industry",
                "country": ["location.country", "country"],
                "city": "location.city",
                "employees": ["employeeCount.exact", "employeeCount"],
                "linkedin": "socialLinks.linkedin",
            },
        },
    },
    "audit": {
        "credits_charged": "billing.creditsCharged",
        "results_returned": "billing.resultsReturned",
    },
})
VENDOR_SLUG = SPEC.slug
VENDOR_NAME = SPEC.name
CONFIGS: list[dict[str, Any]] = SPEC.configs


def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    return run_from_spec(SPEC, seed, k, config)
