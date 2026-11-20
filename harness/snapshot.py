"""Reads the seed companies and writes run results.

`data/latest-lookalike.json` holds the seeds, one cell per (seed, tool) and the
board. Each cell's full detail and its raw audit trail are written under
`data/lookalike-runs/<dataset>/<seed>/`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import JudgedRun, RawHttpCall, RawJudgeCall, Seed, now_iso

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "data" / "latest-lookalike.json"
RUNS_DIR = ROOT / "data" / "lookalike-runs"

DEFAULT_K = 100


def read_snapshot() -> dict[str, Any]:
    if not SNAPSHOT_PATH.exists():
        raise FileNotFoundError(f"snapshot missing at {SNAPSHOT_PATH}")
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def write_snapshot(snapshot: dict[str, Any]) -> None:
    SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, indent=2) + "\n", encoding="utf-8"
    )


def read_seeds() -> list[Seed]:
    snap = read_snapshot()
    out: list[Seed] = []
    for s in snap.get("seeds", []):
        out.append(
            Seed(
                seed_slug=s["seed_slug"],
                seed_name=s["seed_name"],
                seed_domain=s.get("seed_domain"),
                description=s.get("description"),
                category=s["category"],
                short_description=s.get("short_description"),
                anchor=s.get("anchor"),
                capabilities=list(s.get("capabilities") or []),
                query=s.get("query"),
            )
        )
    return out


def raw_http_call_to_dict(call: RawHttpCall) -> dict[str, Any]:
    return {
        "method": call.method,
        "url": call.url,
        "request_headers": call.request_headers,
        "request_body": call.request_body,
        "response_status": call.response_status,
        "response_body": call.response_body,
        "elapsed_ms": call.elapsed_ms,
    }


def raw_judge_call_to_dict(call: RawJudgeCall) -> dict[str, Any]:
    return {
        "model": call.model,
        "messages": call.messages,
        "raw_response": call.raw_response,
        "parsed_response": call.parsed_response,
        "elapsed_ms": call.elapsed_ms,
        "for_candidate_rank": call.for_candidate_rank,
        "error": call.error,
        "cached": call.cached,
        "usage": call.usage,
    }


def persist_run_raw(
    dataset_slug: str,
    *,
    seed: Seed,
    provider_slug: str,
    provider_name: str,
    k: int,
    judge_model: str,
    winning_config_name: str | None,
    attempts: list[dict[str, Any]],
) -> Path:
    """Write the full audit trail for one (seed, tool) cell. Sibling to
    the slim `<tool>.json`. Captures every config attempted in the sweep
    with its literal HTTP request/response + judge prompt/response.

    Shape:
        {
          "dataset_slug": ..., "seed_slug": ..., "seed_input": {...},
          "provider_slug": ..., "k": ...,
          "winning_config_name": ...,
          "judge_model": ...,
          "captured_at": ...,
          "attempts": [
            {
              "config": {...},
              "vendor_calls": [<raw_http_call>, ...],
              "extracted_candidates": [<the slim candidate dicts handed to the judge>],
              "judge_calls": [<raw_judge_call>, ...],
              "result": {
                "k": ..., "relevant_count": ..., "precision_at_k": ...,
                "latency_ms": ..., "judged_at": ..., "error": ...,
              },
            },
            ...
          ],
        }
    """
    out_dir = RUNS_DIR / dataset_slug / seed.seed_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{provider_slug}.raw.json"
    payload = {
        "dataset_slug": dataset_slug,
        "seed_slug": seed.seed_slug,
        "seed_input": {
            "seed_name": seed.seed_name,
            "seed_domain": seed.seed_domain,
            "description": seed.description,
            "category": seed.category,
        },
        "provider_slug": provider_slug,
        "provider_name": provider_name,
        "k": k,
        "winning_config_name": winning_config_name,
        "judge_model": judge_model,
        "captured_at": now_iso(),
        "attempts": attempts,
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out_path


def persist_run_detail(dataset_slug: str, judged: JudgedRun) -> Path:
    """Write the full per-candidate judge output so any cell on the
    board can be audited row-by-row. The snapshot only carries the
    aggregate Precision@K — detail lives here."""
    run = judged.run
    out_dir = RUNS_DIR / dataset_slug / run.seed_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run.provider_slug}.json"
    payload = {
        "dataset_slug": dataset_slug,
        "seed_slug": run.seed_slug,
        "provider_slug": run.provider_slug,
        "config_name": run.config_name,
        "config": run.config,
        "k": judged.k,
        "relevant_count": judged.relevant_count,
        "precision_at_k": judged.precision_at_k,
        "relevant_count_at_10": judged.relevant_count_at(10),
        "relevant_count_at_25": judged.relevant_count_at(25),
        "relevant_count_at_100": judged.relevant_count_at(100),
        "precision_at_10": judged.precision_at(10),
        "precision_at_25": judged.precision_at(25),
        "precision_at_100": judged.precision_at(100),
        "latency_ms": run.latency_ms,
        "cost_usd": run.cost_usd,
        "duplicates_removed": run.duplicates_removed,
        "sent_count": run.sent_count,
        "max_results": run.max_results,
        "judge_model": judged.judge_model,
        "judged_at": judged.judged_at,
        "candidates": [
            {
                "name": j.candidate.name,
                "domain": j.candidate.domain,
                "description": j.candidate.description,
                "rank": j.candidate.rank,
                "extra": j.candidate.extra,
                "relevant": j.relevant,
                "rationale": j.rationale,
                # Gate + overlap: lets a cell be read as "right kind of company,
                # 3 of 4 capabilities" rather than a bare boolean, and shows
                # WHY a candidate failed (wrong anchor vs no overlap).
                "anchor_match": j.anchor_match,
                "capabilities_matched": j.capabilities_matched,
                # Per-judge breakdown (panel runs). Without it a majority(n=3)
                # cell would flatten to one fabricated vote.
                "votes": [
                    {"judge_model": v.judge_model, "relevant": v.relevant, "rationale": v.rationale}
                    for v in (j.votes or [])
                ],
            }
            for j in judged.judged
        ],
        "error": run.error,
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out_path


def upsert_seed_vendor_cell(
    snapshot: dict[str, Any],
    *,
    dataset_slug: str,
    seed: Seed,
    provider_slug: str,
    provider_name: str,
    k: int,
    judged: JudgedRun | None,
    error: str | None = None,
) -> None:
    """Insert / replace one (seed, tool) cell in `snapshot.seed_vendors`.

    Pass `judged=None` (and optionally an `error` string) when the tool
    failed or returned 0 candidates — the cell will land with
    precision_at_k=null so the board shows it as N/A."""
    cells: list[dict[str, Any]] = snapshot.setdefault("seed_vendors", [])

    # Drop any existing cell for this (seed, tool) pair.
    cells[:] = [
        c
        for c in cells
        if not (c["seed_slug"] == seed.seed_slug and c["provider_slug"] == provider_slug)
    ]

    if judged is None:
        cells.append(
            {
                "dataset_slug": dataset_slug,
                "seed_slug": seed.seed_slug,
                "seed_name": seed.seed_name,
                "category": seed.category,
                "provider_slug": provider_slug,
                "provider_name": provider_name,
                "k": k,
                "returned_count": 0,
                "relevant_count": None,
                "precision_at_k": None,
                "relevant_count_at_10": None,
                "relevant_count_at_25": None,
                "relevant_count_at_100": None,
                "precision_at_10": None,
                "precision_at_25": None,
                "precision_at_100": None,
                "latency_ms": None,
                "cost_usd": None,
                "duplicates_removed": 0,
                "sent_count": None,
                "max_results": None,
                "judge_model": None,
                "judged_at": None,
                "config_used": None,
                "error": error,
            }
        )
        return

    run = judged.run
    cells.append(
        {
            "dataset_slug": dataset_slug,
            "seed_slug": seed.seed_slug,
            "seed_name": seed.seed_name,
            "category": seed.category,
            "provider_slug": provider_slug,
            "provider_name": provider_name,
            "k": k,
            "returned_count": len(run.candidates),
            "relevant_count": judged.relevant_count,
            "precision_at_k": judged.precision_at_k,
            "relevant_count_at_10": judged.relevant_count_at(10),
            "relevant_count_at_25": judged.relevant_count_at(25),
            "relevant_count_at_100": judged.relevant_count_at(100),
            "precision_at_10": judged.precision_at(10),
            "precision_at_25": judged.precision_at(25),
            "precision_at_100": judged.precision_at(100),
            "latency_ms": run.latency_ms,
            "cost_usd": run.cost_usd,
            "duplicates_removed": run.duplicates_removed,
            "sent_count": run.sent_count,
            "max_results": run.max_results,
            "judge_model": judged.judge_model,
            "judged_at": judged.judged_at,
            "config_used": {"name": run.config_name, **run.config},
        }
    )
