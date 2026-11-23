"""Parallel Task API adapters, offline: the create-run body, waiting through "still running",
parsing, seed and duplicate removal, citations hidden from the judges, the core variant's
minItems handling, resuming a run, and a dropped result call."""
from __future__ import annotations

import json
from typing import Any

import pytest

from harness.adapters import REGISTRY
from harness.adapters import parallel_task as pt
from harness.models import Seed
from judge.llm_judge import _format_extra

SEED = Seed(seed_slug="acme-mail", seed_name="Acme Mail", seed_domain="acmemail.example", description="d",
            category="b2b-saas", anchor="email platform", capabilities=["email"],
            query="Find companies similar to Acme Mail.")
CONTENT = {"companies": [
    {"name": "Bluebird", "domain": "https://www.bluebird.example/", "description": "Email marketing platform."},
    {"name": "Acme Mail", "domain": "acmemail.example", "description": "seed"},
    {"name": "Bluebird Inc", "domain": "bluebird.example", "description": "dup"},
    {"name": "Cardinal", "domain": "cardinal.example", "description": "SMS marketing for retailers."},
]}
BASIS = [{"field": "companies.0", "citations": [{"url": "https://bluebird.example"}]},
         {"field": "companies.3.domain", "citations": [{"url": "https://cardinal.example/about"}]}]
DONE = {"run": {"run_id": "trun_1", "status": "completed"}, "output": {"type": "json", "content": CONTENT, "basis": BASIS}}


class FakeApi:
    """Records calls; the first result call answers `first_get` (default: still running)."""

    def __init__(self, first_get: Any = (408, {"detail": "still running"}), reject_min_items: Any = None):
        self.calls: list[tuple[str, str, Any]] = []
        self.first_get = first_get
        self.reject_min_items = reject_min_items

    def __call__(self, method, url, *, headers=None, body=None, body_encoding="json", timeout=60):
        self.calls.append((method, url, body))
        if method == "POST":
            if self.reject_min_items and "minItems" in json.dumps(body):
                return (*self.reject_min_items, 5)
            return 202, {"run_id": "trun_1", "status": "queued"}, 5
        if sum(1 for c in self.calls if c[0] == "GET") == 1:
            if isinstance(self.first_get, Exception):
                raise self.first_get
            return (*self.first_get, 5)
        return 200, DONE, 5

    def posts(self) -> list[Any]:
        return [c[2] for c in self.calls if c[0] == "POST"]

    def gets(self) -> int:
        return sum(1 for c in self.calls if c[0] == "GET")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.delenv("PARALLEL_TASK_RESUME", raising=False)
    monkeypatch.setattr(pt, "RESULT_RETRY_PAUSE_SEC", 0)


def _run(slug: str, monkeypatch: pytest.MonkeyPatch, api: FakeApi):
    monkeypatch.setattr(pt, "http_request", api)
    mod = REGISTRY[slug]
    return mod.run(SEED, 100, mod.CONFIGS[0])


def test_base_task_request_parsing_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi()
    res = _run("parallel-task", monkeypatch, api)
    post = api.posts()[0]
    assert api.calls[0][1].endswith("/v1/tasks/runs") and post["processor"] == "base"
    assert post["input"] == "Return 100 companies similar to Acme Mail."
    companies = post["task_spec"]["output_schema"]["json_schema"]["properties"]["companies"]
    assert set(companies["items"]["properties"]) == {"name", "domain", "description"}
    assert "Up to 100" in companies["description"] and "minItems" not in companies
    assert api.gets() == 2 and f"timeout={pt.RESULT_WAIT_SEC}" in api.calls[1][1]
    assert [c.domain for c in res.candidates] == ["bluebird.example", "cardinal.example"]
    assert (res.sent_count, res.duplicates_removed, res.cost_usd) == (4, 1, 0.01)


def test_citations_kept_but_hidden_from_judges(monkeypatch: pytest.MonkeyPatch) -> None:
    res = _run("parallel-task", monkeypatch, FakeApi())
    assert res.candidates[1].extra["basis_citations"] == ["https://cardinal.example/about"]
    assert "cardinal.example/about" not in (_format_extra(res.candidates[1].extra) or "")


def test_core_task_puts_the_count_in_the_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi()
    res = _run("parallel-task-core", monkeypatch, api)
    post = api.posts()[0]
    companies = post["task_spec"]["output_schema"]["json_schema"]["properties"]["companies"]
    assert post["processor"] == "core" and companies["minItems"] == 100 and "exactly 100" in companies["description"]
    assert res.cost_usd == 0.025 and "min_items_rejected" not in res.config


@pytest.mark.parametrize("rejection", [
    (422, {"error": "minItems is not supported"}),
    (200, {"type": "error", "error": {"message": "Unsupported keyword 'minItems'"}}),
])
def test_rejected_min_items_is_resent_without_it(monkeypatch: pytest.MonkeyPatch, rejection) -> None:
    api = FakeApi(reject_min_items=rejection)
    res = _run("parallel-task-core", monkeypatch, api)
    posts = api.posts()
    second = posts[1]["task_spec"]["output_schema"]["json_schema"]["properties"]["companies"]
    assert len(posts) == 2 and "minItems" not in second and "exactly 100" in second["description"]
    assert str(rejection[0]) in res.config["min_items_rejected"]
    assert res.error is None and len(res.candidates) == 2


def test_resumed_run_is_fetched_without_a_new_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    resume = tmp_path / "resume.json"
    resume.write_text(json.dumps({"parallel-task-core/acme-mail": "trun_old"}))
    monkeypatch.setenv("PARALLEL_TASK_RESUME", str(resume))
    api = FakeApi()
    res = _run("parallel-task-core", monkeypatch, api)
    assert not api.posts() and "trun_old" in api.calls[0][1]
    assert res.config["resumed_run"] is True and len(res.candidates) == 2


def test_dropped_result_call_is_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(first_get=TimeoutError("The read operation timed out"))
    res = _run("parallel-task", monkeypatch, api)
    assert api.gets() == 2 and res.error is None and len(res.candidates) == 2
    assert res.config["run_id"] == "trun_1" and len(res.config["result_poll_errors"]) == 1


def test_still_active_sent_as_200_is_polled_again(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(first_get=(200, {"type": "error", "error": {"message": "Run still active."}}))
    res = _run("parallel-task", monkeypatch, api)
    assert api.gets() == 2 and res.error is None and len(res.candidates) == 2
