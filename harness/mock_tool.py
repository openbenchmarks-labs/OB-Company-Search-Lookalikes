"""Mock tool — deterministic, offline, no API key required.

Generates K plausible-looking candidate companies per seed by walking a
small per-vertical pool. Combined with the mock judge in
`judge/llm_judge.py`, this runs the whole pipeline end-to-end without
spending tool credits or judge tokens.

Not in the adapter registry. `harness.run` uses it when `--mock` is passed.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .models import Candidate, RunResult, Seed, take_top

CONFIGS: list[dict[str, Any]] = [{"name": "mock", "filters": {}}]

# Per-category pools of synthetic candidates. The mock runner deterministically
# shuffles a pool into K candidates per seed. Names and domains are placeholders
# (".example" never resolves) so a mock run can never be mistaken for live data.
POOL_CATEGORIES = (
    "b2b-saas", "devtools", "ecommerce", "healthtech", "home-services", "real-estate",
    "fintech", "cybersecurity", "industrial", "logistics", "hospitality", "energy",
)
POOL_SIZE = 15
POOLS: dict[str, list[tuple[str, str]]] = {
    category: [
        (f"Mock {category} company {i:02d}", f"mock-{category}-{i:02d}.example")
        for i in range(1, POOL_SIZE + 1)
    ]
    for category in POOL_CATEGORIES
}


def make_runner(provider_slug: str, provider_name: str):
    """Build a vendor-specific mock runner closure. Each vendor's mock
    output is deterministic *and* slightly different from the others (so
    the matrix doesn't look identical row-by-row)."""

    def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
        pool = list(POOLS.get(seed.category, []))
        if not pool:
            return RunResult(
                seed_slug=seed.seed_slug,
                provider_slug=provider_slug,
                config_name=config["name"],
                config=config,
                candidates=[],
                latency_ms=120,
                error=f"mock: no pool for category {seed.category}",
            )
        # Deterministic shuffle per (vendor, seed).
        keyed = sorted(
            pool,
            key=lambda nd: hashlib.sha256(
                f"{provider_slug}|{seed.seed_slug}|{nd[1]}".encode("utf-8")
            ).hexdigest(),
        )
        # Drop the seed itself if present.
        keyed = [
            (n, d)
            for (n, d) in keyed
            if d.lower() != (seed.seed_domain or "").lower()
        ]
        cands = [
            Candidate(
                name=n,
                domain=d,
                description=f"Mock candidate from {seed.category} pool — {n}",
                extra={"vendor": provider_slug},
            )
            for (n, d) in keyed
        ]
        return RunResult(
            seed_slug=seed.seed_slug,
            provider_slug=provider_slug,
            config_name=config["name"],
            config=config,
            candidates=take_top(cands, k),
            latency_ms=180 + (hash(provider_slug) % 200),
            cost_usd=0.0,
            requested_k=k,
        )

    return run
