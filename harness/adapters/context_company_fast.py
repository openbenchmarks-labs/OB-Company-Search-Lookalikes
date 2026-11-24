"""context.dev company search, fast mode: one natural-language search per seed.

`query` sends the seed's authored query verbatim (the same string Exa and Parallel
receive), composing the seed-first query when a seed has none. `paginate` turns the
first page of company records into candidates, then follows `next_cursor` (10
companies per page, no limit parameter) until K candidates, an empty page, no
cursor, or a 12-page bound. The ultra adapter reuses both hooks.
"""
from __future__ import annotations

from typing import Any

from ..http_client import http_request
from ..models import Candidate, RunResult, Seed
from ..spec_runner import run_from_spec
from ..tool_specs import build_spec

_MAX_PAGES = 12


def query(ctx: dict[str, Any]) -> None:
    """build_request: the seed's authored query, or the seed-first sentence."""
    seed: Seed = ctx["seed"]
    if seed.query:
        ctx["vars"]["query"] = seed.query.strip()
        return
    anchor = f"{seed.seed_name} ({seed.seed_domain})" if seed.seed_domain else seed.seed_name
    identity = (seed.short_description or seed.description or "").strip().rstrip(".!?")
    suffix = f", {identity}." if identity else "."
    ctx["vars"]["query"] = (
        f"Find companies similar to {anchor}{suffix} "
        f"Return companies a buyer would realistically evaluate alongside {seed.seed_name}."
    )


def _to_candidates(items: list[Any], start_rank: int) -> list[Candidate]:
    out: list[Candidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        domain = item.get("domain")
        name = (item.get("name") or domain or "").strip()
        if not name:
            continue
        out.append(
            Candidate(
                name=name,
                domain=domain,
                description=item.get("description"),
                rank=start_rank + len(out),
                extra={
                    "organization_type": item.get("organization_type"),
                    "operating_status": item.get("operating_status"),
                    "industries": item.get("industries"),
                    "employees": item.get("employees"),
                    "revenue": item.get("revenue"),
                    "locations": item.get("locations"),
                },
            )
        )
    return out


def paginate(raw_items: list[Any], ctx: dict[str, Any]) -> list[Candidate]:
    """transform_candidates: the first page, then follow `next_cursor` until K. A failed
    later page keeps what was already collected."""
    payload: dict[str, Any] = ctx["payload"]
    k: int = ctx["k"]
    spec = ctx["spec"]
    candidates = _to_candidates(raw_items, 1)
    cursor = payload.get("next_cursor") if isinstance(payload, dict) else None
    headers = {a.header: f"{a.value_prefix}{ctx['env'][a.env]}" for a in spec.auth}
    body = {"query": ctx["vars"]["query"], "mode": ctx["config"]["mode"]}
    pages = 1
    while cursor and len(candidates) < k and pages < _MAX_PAGES:
        status, page, _ = http_request("POST", f"{spec.base_url}{spec.request.path}", headers=headers,
                                       body={**body, "cursor": cursor}, timeout=spec.timeout_sec)
        if status >= 300 or not isinstance(page, dict):
            break
        items = page.get("results") or []
        if not items:
            break
        candidates.extend(_to_candidates(items, len(candidates) + 1))
        cursor = page.get("next_cursor")
        pages += 1
    return candidates


def request_template(mode: str) -> dict[str, Any]:
    return {
        "base_url": "https://api.context.dev",
        "requires_seed_domain": False,
        "auth": [{"header": "Authorization", "env": "CONTEXT_API_KEY", "value_prefix": "Bearer "}],
        "request": {"method": "POST", "path": "/v1/company/search", "body": {"query": "$query", "mode": mode}},
        "response": {"candidates_path": ["results"]},
        "hooks": {"build_request": query, "transform_candidates": paginate},
    }


SPEC = build_spec("context-company-fast", request_template("fast"))
VENDOR_SLUG = SPEC.slug
VENDOR_NAME = SPEC.name
CONFIGS: list[dict[str, Any]] = SPEC.configs


def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    return run_from_spec(SPEC, seed, k, config)
