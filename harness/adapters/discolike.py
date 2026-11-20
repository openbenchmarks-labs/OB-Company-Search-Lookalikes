"""Discolike adapter: one /v1/discover request per seed, routed the way Discolike recommends."""
from __future__ import annotations

import time
from typing import Any

from ..models import RunResult, Seed, SkipConfig
from ..spec_runner import run_from_spec
from ..tool_specs import build_spec


def build_request(ctx: dict[str, Any]) -> None:
    """build_request: route the seed's inputs the way Discolike recommends.

    Domain + authored query -> domain lookalike steered by icp_prompt (the
    query is the identical string Exa and Parallel receive). Query only ->
    icp_prompt alone. Domain only -> domain lookalike, with and without
    auto_icp_text. Over-fetch so the cross-tool dedupe and seed removal
    still leave K rows."""
    config = ctx["config"]
    vars = ctx["vars"]
    seed = ctx["seed"]
    query = (seed.query or "").strip() or None
    if config.get("auto_icp_text") and (query or not seed.seed_domain):
        raise SkipConfig("auto_icp_text only applies to domain-only seeds")
    vars["max_records"] = int(ctx["k"] * float(config.get("over_fetch") or 1))
    vars["icp_prompt"] = query
    vars["auto_icp_text"] = "true" if config.get("auto_icp_text") else None


SPEC = build_spec("discolike", {
    "base_url": "https://api.discolike.com",
    "requires_seed_domain": False,
    "auth": [{"header": "x-discolike-key", "env": "DISCOLIKE_API_KEY"}],
    "request": {
        "method": "GET",
        "path": "/v1/discover",
        "query": {
            "domain": "$seed_domain",
            "max_records": "$max_records",
            "icp_prompt": "$icp_prompt",
            "auto_icp_text": "$auto_icp_text",
        },
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
    "hooks": {"build_request": build_request},   # input routing (domain / icp_prompt) + over-fetch
})
VENDOR_SLUG = SPEC.slug
VENDOR_NAME = SPEC.name
CONFIGS: list[dict[str, Any]] = SPEC.configs

# /v1/discover is rate-limited per API key over a moving 60s window (429) and
# answers 503 "please retry" while vector search is briefly unavailable; both
# wait and retry instead of failing the cell.
RETRYABLE_PREFIXES = ("HTTP 429", "HTTP 503")
MAX_ATTEMPTS = 6
BACKOFF_SEC = 20


def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    for _ in range(MAX_ATTEMPTS - 1):
        result = run_from_spec(SPEC, seed, k, config)
        if not (result.error or "").startswith(RETRYABLE_PREFIXES):
            return result
        time.sleep(BACKOFF_SEC)
    return run_from_spec(SPEC, seed, k, config)
