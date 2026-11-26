# harness

Runs the benchmark and calls each tool's API. No grading code lives here.

- `run.py` — Command-line runner: asks every tool for lookalikes of every seed, has the judge score them, and saves the results.
- `rejudge.py` — Scores saved runs again with the current judge, without calling any tool.
- `adapters/` — One file per tool with the exact request it sends and how its reply is read.
- `spec_runner.py` — Sends a tool's request, reads the reply into candidates, drops the seed and duplicates, and prices the call.
- `tool_specs.py` — Loads a tool's setups and price from `tools/<tool>.yaml`.
- `http_client.py` — Reads keys from `.env` and makes every HTTP call, keeping a copy of each with keys removed.
- `models.py` — Shared data types: seed, candidate, run result and judge verdict.
- `snapshot.py` — Reads the seed companies and writes result cells and detail files under `data/`.
- `mock_tool.py` — A fake tool for offline runs (`--mock`).
