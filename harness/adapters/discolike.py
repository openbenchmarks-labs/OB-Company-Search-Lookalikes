"""Discolike adapter: one domain-lookalike request per seed."""
from __future__ import annotations

from typing import Any

from ..models import RunResult, Seed
from ..spec_runner import run_from_spec
from ..tool_specs import build_spec

SPEC = build_spec("discolike", {
    "base_url": "https://api.discolike.com",
    "requires_seed_domain": True,
    "auth": [{"header": "x-discolike-key", "env": "DISCOLIKE_API_KEY"}],
    "request": {
        "method": "GET",
        "path": "/v1/discover",
        "query": {"domain": "$seed_domain", "max_records": "$k"},
    },
    "response": {
        "candidates_path": [],
        "item": {
            "name": "name",
            "domain": "domain",
            "description": "description",
            "rank": None,
            "extra": {
                "similarity": "similarity",
                "employees": "employees",
                "industry_groups": "industry_groups",
                "business_model": "business_model",
                "country": "address.country",
                "state": "address.state",
                "city": "address.city",
                "social_urls": "social_urls",
            },
        },
    },
})
VENDOR_SLUG = SPEC.slug
VENDOR_NAME = SPEC.name
CONFIGS: list[dict[str, Any]] = SPEC.configs


def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    return run_from_spec(SPEC, seed, k, config)
