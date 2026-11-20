# data

No seed companies or run outputs are committed to this folder.

## Public vs. held-out evaluation sets

- **Public split:** 10 seed companies published on Hugging Face at [`openbenchmarks/OB-Company-Lookalikes`](https://huggingface.co/datasets/openbenchmarks/OB-Company-Lookalikes).
- **Held-out split (private):** A private seed set for the next version of the board stays out of this repository so tools cannot tune to it. The current leaderboard's 48 seeds are public on openbenchmarks.com.
- **Local runs:** The runner reads seed companies from `data/latest-lookalike.json` (gitignored) and writes scored results back under `data/`. Use `--mock` to test the pipeline without a seed file or API keys.

## Files and schema

`data/latest-lookalike.json` is a JSON object with:

| Field | Type | Description |
|---|---|---|
| `dataset_slug` | string | Name of the dataset or run. |
| `k` | integer | Number of lookalikes requested per seed (default `100`). |
| `seeds` | array | Seed companies, each with `seed_slug`, `seed_name`, `seed_domain`, `description`, and `category`. Optional fields: `short_description`, `anchor`, `capabilities`, and `query`. |
| `seed_vendors` | object | Result cells per `(seed, tool)`, populated by the runner. |
| `leaderboard` | array | Aggregated board rows per tool, rebuilt after each run. |

Per-cell details are written to `data/lookalike-runs/<dataset>/<seed>/<tool>.json`, with sanitized request and judge logs in `<tool>.raw.json` beside it.
