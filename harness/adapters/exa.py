"""Exa adapter: one semantic company search per seed.

`company_query` builds the seed-first query and over-fetches numResults;
`dedupe_by_domain` collapses page hits to one candidate per domain, sorted by score.
If the query returns nothing, `run` retries once with the concise query.

Edge-case handling:
- Malformed or non-string URLs are skipped cleanly in dedupe_by_domain.
- Aggregator domain matching is explicit (exact host or subdomain), not substring.
- Score comparisons guard against non-numeric values via float() coercion.
- The fallback is only used when the primary run succeeds (no error) but returns
  zero candidates, so network/auth failures are not silently retried.
"""
from __future__ import annotations

import logging
from typing import Any

from ..models import Candidate, RunResult, Seed
from ..spec_runner import run_from_spec
from ..tool_specs import build_spec

log = logging.getLogger(__name__)

AGGREGATOR_HOSTS = {
    "linkedin.com", "tracxn.com", "platform.tracxn.com", "crunchbase.com",
    "wikipedia.org", "en.wikipedia.org", "bloomberg.com", "pitchbook.com",
}


def company_query(ctx: dict[str, Any]) -> None:
    """build_request: seed-first lookalike query. The seed (name + domain) is
    the input, one short clause disambiguates the entity — no feature
    enumerations, no similarity definition — and a return clause frames the
    output as companies a buyer would evaluate alongside the seed. What
    "similar" means is left for the vendor to derive from the seed, which is
    the behavior the benchmark measures."""
    k = ctx["k"]
    seed = ctx["seed"]
    config = ctx["config"]
    ctx["vars"]["num_results"] = min(k * int(config.get("over_fetch") or 1), 100)
    # An authored per-seed query wins outright — Exa and Parallel both read the
    # same field, so the two vendors keep receiving the identical string.
    if seed.query and config.get("query_variant") != "concise_fallback":
        ctx["vars"]["query"] = seed.query.strip()
        return
    anchor = f"{seed.seed_name} ({seed.seed_domain})" if seed.seed_domain else seed.seed_name
    identity = (seed.short_description or seed.description or "").strip().rstrip(".!?")
    if config.get("query_variant") == "concise_fallback":
        suffix = f" — {identity}" if identity else ""
        ctx["vars"]["query"] = f"companies like {anchor}{suffix}"
        return
    suffix = f", {identity}." if identity else "."
    ctx["vars"]["query"] = (
        f"Find companies similar to {anchor}{suffix} "
        f"Return companies a buyer would realistically evaluate alongside {seed.seed_name}."
    )


def _company_entity(item: dict[str, Any]) -> dict[str, Any] | None:
    for entity in item.get("entities") or []:
        if isinstance(entity, dict) and entity.get("type") == "company":
            props = entity.get("properties")
            return props if isinstance(props, dict) else {}
    return None


def _safe_score(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def dedupe_by_domain(raw_items: list[Any], ctx: dict[str, Any]) -> list[Candidate]:
    """transform_candidates: collapse page hits to one candidate per domain
    (highest score), then sort by score DESC.

    Robustness notes:
    - Non-dict items are skipped (Exa occasionally wraps results in extra layers).
    - Non-string or empty URLs are skipped rather than causing an AttributeError.
    - Scores are coerced to float with a 0.0 fallback; non-numeric values (e.g.
      null, missing key) are treated as 0 and do not raise.
    - Aggregator matching uses exact host equality or subdomain suffix — not a
      substring — so "mylinkedin.com" is never mislabelled as an aggregator.
    """
    by_domain: dict[str, tuple[dict[str, Any], str | None]] = {}
    for r in raw_items:
        if not isinstance(r, dict):
            log.debug("exa: skipping non-dict result item: %r", type(r))
            continue
        u = r.get("url")
        if not isinstance(u, str) or not u.strip():
            continue
        host = u.replace("https://", "").replace("http://", "").split("/")[0].lower().strip()
        host = host[4:] if host.startswith("www.") else host
        if not host:
            continue
        is_aggregator = any(host == agg or host.endswith("." + agg) for agg in AGGREGATOR_HOSTS)
        domain = None if is_aggregator else host
        key = domain or f"url:{u.lower()}"
        existing = by_domain.get(key)
        if existing is None or _safe_score(r.get("score")) > _safe_score(existing[0].get("score")):
            by_domain[key] = (r, domain)

    candidates: list[Candidate] = []
    for r, domain in by_domain.values():
        company = _company_entity(r) or {}
        title = company.get("name") or r.get("title") or r.get("author") or domain or r.get("url")
        text = (
            company.get("description")
            or r.get("text")
            or ((r.get("highlights") or [None])[0] if r.get("highlights") else None)
        )
        candidates.append(
            Candidate(
                name=str(title).strip() if title else "",
                domain=domain,
                description=(text or "")[:400] if isinstance(text, str) else None,
                extra={
                    "score": r.get("score"),
                    "url": r.get("url"),
                    "entity": company or None,
                },
            )
        )
    candidates.sort(key=lambda c: -_safe_score(c.extra.get("score")))
    return candidates


SPEC = build_spec("exa", {
    "base_url": "https://api.exa.ai",
    "requires_seed_domain": False,
    "auth": [{"header": "x-api-key", "env": "EXA_API_KEY"}],
    "request": {
        "method": "POST",
        "path": "/search",
        "body": {
            "query": "$query",              # "Find companies similar to <seed> (<domain>), <identity>."
            "category": "company",
            "type": "auto",
            "numResults": "$num_results",   # set by company_query (k * over_fetch, capped 100)
            "contents": {"text": {"maxCharacters": 600}},
        },
    },
    "response": {"candidates_path": ["results"]},
    "hooks": {"build_request": company_query, "transform_candidates": dedupe_by_domain},
})
VENDOR_SLUG = SPEC.slug
VENDOR_NAME = SPEC.name
CONFIGS: list[dict[str, Any]] = SPEC.configs


def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    primary = run_from_spec(SPEC, seed, k, config)
    if primary.error is None and not primary.candidates:
        fallback = run_from_spec(SPEC, seed, k, {**config, "query_variant": "concise_fallback"})
        fallback.config["fallback_used"] = True
        fallback.config["fallback_reason"] = "semantic_query_returned_zero_candidates"
        return fallback
    return primary
