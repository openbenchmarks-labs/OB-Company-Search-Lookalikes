"""context.dev company search, offline: the seed's query and mode in the request, cursor
paging until K, and a failed later page keeping what was collected."""
from __future__ import annotations

import pytest

from harness import spec_runner
from harness.adapters import REGISTRY
from harness.adapters import context_company_fast as cc
from harness.models import Seed

SEED = Seed(seed_slug="acme-mail", seed_name="Acme Mail", seed_domain="acmemail.example", description="d",
            category="b2b-saas", query="Find companies similar to Acme Mail.")


def _page(start: int, cursor: str | None) -> dict:
    return {"results": [{"name": f"Co {i}", "domain": f"co{i}.example"} for i in range(start, start + 10)],
            "next_cursor": cursor}


class FakeApi:
    def __init__(self, fail_page: int | None = None):
        self.bodies: list[dict] = []
        self.fail_page = fail_page

    def __call__(self, method, url, *, headers=None, body=None, body_encoding="json", timeout=60):
        self.bodies.append(body)
        n = len(self.bodies)
        if n == self.fail_page:
            return 500, {"error": "server"}, 5
        return 200, _page((n - 1) * 10, f"c{n}" if n < 5 else None), 5


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXT_API_KEY", "test-key")


@pytest.mark.parametrize("slug,mode", [("context-company-fast", "fast"), ("context-company-ultra", "ultra")])
def test_pages_until_k(monkeypatch: pytest.MonkeyPatch, slug: str, mode: str) -> None:
    api = FakeApi()
    monkeypatch.setattr(spec_runner, "http_request", api)
    monkeypatch.setattr(cc, "http_request", api)
    mod = REGISTRY[slug]
    res = mod.run(SEED, 30, mod.CONFIGS[0])
    assert api.bodies[0] == {"query": "Find companies similar to Acme Mail.", "mode": mode}
    assert [b.get("cursor") for b in api.bodies] == [None, "c1", "c2"]
    assert all(b["mode"] == mode for b in api.bodies)
    assert len(res.candidates) == 30 and res.candidates[0].domain == "co0.example"


def test_failed_later_page_keeps_earlier_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(fail_page=3)
    monkeypatch.setattr(spec_runner, "http_request", api)
    monkeypatch.setattr(cc, "http_request", api)
    mod = REGISTRY["context-company-fast"]
    res = mod.run(SEED, 100, mod.CONFIGS[0])
    assert res.error is None and len(res.candidates) == 20


def test_stops_when_the_cursor_runs_out(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi()
    monkeypatch.setattr(spec_runner, "http_request", api)
    monkeypatch.setattr(cc, "http_request", api)
    mod = REGISTRY["context-company-fast"]
    res = mod.run(SEED, 100, mod.CONFIGS[0])
    assert len(api.bodies) == 5 and len(res.candidates) == 50
