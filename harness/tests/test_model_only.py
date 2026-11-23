"""Model-only baselines, offline: the request per transport, parsing, seed and duplicate
removal, token cost, blind description calls, and bookkeeping hidden from the judges."""
from __future__ import annotations

import json

import pytest

from harness.adapters import REGISTRY
from harness.adapters import model_only as mo
from harness.models import Seed
from judge.llm_judge import _format_extra

SEED = Seed(seed_slug="acme-mail", seed_name="Acme Mail", seed_domain="acmemail.example", description="d",
            category="b2b-saas", anchor="email platform", capabilities=["email", "sms"],
            query="Find companies similar to Acme Mail.")
SLUGS = ("model-only-gpt-5.6-sol", "model-only-claude-opus-5-5", "model-only-kimi-k3")


def _reply(url: str, text: str, tokens_in: int, tokens_out: int):
    if "anthropic" in url:
        return 200, {"content": [{"type": "text", "text": text}],
                     "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out}}, 5
    return 200, {"choices": [{"message": {"content": text}}],
                 "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out}}, 5


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("ANTHROPIC_API_KEY", "FIREWORKS_API_KEY", "AZURE_OPENAI_NEXTGEN_DEPLOYMENT_KEY"):
        monkeypatch.setenv(key, "test-key")
    monkeypatch.setenv("AZURE_OPENAI_NEXTGEN_DEPLOYMENT_URL", "https://example.test/openai/v1")
    monkeypatch.setattr(mo, "domain_resolves", lambda d: True)


def test_single_call_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"name": "Bluebird", "domain": "https://www.bluebird.example/", "description": "Email marketing.",
             "industry": "Software", "country": "US", "employees": "501-1K", "founded_year": 2013},
            {"name": "Acme Mail", "domain": "acmemail.example"},
            {"name": "Bluebird", "domain": "bluebird.example"},
            {"name": "Cardinal", "domain": "cardinal.example"}]
    seen: dict = {}

    def fake(method, url, *, headers=None, body=None, body_encoding="json", timeout=60):
        seen["body"] = body
        return _reply(url, json.dumps({"companies": rows}), 1000, 500)

    monkeypatch.setattr(mo, "http_request", fake)
    for slug in SLUGS:
        res = REGISTRY[slug].run(SEED, 100, {"name": "model_only"})
        assert [c.domain for c in res.candidates] == ["bluebird.example", "cardinal.example"]
        assert (res.sent_count, res.duplicates_removed) == (4, 1)
        first = res.candidates[0]
        assert first.description == "Email marketing." and first.extra["employees"] == "501-1K"
        assert first.extra["domain_resolves"] is True
        assert "Find companies similar to Acme Mail." in json.dumps(seen["body"])
    assert REGISTRY["model-only-gpt-5.6-sol"].run(SEED, 100, {"name": "model_only"}).cost_usd == pytest.approx(0.014)
    assert REGISTRY["model-only-kimi-k3"].run(SEED, 100, {"name": "model_only"}).cost_usd is None


def test_list_then_describe_keeps_descriptions_blind_to_the_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake(method, url, *, headers=None, body=None, body_encoding="json", timeout=60):
        text_in = json.dumps(body)
        calls.append(text_in)
        if "lookalike_list" in text_in or "Return 100 companies" in text_in:
            text = json.dumps({"companies": [{"name": "Bluebird", "domain": "bluebird.example"},
                                             {"name": "Acme Mail", "domain": "acmemail.example"},
                                             {"name": "Cardinal", "domain": "https://www.cardinal.example/"}]})
        else:
            name = "Bluebird" if "bluebird.example" in text_in else "Cardinal"
            text = json.dumps({"description": f"{name} sells marketing software.", "industry": "Software",
                               "country": "US", "employees": "501-1K", "founded_year": 2012})
        return _reply(url, text, 100, 50)

    monkeypatch.setattr(mo, "http_request", fake)
    monkeypatch.setattr(mo, "fetch_page", lambda d: {"ok": True, "status": 200, "url": f"https://www.{d}/",
                                                     "text": f"Title: {d} home\nVisible text: page for {d}"})
    for slug in SLUGS:
        calls.clear()
        mod = REGISTRY[slug]
        assert mod.CONFIGS[0]["name"] == "list_then_describe"
        res = mod.run(SEED, 100, mod.CONFIGS[0])
        assert [c.domain for c in res.candidates] == ["bluebird.example", "cardinal.example"]
        assert len(calls) == 3                                  # one list call + one per kept company
        assert "Find companies similar to Acme Mail." in calls[0]
        for blind in calls[1:]:
            assert "acme" not in blind.lower() and "email platform" not in blind
        assert "page for bluebird.example" in calls[1]
        first = res.candidates[0]
        assert first.description == "Bluebird sells marketing software."
        assert first.extra["description_source"] == "separate_call_with_homepage" and first.extra["page_fetched"] is True
        assert res.config["describe_calls"] == 2 and res.config["describe_failures"] == 0
        shown = _format_extra(first.extra) or ""
        assert "industry" in shown
        assert not any(k in shown for k in ("domain_resolves", "model_knowledge", "description_source",
                                            "page_fetched", "page_url"))
    cost = REGISTRY["model-only-gpt-5.6-sol"].run(SEED, 100, {"name": "list_then_describe"}).cost_usd
    assert cost == pytest.approx((300 * 4 + 150 * 20) / 1e6)
