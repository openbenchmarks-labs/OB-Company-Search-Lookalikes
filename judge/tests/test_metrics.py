"""Metric registry checks (offline): pooled recall does the right set math and
Precision@K is the primary metric."""
from __future__ import annotations

from harness.models import Candidate, JudgedCandidate
from judge import metrics


def _judged(cands: list[dict]) -> list[JudgedCandidate]:
    return [
        JudgedCandidate(candidate=Candidate(name=c["name"], domain=c.get("domain")),
                        relevant=bool(c["relevant"]), rationale="")
        for c in cands
    ]


def test_recall_uses_the_cross_tool_pool() -> None:
    judged = _judged([
        {"name": "A", "domain": "a.com", "relevant": True},
        {"name": "B", "domain": "b.com", "relevant": False},
        {"name": "C", "domain": "c.com", "relevant": True},
    ])
    pool = {"a.com", "c.com", "z.com"}  # 3 distinct relevant across all tools
    rv, detail = metrics.compute_seed_context_metric("recall_at_k", judged, 10, pool)
    assert rv == round(100.0 * 2 / 3, 2), f"recall synthetic: got {rv} ({detail})"


def test_primary_metric_is_precision_at_k() -> None:
    assert metrics.primary_metric().key == "precision_at_k"
