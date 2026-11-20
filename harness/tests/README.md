# tests

Offline tests for the harness. No network: each test swaps the HTTP call for a stand-in.

- `test_firmographic_configs.py` — The request each tool sends per setup, seed removal, duplicate removal and PredictLeads paging.
- `test_seltz.py` — Reading Seltz's Markdown pages and the request Seltz receives.
- `test_cost.py` — Call prices worked out from `tools/<tool>.yaml`.
