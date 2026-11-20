"""Extruct adapter: one domain-seeded lookalike request per seed."""
from __future__ import annotations

from typing import Any

from ..models import RunResult, Seed
from ..spec_runner import run_from_spec
from ..tool_specs import build_spec

SPEC = build_spec("extruct", {
    "base_url": "https://api.extruct.ai",
    "requires_seed_domain": True,
    "auth": [{"header": "Authorization", "env": "EXTRUCT_API_TOKEN", "value_prefix": "Bearer "}],
    "request": {
        "method": "POST",
        "path": "/v1/companies/$seed_domain/similar",
        "body": {"pagination": {"offset": 0, "limit": "$k"}},
    },
    "response": {
        "candidates_path": ["results"],
        "item": {
            "name": "name",
            "domain": "domain",
            "description": ["short_description", "full_description"],
            "rank": None,
            "extra": {
                "id": "id",
                "full_description": "full_description",
                "products_services": "products_services",
                "use_cases": "use_cases",
                "target_audience": "target_audience",
                "industries": "industries",
                "employee_count": "employee_count",
            },
        },
    },
})
VENDOR_SLUG = SPEC.slug
VENDOR_NAME = SPEC.name
CONFIGS: list[dict[str, Any]] = SPEC.configs


def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    return run_from_spec(SPEC, seed, k, config)
