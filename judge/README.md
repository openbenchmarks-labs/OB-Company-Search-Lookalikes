# judge

Decides which returned companies are real lookalikes and builds the board. No tool or HTTP code lives here.

- `llm_judge.py` — The judge prompt and the three-model judge panel. A company counts as relevant when it matches the seed's anchor and at least one capability, by majority vote.
- `metrics.py` — Scores for one cell: Precision@K (the main score), recall, nDCG, MAP and MRR.
- `leaderboard.py` — Rolls the cells up into one board row per tool: average precision, cost, relevant companies per dollar and latency.
- `tests/` — Offline tests for the judge panel, metrics and board.
