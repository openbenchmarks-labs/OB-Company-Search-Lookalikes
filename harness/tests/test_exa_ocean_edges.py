"""Offline edge-case tests for Exa and Ocean adapters."""
from __future__ import annotations

import pytest

from harness.adapters import exa as exa_adapter
from harness.adapters import ocean as ocean_adapter
from harness.models import Seed, SkipConfig


def test_exa_dedupe_handles_malformed_items_and_scores() -> None:
    raw = [
        None,
        "bad-item",
        {"url": None, "title": "No URL"},
        {"url": "", "title": "Empty URL"},
        {"url": "https://www.mylinkedin.com/about", "title": "MyLinkedIn", "score": "bad-score"},
        {"url": "https://www.linkedin.com/company/acme", "title": "Acme LI", "score": 0.9},
        {"url": "https://www.peer.example/a", "title": "Peer Low", "score": 0.2},
        {"url": "https://peer.example/b", "title": "Peer High", "score": 0.8},
    ]
    cands = exa_adapter.dedupe_by_domain(raw, {})
    by_name = {c.name: c for c in cands}

    # Substring "linkedin.com" inside "mylinkedin.com" must NOT be treated as an aggregator.
    assert by_name["MyLinkedIn"].domain == "mylinkedin.com"
    # Exact aggregator host must have domain=None.
    assert by_name["Acme LI"].domain is None
    # Same domain collapses to highest-scoring entry.
    assert "Peer Low" not in by_name
    assert by_name["Peer High"].domain == "peer.example"


def test_ocean_size_bands_handles_non_numeric_hints() -> None:
    bands = ocean_adapter._size_bands("bad", "also-bad")
    assert len(bands) == len(ocean_adapter._OCEAN_BANDS)


def test_ocean_firmographic_filters_cleans_locations_and_skips_empty() -> None:
    bare = Seed(seed_slug="b", seed_name="Bare", seed_domain="bare.com", description="d", category="saas")
    with pytest.raises(SkipConfig):
        ocean_adapter.firmographic_filters({"config": {"use_seed_firmo": True}, "seed": bare, "vars": {}})

    seeded = Seed(
        seed_slug="s",
        seed_name="Seeded",
        seed_domain="seeded.com",
        description="d",
        category="saas",
        firmographics={"min_employees": 20, "max_employees": 100, "locations": ["US", "", None, "  ", "DE"]},
    )
    ctx = {"config": {"use_seed_firmo": True}, "seed": seeded, "vars": {}}
    ocean_adapter.firmographic_filters(ctx)
    assert ctx["vars"]["company_sizes"] == ["11-50", "51-200"]
    assert ctx["vars"]["primary_locations"] == {"includeCountries": ["us", "de"]}
