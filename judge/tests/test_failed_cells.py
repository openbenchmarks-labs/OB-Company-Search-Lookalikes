"""Cells with no judged list in the board averages.

- A seed the tool answered with no usable list (zero candidates, or HTTP 404 saying the seed
  company is not found) counts as 0; dropping it would reward a tool for failing.
- Any other failure (a setup skipped before any call, a crash, a timeout, a 400, a bare 404) may
  be our side, so it stays out of the averages and is counted on the row.
- A zeroed seed counts only at cutoffs the tool is scored at, and a tool with no judged seed
  stays null.
- A seed scores 0 only when every setup that called the tool came back empty.
- Re-judging from raw files classifies from the saved attempts, not the error string.
- Snapshots written before `failure_kind` existed are classified from the `error` string.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from harness.models import Seed
from harness.snapshot import upsert_seed_vendor_cell
from judge.leaderboard import recompute_leaderboard


def _judged(seed: str, tool: str, p: float, max_results: int | None = None) -> dict:
    return {
        "dataset_slug": "d", "seed_slug": seed, "provider_slug": tool, "provider_name": tool,
        "k": 100, "max_results": max_results, "returned_count": 10, "relevant_count": 1,
        "precision_at_k": p, "precision_at_10": p, "precision_at_25": p,
        "precision_at_100": None if max_results and max_results < 100 else p,
        "relevant_count_at_10": 1, "relevant_count_at_25": 1, "relevant_count_at_100": 1,
        "latency_ms": 100,
    }


def _seed(slug: str) -> Seed:
    return Seed(seed_slug=slug, seed_name=slug, seed_domain=f"{slug}.example", description="",
                category="b2b-saas", anchor="", capabilities=[])


def _failed(snapshot: dict, slug: str, tool: str, error: str, attempts: list | None = None) -> None:
    upsert_seed_vendor_cell(snapshot, dataset_slug="d", seed=_seed(slug), provider_slug=tool,
                            provider_name=tool, k=100, judged=None, error=error, attempts=attempts)


def _rows(snapshot: dict) -> dict[str, dict]:
    recompute_leaderboard(snapshot, 100)
    return {r["provider_slug"]: r for r in snapshot["leaderboard"]}


@pytest.fixture()
def snap() -> dict:
    s: dict = {"seeds": [{"seed_slug": f"s{i}"} for i in range(4)], "leaderboard": [], "seed_vendors": [
        _judged("s0", "empty", 60.0), _judged("s1", "empty", 40.0),
        _judged("s0", "notfound", 60.0), _judged("s1", "notfound", 40.0),
        _judged("s0", "flaky", 60.0), _judged("s1", "flaky", 40.0),
        _judged("s0", "capped", 60.0, max_results=25), _judged("s1", "capped", 40.0, max_results=25),
    ]}
    _failed(s, "s2", "empty", "paginated: 0 candidates")
    _failed(s, "s3", "empty", "default: no_candidates")
    _failed(s, "s2", "notfound", "default: HTTP 404: {'detail': 'Company not found: s2.example'}")
    _failed(s, "s3", "notfound", "default: HTTP 404: {'detail': 'Not Found'}")
    _failed(s, "s2", "flaky", "paginated: HTTP 400: {'error': {'type': 'bad_request'}}")
    _failed(s, "s3", "flaky", "seed_firmographic: skipped: no hints for seed")
    _failed(s, "s2", "capped", "paginated: 0 candidates")
    _failed(s, "s0", "never", "default: 0 candidates")
    _failed(s, "s1", "never", "default: HTTP 400: bad request")
    return s


def test_empty_answer_counts_as_zero(snap: dict) -> None:
    row = _rows(snap)["empty"]
    assert row["avg_precision_at_100"] == 25.0          # (60 + 40 + 0 + 0) / 4
    assert row["avg_precision_at_k"] == 25.0
    assert (row["seeds_zeroed"], row["seeds_excluded"]) == (2, 0)


def test_seed_not_found_counts_as_zero_but_bare_404_stays_out(snap: dict) -> None:
    row = _rows(snap)["notfound"]
    assert row["avg_precision_at_10"] == round(100 / 3, 2)  # (60 + 40 + 0) / 3
    assert (row["seeds_zeroed"], row["seeds_excluded"]) == (1, 1)


def test_other_failures_stay_out_but_are_counted(snap: dict) -> None:
    row = _rows(snap)["flaky"]
    assert row["avg_precision_at_100"] == 50.0
    assert (row["seeds_zeroed"], row["seeds_excluded"]) == (0, 2)


def test_capped_tool_zero_counts_only_where_scored(snap: dict) -> None:
    row = _rows(snap)["capped"]
    assert row["avg_precision_at_25"] == round(100 / 3, 2)
    assert row["avg_precision_at_100"] is None


def test_tool_with_no_judged_seed_stays_null(snap: dict) -> None:
    row = _rows(snap)["never"]
    assert row["avg_precision_at_10"] is None and row["avg_precision_at_100"] is None
    assert (row["seeds_zeroed"], row["seeds_excluded"]) == (1, 1)


def test_every_setup_must_come_back_empty(snap: dict) -> None:
    mixed = [{"result": {"status": "vendor_crash", "error": "TimeoutError"}}, {"result": {"status": "no_candidates"}}]
    _failed(snap, "s2", "empty", "paginated: 0 candidates", attempts=mixed)
    assert _rows(snap)["empty"]["seeds_excluded"] == 1


def test_setup_skipped_before_any_call_is_not_a_run(snap: dict) -> None:
    call = [{"response_status": 200}]
    skipped = {"status": "vendor_error", "error": "skipped: no firmographic hints for seed"}
    three_empty_one_skipped = [{"vendor_calls": call, "result": {"status": "no_candidates"}}] * 3 + [
        {"vendor_calls": [], "result": skipped}]
    _failed(snap, "s2", "filtered", "seed_firmographic: skipped", attempts=three_empty_one_skipped)
    _failed(snap, "s3", "filtered", "seed_firmographic: skipped", attempts=[{"vendor_calls": [], "result": skipped}])
    assert snap["seed_vendors"][-2]["failure_kind"] == "empty"
    assert snap["seed_vendors"][-1]["failure_kind"] == "error"


def test_old_snapshots_are_classified_from_the_error_string(snap: dict) -> None:
    for c in snap["seed_vendors"]:
        c.pop("failure_kind", None)
    assert _rows(snap)["notfound"]["avg_precision_at_10"] == round(100 / 3, 2)


def test_rejudge_classifies_from_saved_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import harness.rejudge as rj

    call = [{"response_status": 200}]
    raw = {
        "tool-a": [{"config": {"name": "default"}, "vendor_calls": [{"response_status": 404}],
                    "result": {"status": "vendor_error", "error": "HTTP 404: {'detail': 'Company not found: s0.example'}"}}],
        "tool-b": [{"config": {"name": "default"}, "result": {"status": "vendor_crash", "error": "TimeoutError: timed out"}},
                   {"config": {"name": "paginated"}, "vendor_calls": call, "result": {"status": "no_candidates"}}],
    }
    snapshot: dict = {"dataset_slug": "d", "k": 100, "seeds": [{"seed_slug": "s0"}], "leaderboard": [],
                      "seed_vendors": [{"seed_slug": "s0", "provider_slug": v, "provider_name": v} for v in raw]}
    seed_dir = tmp_path / "d" / "s0"
    seed_dir.mkdir(parents=True)
    for tool, attempts in raw.items():
        (seed_dir / f"{tool}.raw.json").write_text(json.dumps({"provider_slug": tool, "k": 100, "attempts": attempts}))
    monkeypatch.setattr(rj, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(rj, "read_snapshot", lambda: snapshot)
    monkeypatch.setattr(rj, "read_seeds", lambda: [_seed("s0")])
    monkeypatch.setattr(rj, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["rejudge", "--mock", "--dry-run", "--skip-vendors", "", "--concurrency", "1"])
    rj.main()
    kinds = {c["provider_slug"]: c.get("failure_kind") for c in snapshot["seed_vendors"]}
    assert kinds == {"tool-a": "empty", "tool-b": "error"}
