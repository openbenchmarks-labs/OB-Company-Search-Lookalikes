"""CUFinder adapter: the Company Lookalikes Finder endpoint, a hard top-10."""
from __future__ import annotations

from typing import Any

from ..http_client import http_request, require_env
from ..models import Candidate, RunResult, Seed, take_top
from ..tool_specs import load_tool

TOOL = load_tool("cufinder")
VENDOR_SLUG = TOOL.tool
VENDOR_NAME = TOOL.name
CONFIGS: list[dict[str, Any]] = TOOL.configs
MAX_RESULTS = 10


def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    env = require_env("CUFINDER_API_KEY")
    status, payload, elapsed_ms = http_request(
        "POST",
        "https://api.cufinder.io/v2/fcl",
        headers={"x-api-key": env["CUFINDER_API_KEY"]},
        body={"query": seed.seed_domain or seed.seed_name},
        body_encoding="form",
    )
    if status >= 300:
        return RunResult(seed.seed_slug, VENDOR_SLUG, config["name"], config, [], elapsed_ms,
                         error=f"HTTP {status}: {str(payload)[:200]}", requested_k=k, max_results=MAX_RESULTS)
    rows = payload.get("data", {}).get("companies", []) if isinstance(payload, dict) else []
    candidates = [
        Candidate(
            name=str(row.get("name") or "").strip(),
            domain=row.get("domain") or row.get("website"),
            description=row.get("description"),
            extra={
                "linkedin_url": row.get("linkedin_url"), "industry": row.get("industry"),
                "employee_count": row.get("employee_count"), "size": row.get("size"),
                "country": row.get("country"), "confidence_level": payload.get("data", {}).get("confidence_level"),
            },
        )
        for row in rows if isinstance(row, dict)
    ]
    sent = len(rows)
    return RunResult(seed.seed_slug, VENDOR_SLUG, config["name"], config, take_top(candidates, min(k, MAX_RESULTS)),
                     elapsed_ms, cost_usd=round(TOOL.pricing.per_result_usd * min(sent, k, MAX_RESULTS), 6),
                     requested_k=k, max_results=MAX_RESULTS, sent_count=sent)
