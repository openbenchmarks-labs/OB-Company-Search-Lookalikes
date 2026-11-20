# AGENTS.md — OB-Company-Search-Lookalikes

Quick navigation guide for coding agents working in `OB-Company-Search-Lookalikes`.

## Structure (Zero Overlap)

| Folder | Owns | Files |
|---|---|---|
| **`data/`** | Seed companies file format and where results are saved | `data/README.md` |
| **`harness/`** | Execution only: CLI runner, re-judge CLI, tool adapters, spec runner, HTTP client, result files | `harness/run.py`, `harness/rejudge.py`, `harness/adapters/<tool>.py`, `harness/spec_runner.py`, `harness/tool_specs.py`, `harness/http_client.py`, `harness/models.py`, `harness/snapshot.py`, `harness/mock_tool.py`, `harness/tests/` |
| **`tools/`** | Tool setups & pricing (one file per tool for clean PRs) | `tools/<tool>.yaml` (11 tools) |
| **`judge/`** | Grading only: LLM judge panel and prompt, metrics, board math | `judge/llm_judge.py`, `judge/metrics.py`, `judge/leaderboard.py`, `judge/tests/` |

Benchmark order & win condition live at the root in `benchmark.yaml`.

## Commands

```sh
# Offline tests (no API keys)
uv venv && uv pip install -r requirements.txt pytest
.venv/bin/python -m pytest
```

## Guidelines

- **Keep folders strictly separated:** Seed file format in `data/`; execution and API calls in `harness/`; per-tool setups and prices in `tools/<tool>.yaml`; grading in `judge/`.
- **Adding or tuning a tool:** Edit `tools/<tool>.yaml` (and `harness/adapters/<tool>.py` plus its line in `harness/adapters/__init__.py` for a new API), then run the tests.
- **No secrets, seed sets or run outputs in git:** Keep API keys in `.env`; `data/latest-lookalike.json` and `data/lookalike-runs/` are gitignored.
- **Consistent terminology:** Use `tool`, `setup`, `seed`, `candidate`, `judge`, `judge panel`, `cell`, `board`, `Precision@K`.
