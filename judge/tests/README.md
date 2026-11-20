# tests

Offline tests for grading. No API keys: the judge runs in mock mode.

- `test_judge_panel.py` — Majority voting, judge labels, the anchor and capability rule, and that every judge call is recorded.
- `test_metrics.py` — Recall against the pool of relevant companies and the main score choice.
- `test_leaderboard.py` — Relevant companies per dollar and latency on the board.
