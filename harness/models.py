"""Shared data types: a seed, a tool's candidates, a run, and judge verdicts."""
from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any


class SkipConfig(Exception):
    """A hook raises this to skip the current config cleanly (no HTTP call). The
    spec runner turns it into an error RunResult so the runner records
    the skip and moves to the next config. Used e.g. by firmographic-filtered
    configs when a seed carries no firmographic hints."""


@dataclasses.dataclass(frozen=True)
class Seed:
    """One row from `snapshot.seeds`. The exact input every tool sees."""

    seed_slug: str
    seed_name: str
    seed_domain: str | None
    description: str | None
    category: str
    # One-clause identity used verbatim in the NL lookalike query sent to
    # text-query tools (Exa, Parallel). Deliberately short: it exists to
    # disambiguate the seed entity, not to enumerate features or target
    # segments — deriving the similarity axes from the seed is the tool's
    # job and is exactly what the benchmark measures. Falls back to
    # `description` when absent.
    short_description: str | None = None

    # --- The lookalike definition, split into its two halves --------------- #
    # A seed description like "Restaurant point-of-sale, payments, online
    # ordering, payroll, and operations platform" is really two claims, and a
    # lookalike has to satisfy them differently:
    #
    #   anchor       — who the company sells to + what it fundamentally is
    #                  ("restaurant point-of-sale platform"). A HARD match:
    #                  every lookalike must clear it, no exceptions. Write it
    #                  at the coarsest level that still defines the space —
    #                  too narrow rejects genuine peers, too broad makes the
    #                  gate a no-op and hands all the work to capabilities.
    #   capabilities — what the company does ("payments", "online ordering",
    #                  "payroll", "operations"). ANY non-empty subset counts;
    #                  a candidate needn't match all of them.
    #
    # relevant = anchor_match AND (>= 1 capability matched). See
    # judge.llm_judge.JUDGE_SYSTEM_PROMPT. Both fall back to `description` when
    # absent so a partially-migrated seed set still runs.
    anchor: str | None = None
    capabilities: list[str] = dataclasses.field(default_factory=list)

    # Verbatim NL query for the text-query tools (Exa, Parallel), authored
    # per seed rather than composed from a template. Both tools receive the
    # SAME string — that identity is the benchmark's fairness property. When
    # absent, the adapters compose one from name/domain/short_description.
    query: str | None = None
    # Optional, public firmographic hints (NOT derived from the
    # ground truth). Lets an adapter use a tool's documented firmographic filter
    # surface (e.g. locations / employee range / funding stages) so
    # each API can put its best foot forward. Shape:
    #   {"locations": ["USA"], "min_employees": 10, "max_employees": 2000,
    #    "funding_stages": ["seed", "series_a", ...]}
    firmographics: dict[str, Any] | None = None


@dataclasses.dataclass
class Candidate:
    """A single lookalike returned by a tool. We persist whatever fields
    the tool returned so the judge has enough to score; the runner
    normalizes name/domain into the canonical pair."""

    name: str
    domain: str | None = None
    description: str | None = None
    rank: int | None = None
    # Free-form tool payload so we don't drop signal (industry, employee
    # count, funding, etc.) — useful for judge context.
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class RunResult:
    """One tool run for one (seed, config)."""

    seed_slug: str
    provider_slug: str
    config_name: str
    config: dict[str, Any]
    candidates: list[Candidate]
    latency_ms: int
    cost_usd: float | None = None
    error: str | None = None
    requested_k: int | None = None
    # Post-fetch quality controls applied uniformly to every tool.
    # `duplicates_removed` is retained in artifacts so a short returned list
    # can be distinguished from a tool returning the same company twice.
    duplicates_removed: int = 0
    # Rows the tool sent, before the seed and duplicates were removed (None on older runs).
    # Per-row tools bill for these, so cost is priced on them.
    sent_count: int | None = None
    # Some tool endpoints have a hard result ceiling below the benchmark
    # depth. Their supported cutoffs remain comparable; deeper ones are N/A.
    max_results: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_slug": self.seed_slug,
            "provider_slug": self.provider_slug,
            "config_name": self.config_name,
            "config": self.config,
            "candidates": [
                {
                    "name": c.name,
                    "domain": c.domain,
                    "description": c.description,
                    "rank": c.rank,
                    "extra": c.extra,
                }
                for c in self.candidates
            ],
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "error": self.error,
            "requested_k": self.requested_k,
            "duplicates_removed": self.duplicates_removed,
            "sent_count": self.sent_count,
            "max_results": self.max_results,
        }


@dataclasses.dataclass
class JudgeVote:
    """One judge model's verdict on one candidate. A cell with N judges stores N
    of these per candidate; the candidate's `relevant` is the majority over them."""

    judge_model: str
    relevant: bool
    rationale: str


@dataclasses.dataclass
class JudgedCandidate:
    candidate: Candidate
    relevant: bool          # anchor_match AND >=1 capability matched (majority across `votes`)
    rationale: str
    # Per-judge breakdown. Empty for legacy single-judge constructions; populated
    # by JudgePanel. The snapshot carries only the aggregate `relevant`/`rationale`;
    # full votes live in the per-cell detail and audit files.
    votes: list[JudgeVote] = dataclasses.field(default_factory=list)
    # Did the candidate clear the seed's anchor (the hard gate)? False here
    # forces `relevant` False regardless of capability overlap.
    anchor_match: bool | None = None
    # Which of the seed's `capabilities` this candidate matched. Lets a cell be
    # read as "found the right kind of company, overlapping 3 of 4 capabilities"
    # rather than a bare boolean.
    capabilities_matched: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class JudgedRun:
    """A RunResult after the judge has scored each candidate."""

    run: RunResult
    judged: list[JudgedCandidate]
    judge_model: str
    judged_at: str  # ISO-8601 UTC

    @property
    def k(self) -> int:
        return self.run.requested_k or len(self.judged)

    @property
    def relevant_count(self) -> int:
        return sum(1 for j in self.judged if j.relevant)

    @property
    def precision_at_k(self) -> float | None:
        if self.k == 0:
            return None
        return round(100.0 * self.relevant_count_at(self.k) / self.k, 2)

    def relevant_count_at(self, cutoff: int) -> int:
        return sum(1 for j in self.judged[:cutoff] if j.relevant)

    def precision_at(self, cutoff: int) -> float | None:
        if cutoff <= 0:
            return None
        return round(100.0 * self.relevant_count_at(cutoff) / cutoff, 2)


@dataclasses.dataclass
class RawHttpCall:
    """One round-trip captured for the open-source audit trail. Auth
    headers redacted. `response_body` is the parsed JSON if the response
    decoded cleanly, else the raw text."""

    method: str
    url: str
    request_headers: dict[str, str]
    request_body: Any
    response_status: int
    response_body: Any
    response_text_preview: str  # first 4kb of body for quick eyeballing
    elapsed_ms: int


@dataclasses.dataclass
class RawJudgeCall:
    """One judge round-trip — exactly what the LLM saw and exactly what
    it returned. `messages` is the literal prompt; `raw_response` is the
    JSON string the model returned (before pydantic parsing). Anyone can
    rerun the same messages against their own LLM and check drift."""

    model: str
    messages: list[dict[str, str]]
    raw_response: str
    parsed_response: dict[str, Any] | None
    elapsed_ms: int
    for_candidate_rank: int | None = None  # which candidate this judged
    error: str | None = None  # if the LLM call failed, what went wrong
    # True when the verdict was served from the process-wide verdict cache
    # (same judge + prompt version + seed + candidate already judged this run,
    # e.g. via another config or tool) — no LLM call was made.
    cached: bool = False
    # Token usage reported by the provider for this call (None when cached or
    # failed): input, cached_input, cache_write, output, reasoning. Kept here so
    # judge cost is reconstructable from the raw files.
    usage: dict[str, int | None] | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def take_top(candidates: Iterable[Candidate], k: int) -> list[Candidate]:
    out: list[Candidate] = []
    for i, c in enumerate(candidates):
        if i >= k:
            break
        if c.rank is None:
            c.rank = i + 1
        out.append(c)
    return out
