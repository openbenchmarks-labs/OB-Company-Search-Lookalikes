"""Parallel adapter: one entity search per seed.

`objective` builds the natural-language objective; `clean_aggregators` strips
www. and routes aggregator hosts (LinkedIn, Crunchbase, ...) out of the domain.
If the search returns nothing, `run` retries once with the concise query.
"""
from __future__ import annotations

import re
from typing import Any

from ..models import Candidate, RunResult, Seed
from ..spec_runner import run_from_spec
from ..tool_specs import build_spec

AGGREGATOR_HOSTS = {
    "linkedin.com",
    "tracxn.com",
    "platform.tracxn.com",
    "crunchbase.com",
    "wikipedia.org",
    "en.wikipedia.org",
    "bloomberg.com",
    "pitchbook.com",
}


def _embedded_website_domain(description: str | None) -> str | None:
    """Extract a vendor-supplied canonical website from its text payload."""
    if not description:
        return None
    match = re.search(r"\bWebsite\s+Url:\s*(https?://[^\s|]+)", description, re.IGNORECASE)
    if not match:
        return None
    host = match.group(1).rstrip(".,;:)]}").replace("https://", "").replace("http://", "").split("/")[0].lower()
    host = host[4:] if host.startswith("www.") else host
    if any(host == item or host.endswith("." + item) for item in AGGREGATOR_HOSTS):
        return None
    return host or None


def objective(ctx: dict[str, Any]) -> None:
    """build_request: seed-first lookalike objective. The seed (name + domain)
    is the input, one short clause disambiguates the entity — no feature
    enumerations, no similarity definition — and a return clause frames the
    output as companies a buyer would evaluate alongside the seed. What
    "similar" means is left for the vendor to derive from the seed, which is
    the behavior the benchmark measures."""
    seed: Seed = ctx["seed"]
    # An authored per-seed query wins outright — Exa and Parallel both read the
    # same field, so the two vendors keep receiving the identical string.
    if seed.query and ctx["config"].get("query_variant") != "concise_fallback":
        ctx["vars"]["query"] = seed.query.strip()
        return
    anchor = f"{seed.seed_name} ({seed.seed_domain})" if seed.seed_domain else seed.seed_name
    identity = (seed.short_description or seed.description or "").strip().rstrip(".!?")
    if ctx["config"].get("query_variant") == "concise_fallback":
        suffix = f" — {identity}" if identity else ""
        ctx["vars"]["query"] = f"companies like {anchor}{suffix}"
        return
    suffix = f", {identity}." if identity else "."
    ctx["vars"]["query"] = (
        f"Find companies similar to {anchor}{suffix} "
        f"Return companies a buyer would realistically evaluate alongside {seed.seed_name}."
    )


def clean_aggregators(raw_items: list[Any], ctx: dict[str, Any]) -> list[Candidate]:
    """transform_candidates: map entities to candidates, keeping aggregator
    pages out of the company `domain` field."""
    candidates: list[Candidate] = []
    for r in raw_items:
        if not isinstance(r, dict):
            continue
        ent = r.get("entity") if isinstance(r.get("entity"), dict) else r
        name = ent.get("name") or ent.get("entity_name") or ent.get("title") or ""
        raw_url = ent.get("url") or ent.get("website") or ent.get("domain")
        domain: str | None = None
        linkedin_url: str | None = None
        is_aggregator = False
        if isinstance(raw_url, str) and raw_url:
            stripped = (
                raw_url.replace("https://", "")
                .replace("http://", "")
                .split("/")[0]
                .lower()
            )
            stripped = stripped[4:] if stripped.startswith("www.") else stripped
            host = stripped
            if any(host == h or host.endswith("." + h) for h in AGGREGATOR_HOSTS):
                is_aggregator = True
                if "linkedin.com" in host:
                    linkedin_url = raw_url
                # else: keep raw_url in extra.source_url below
            else:
                domain = stripped
        description = (
            ent.get("description")
            or ent.get("snippet")
            or ent.get("summary")
            or r.get("description")
        )
        if not domain:
            domain = _embedded_website_domain(description)
        candidates.append(
            Candidate(
                name=str(name).strip(),
                domain=domain,
                description=description,
                extra={
                    "linkedin_url": linkedin_url,
                    "source_url": raw_url if is_aggregator and not linkedin_url else None,
                    "source_urls": r.get("source_urls") or r.get("citations"),
                    "confidence": r.get("confidence") or r.get("score"),
                },
            )
        )
    return candidates


SPEC = build_spec("parallel", {
    "base_url": "https://api.parallel.ai",
    "requires_seed_domain": False,
    "auth": [{"header": "x-api-key", "env": "PARALLEL_API_KEY"}],
    "request": {
        "method": "POST",
        "path": "/v1beta/findall/entity-search",
        "body": {
            "entity_type": "companies",
            "objective": "$query",
            "match_limit": "$k_min5",   # API floor is 5
        },
    },
    "response": {"candidates_path": ["results", "entities", "data"]},
    "hooks": {"build_request": objective, "transform_candidates": clean_aggregators},
})
VENDOR_SLUG = SPEC.slug
VENDOR_NAME = SPEC.name
CONFIGS: list[dict[str, Any]] = SPEC.configs


def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    primary = run_from_spec(SPEC, seed, k, config)
    # The expanded semantic request is preferred, but Entity Search sometimes
    # returns an empty set for broad/conglomerate seeds. Retry once with the
    # proven concise input rather than scoring an avoidable zero-result cell.
    if primary.error is None and not primary.candidates:
        fallback_config = {**config, "query_variant": "concise_fallback"}
        fallback = run_from_spec(SPEC, seed, k, fallback_config)
        fallback.config["fallback_used"] = True
        fallback.config["fallback_reason"] = "expanded_query_returned_zero_candidates"
        return fallback
    return primary
