"""Board math: rolls the judged (seed, tool) cells up into one row per tool."""
from __future__ import annotations

import math
import re
from typing import Any

from harness.models import now_iso

PRECISION_CUTOFFS = (10, 25, 100)

# --------------------------------------------------------------------------- #
# Failed cells                                                                #
# --------------------------------------------------------------------------- #
# A cell with no judged list is one of two kinds:
#   empty — the tool answered for the seed but gave no usable list: zero
#           candidates, or HTTP 404 saying the seed company or domain is not
#           found, on every setup that called it. It counts as 0 in the tool's
#           averages, at each cutoff the tool is scored at.
#   error — anything else (a setup skipped before any call, a crash, a judge
#           error, a timeout, 5xx/429, other 4xx, a bare 404). It may be our
#           side or transient, so it stays out of the averages and is counted.

_EMPTY_ANSWER = re.compile(r"(?:[\w.\-]+: )?(?:0 candidates|no_candidates)")
_SEED_NOT_FOUND = re.compile(r"(?:[\w.\-]+: )?HTTP 404\b.*\b(?:company|domain) not found", re.IGNORECASE | re.DOTALL)


def _is_empty_answer(message: str | None) -> bool:
    """Zero candidates, or HTTP 404 saying the seed company or domain is not found
    (a bare 'Not Found' may be a wrong URL on our side)."""
    m = (message or "").strip()
    return bool(_EMPTY_ANSWER.fullmatch(m) or _SEED_NOT_FOUND.match(m))


def _attempt_message(result: dict[str, Any]) -> str | None:
    if result.get("status") == "no_candidates":
        return "0 candidates"
    if result.get("status") == "vendor_error":
        return result.get("error")
    return result.get("status")


def _skipped_before_call(attempt: dict[str, Any]) -> bool:
    result = attempt.get("result") or {}
    return (
        result.get("status") == "vendor_error"
        and str(result.get("error") or "").startswith("skipped")
        and not attempt.get("vendor_calls")
    )


def failure_kind(error: str | None, attempts: list[dict[str, Any]] | None = None) -> str:
    """'empty' or 'error' for a cell with no judged list. With the run's
    attempts, every setup that called the tool must have come back empty; a
    setup skipped before any call does not count."""
    if attempts:
        ran = [a for a in attempts if not _skipped_before_call(a)]
        messages = [_attempt_message(a.get("result") or {}) for a in ran]
    else:
        messages = [error]
    return "empty" if messages and all(_is_empty_answer(m) for m in messages) else "error"


def cell_failure_kind(cell: dict[str, Any]) -> str | None:
    """None for a judged cell. Cells written before `failure_kind` existed are
    classified from their `error` string."""
    if cell.get("precision_at_k") is not None:
        return None
    return cell.get("failure_kind") or failure_kind(cell.get("error"))


def _nearest_rank(sorted_values: list[int], q: float) -> int | None:
    """Nearest-rank percentile of an ascending list, q in (0, 100]."""
    if not sorted_values:
        return None
    return sorted_values[max(0, math.ceil(q / 100 * len(sorted_values)) - 1)]


def recompute_leaderboard(snapshot: dict[str, Any], k: int) -> None:
    """Re-aggregate `snapshot.leaderboard` from the current `seed_vendors`
    cells, so the board is always derived from one source of truth."""
    cells: list[dict[str, Any]] = snapshot.get("seed_vendors", [])
    rows: list[dict[str, Any]] = snapshot.get("leaderboard", [])
    total_seeds = len(snapshot.get("seeds", []))

    by_vendor: dict[str, list[dict[str, Any]]] = {}
    for c in cells:
        by_vendor.setdefault(c["provider_slug"], []).append(c)

    # A newly added tool has cells before it has a derived board row.
    # Hydrate that row from the cells so adding a tool never requires a
    # hand-edited snapshot.
    known_slugs = {row["provider_slug"] for row in rows}
    for provider_slug, provider_cells in by_vendor.items():
        if provider_slug in known_slugs:
            continue
        exemplar = provider_cells[0]
        rows.append(
            {
                "dataset_slug": exemplar["dataset_slug"],
                "dataset_name": snapshot.get("dataset_name"),
                "provider_slug": provider_slug,
                "provider_name": exemplar["provider_name"],
            }
        )

    for row in rows:
        my_cells = by_vendor.get(row["provider_slug"], [])
        judged_cells = [c for c in my_cells if c.get("precision_at_k") is not None]
        primary_cells = [
            c for c in judged_cells
            if c.get("max_results") is None or int(c["max_results"]) >= k
        ]
        attempted = len({c["seed_slug"] for c in my_cells})
        kinds = [cell_failure_kind(c) for c in my_cells]
        zeroed = kinds.count("empty")

        # avg Precision@K — judged cells, plus a 0 for each seed the tool
        # answered with an empty list, once it is scored at K at all
        if primary_cells:
            avg = sum(c["precision_at_k"] for c in primary_cells) / (len(primary_cells) + zeroed)
            row["avg_precision_at_k"] = round(avg, 2)
        else:
            row["avg_precision_at_k"] = None

        for cutoff in PRECISION_CUTOFFS:
            precision_key = f"precision_at_{cutoff}"
            avg_key = f"avg_precision_at_{cutoff}"
            relevant_key = f"relevant_count_at_{cutoff}"
            total_key = f"total_relevant_at_{cutoff}"
            cells_with_cutoff = [
                c for c in my_cells
                if c.get(precision_key) is not None
                and (c.get("max_results") is None or int(c["max_results"]) >= cutoff)
            ]
            if cells_with_cutoff:
                row[avg_key] = round(
                    sum(float(c[precision_key]) for c in cells_with_cutoff)
                    / (len(cells_with_cutoff) + zeroed),
                    2,
                )
            else:
                row[avg_key] = None
            row[total_key] = sum(
                int(c.get(relevant_key) or 0) for c in cells_with_cutoff
            )

        row["seeds_attempted"] = attempted
        row["seeds_judged"] = len(judged_cells)
        row["seeds_zeroed"] = zeroed
        row["seeds_excluded"] = kinds.count("error")
        row["total_seeds"] = total_seeds
        row["k"] = k
        row["total_relevant"] = sum(
            int(c.get("relevant_count") or 0) for c in judged_cells
        )
        row["total_returned"] = sum(
            int(c.get("returned_count") or 0) for c in my_cells
        )
        # What the tool sent (before seed and duplicate removal); older cells fall back to
        # what was kept plus the duplicates they recorded.
        row["total_sent"] = sum(
            int(c["sent_count"]) if c.get("sent_count") is not None
            else int(c.get("returned_count") or 0) + int(c.get("duplicates_removed") or 0)
            for c in my_cells
        )

        # avg latency — only over cells that actually ran (latency != null)
        latencies = [
            c["latency_ms"] for c in my_cells if isinstance(c.get("latency_ms"), int)
        ]
        row["avg_latency_ms"] = (
            round(sum(latencies) / len(latencies), 1) if latencies else None
        )
        # p50 / p99 latency (nearest rank) over the calls that returned a judged list
        judged_lat = sorted(
            c["latency_ms"] for c in judged_cells if isinstance(c.get("latency_ms"), int)
        )
        row["p50_latency_ms"] = _nearest_rank(judged_lat, 50)
        row["p99_latency_ms"] = _nearest_rank(judged_lat, 99)

        row["total_cost_usd"] = round(
            sum(float(c.get("cost_usd") or 0.0) for c in my_cells), 4
        )
        row["cost_per_relevant_usd"] = (
            round(row["total_cost_usd"] / row["total_relevant"], 4)
            if row["total_relevant"] > 0
            else None
        )
        # Relevant companies per $1 at depth 100: judged-relevant companies in the top 100 over
        # the list price of those calls (rows sent, not rows kept, for per-row tools). Null
        # when the tool has no list price or its endpoint does not reach 100.
        priced = [
            c for c in judged_cells
            if c.get("cost_usd") is not None and c.get("relevant_count_at_100") is not None
            and (c.get("max_results") is None or int(c["max_results"]) >= 100)
        ]
        spend = sum(float(c["cost_usd"]) for c in priced)
        row["relevant_per_usd_at_100"] = (
            round(sum(int(c["relevant_count_at_100"]) for c in priced) / spend, 1)
            if priced and spend > 0 else None
        )

    # Re-rank: avg_precision_at_k DESC (nulls last), then total_relevant DESC,
    # then provider_slug ASC.
    rows.sort(
        key=lambda r: (
            1 if r["avg_precision_at_k"] is None else 0,
            -(r["avg_precision_at_k"] or 0.0),
            -int(r.get("total_relevant") or 0),
            r["provider_slug"],
        )
    )
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx

    snapshot["generated_at"] = now_iso()
