"""The board derives relevant per $1 at 100 and p50 / p99 latency from the cells."""
from judge.leaderboard import recompute_leaderboard


def _cell(seed: str, latency_ms: int, relevant: int, cost_usd: float) -> dict:
    return {"dataset_slug": "d", "seed_slug": seed, "provider_slug": "v", "provider_name": "V",
            "precision_at_k": relevant, "relevant_count_at_100": relevant, "relevant_count": relevant,
            "latency_ms": latency_ms, "cost_usd": cost_usd, "returned_count": 100}


def _board_row() -> dict:
    snap = {"seeds": [{}] * 3,
            "seed_vendors": [_cell("a", 1000, 50, 0.5), _cell("b", 2000, 70, 0.5), _cell("c", 9000, 30, 0.5)],
            "leaderboard": []}
    recompute_leaderboard(snap, 100)
    return snap["leaderboard"][0]


def test_relevant_per_dollar_is_150_over_1_50() -> None:
    assert _board_row()["relevant_per_usd_at_100"] == 100.0


def test_p50_and_p99_latency_use_nearest_rank() -> None:
    row = _board_row()
    assert row["p50_latency_ms"] == 2000
    assert row["p99_latency_ms"] == 9000
