# data

No seed companies or run outputs are committed to this folder.

## Seed sets

- **Private set:** 285 seed companies, held out; the published leaderboard is computed on it.
- **Validation set:** 74 seed companies, shared with the tools' vendors with per-seed results.
- **Public sample:** 10 seed companies in the same format, on Hugging Face at [`openbenchmarks/OB-Company-Lookalikes`](https://huggingface.co/datasets/openbenchmarks/OB-Company-Lookalikes).
- **Local runs:** The runner reads seed companies from `data/latest-lookalike.json` (gitignored) and writes scored results back under `data/`. Use `--mock` to run the pipeline without API keys (a seed file is still needed).

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
