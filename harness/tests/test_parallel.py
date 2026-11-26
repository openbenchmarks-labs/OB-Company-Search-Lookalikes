"""Offline tests for the original Parallel adapter (`harness/adapters/parallel.py`)."""
from __future__ import annotations

import pytest

from harness import spec_runner
from harness.adapters import REGISTRY
from harness.adapters import parallel as parallel_adapter
from harness.models import Seed

SEED_WITH_QUERY = Seed(
    seed_slug="toast",
    seed_name="Toast",
    seed_domain="toasttab.com",
    description="Restaurant point-of-sale platform",
    category="hospitality",
    short_description="restaurant POS and operations platform",
    query="Find companies similar to Toast (toasttab.com) for restaurants.",
)

SEED_NO_QUERY = Seed(
    seed_slug="acme",
    seed_name="Acme",
    seed_domain="acme.com",
    description="Cloud billing platform.",
    category="b2b-saas",
    short_description="cloud billing platform!",
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")


def test_parallel_registered_and_spec() -> None:
    assert "parallel" in REGISTRY
    spec = REGISTRY["parallel"].SPEC
    assert spec.slug == "parallel"
    assert spec.request.method == "POST"
    assert spec.request.path == "/v1beta/findall/entity-search"


def test_objective_authored_query_and_fallback() -> None:
    ctx_primary = {"seed": SEED_WITH_QUERY, "config": {"name": "seed_first"}, "vars": {}}
    parallel_adapter.objective(ctx_primary)
    assert ctx_primary["vars"]["query"] == SEED_WITH_QUERY.query

    ctx_fallback = {
        "seed": SEED_WITH_QUERY,
        "config": {"name": "seed_first", "query_variant": "concise_fallback"},
        "vars": {},
    }
    parallel_adapter.objective(ctx_fallback)
    assert ctx_fallback["vars"]["query"] == (
        "companies like Toast (toasttab.com) — restaurant POS and operations platform"
    )

    ctx_composed = {"seed": SEED_NO_QUERY, "config": {"name": "seed_first"}, "vars": {}}
    parallel_adapter.objective(ctx_composed)
    assert ctx_composed["vars"]["query"] == (
        "Find companies similar to Acme (acme.com), cloud billing platform. "
        "Return companies a buyer would realistically evaluate alongside Acme."
    )


def test_clean_aggregators_and_embedded_website() -> None:
    raw = [
        "not-a-dict",
        {
            "entity": {
                "name": "Direct Co",
                "url": "https://www.directco.example/about",
                "description": "Direct company",
            },
            "confidence": 0.9,
        },
        {
            "entity": {
                "name": "LinkedIn Only Co",
                "url": "https://www.linkedin.com/company/li-only",
                "description": "B2B SaaS | Website Url: https://www.lionly.example/home",
            },
            "score": 0.7,
        },
        {
            "entity": {
                "name": "Crunchbase Co",
                "url": "https://www.crunchbase.com/organization/cb-co",
                "snippet": "Some snippet",
            },
        },
    ]
    cands = parallel_adapter.clean_aggregators(raw, {})
    assert len(cands) == 3

    assert cands[0].name == "Direct Co"
    assert cands[0].domain == "directco.example"
    assert cands[0].extra["linkedin_url"] is None

    assert cands[1].name == "LinkedIn Only Co"
    assert cands[1].domain == "lionly.example"
    assert cands[1].extra["linkedin_url"] == "https://www.linkedin.com/company/li-only"

    assert cands[2].name == "Crunchbase Co"
    assert cands[2].domain is None
    assert cands[2].extra["source_url"] == "https://www.crunchbase.com/organization/cb-co"


def test_run_fallback_on_empty_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_http(method, url, *, headers=None, body=None, timeout=60):
        calls.append(body)
        if len(calls) == 1:
            return 200, {"results": []}, 10
        return 200, {
            "results": [{"entity": {"name": "Peer Co", "url": "https://peer.example"}, "score": 0.8}]
        }, 12

    monkeypatch.setattr(spec_runner, "http_request", fake_http)
    res = parallel_adapter.run(SEED_WITH_QUERY, 3, parallel_adapter.CONFIGS[0])

    assert len(calls) == 2
    assert calls[0]["match_limit"] == 5  # $k_min5 floor
    assert calls[0]["objective"] == SEED_WITH_QUERY.query
    assert calls[1]["objective"].startswith("companies like Toast")
    assert res.error is None
    assert len(res.candidates) == 1
    assert res.config.get("fallback_used") is True
    assert res.config.get("fallback_reason") == "expanded_query_returned_zero_candidates"


def test_run_does_not_fallback_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_http(method, url, *, headers=None, body=None, timeout=60):
        calls.append(body)
        return 500, {"error": "upstream error"}, 10

    monkeypatch.setattr(spec_runner, "http_request", fake_http)
    res = parallel_adapter.run(SEED_WITH_QUERY, 10, parallel_adapter.CONFIGS[0])

    assert len(calls) == 1
    assert res.error is not None
    assert "HTTP 500" in res.error
