# OB-Company-Search-Lookalikes

Open, independent benchmark of company lookalike APIs. Each tool returns up to 100 similar companies for every seed company, a three-model LLM judge panel scores every company returned, and tools are ranked by Precision@100, with Precision@10 for the top of the list.

The live leaderboard currently shows the previous board (2026 Q3: 10 tools on 48 seeds, judged by claude-opus-5, gpt-5.6-terra and kimi-k3). The v2 board, produced by this version of the harness, will replace it.

**Live leaderboard:** [openbenchmarks.com/lookalikes](https://openbenchmarks.com/lookalikes) · **Public dataset:** [`openbenchmarks/OB-Company-Lookalikes`](https://huggingface.co/datasets/openbenchmarks/OB-Company-Lookalikes)

Published and maintained by **[OpenBenchmarks Labs](https://openbenchmarks.com)**.

## Methodology

For each seed company, every tool returns an ordered list of candidate lookalike companies (default depth `k = 100`).

- **Relevance rule:** A returned company counts as a valid lookalike when it matches the seed's **anchor** (what every lookalike must be) and at least one **capability**, decided by majority vote of a three-LLM panel from three independent labs (`judge/llm_judge.py`).
- **Metrics:** Precision@10, Precision@100 (headline metric), relevant lookalikes per dollar, recall, nDCG, MAP, and MRR (`judge/metrics.py`).
- **Empty answers:** A seed the tool answers with no usable list (zero companies, or "company not found" for the seed) counts as 0 in its averages. Other failures (timeouts, server errors, a setup skipped for the seed) may be on our side, so they stay out of the averages and are counted on the board row (`judge/leaderboard.py`).
- **Auditability:** Each `(seed, tool)` cell records both the raw provider request/response and the judge panel prompts/responses (with API keys stripped).

## Tools evaluated

16 tools configured in [`tools/`](tools/README.md): 13 APIs and three model-only baselines. Lusha is configured but not scored, because its lookalike endpoint needs at least five seed companies per request.

`companyenrich`, `cufinder`, `discolike`, `exa`, `extruct`, `lusha`, `ocean`, `parallel`, `parallel-task`, `parallel-task-core`, `predictleads`, `seltz`, `zoominfo`

Model-only baselines (a model answers from its own knowledge, with no web access): `model-only-gpt-5.6-sol`, `model-only-claude-opus-5-5`, `model-only-kimi-k3`

## Dataset

No seed files or run dumps are committed to this repository.

- **Private set:** 285 seed companies. Held out; the published leaderboard is computed on it.
- **Validation set:** 74 seed companies, shared with the tools' vendors with per-seed results.
- **Public sample:** 10 seed companies in the same format, on Hugging Face at [`openbenchmarks/OB-Company-Lookalikes`](https://huggingface.co/datasets/openbenchmarks/OB-Company-Lookalikes), for running the harness yourself.

Place your seed file at `data/latest-lookalike.json` before running. `--mock` runs the same pipeline with a fake tool and judge, no API keys needed; it still needs the seed file.

See [`data/README.md`](data/README.md) for the `latest-lookalike.json` schema and output layout.

## Repository structure

- [`data/`](data/README.md): Schema for `latest-lookalike.json` and run output layout (no seed files committed).
- [`harness/`](harness/README.md): CLI runner (`harness/run.py`), re-judge CLI (`harness/rejudge.py`) and per-tool API adapters.
- [`tools/`](tools/README.md): One `<tool>.yaml` config per vendor with setups and pricing (16 tools).
- [`judge/`](judge/README.md): Three-model judge panel, precision/ranking metrics, and leaderboard builder.
- [`benchmark.yaml`](benchmark.yaml): Benchmark contract, scope, and refresh cadence.
- [`AGENTS.md`](AGENTS.md): Entry guide for coding agents.

## Quick start

Run the offline test suite (no API keys required):

```sh
uv venv && uv pip install -r requirements.txt pytest
.venv/bin/python -m pytest
```

To run against live APIs, place your seed file at `data/latest-lookalike.json` (see [`data/README.md`](data/README.md)), copy `.env.example` to `.env`, add your keys, and inspect the runner:

```sh
cp .env.example .env
.venv/bin/python -m harness.run --help
```

Add `--mock` to run end to end with a fake tool and judge instead of live APIs.

## Adding or updating a tool

1. Add or edit `tools/<tool>.yaml` (and add `harness/adapters/<tool>.py` plus one line in `harness/adapters/__init__.py` if adding a new API).
2. Run `.venv/bin/python -m pytest`.
3. Open a pull request.

## Links

- [Live leaderboard](https://openbenchmarks.com/lookalikes) and [methodology](https://openbenchmarks.com/lookalikes#methodology)
- [JSON API](https://openbenchmarks.com/api/benchmarks/lookalikes) (CC-BY-4.0)
- [Hugging Face dataset](https://huggingface.co/datasets/openbenchmarks/OB-Company-Lookalikes)
- [`llms.txt`](https://openbenchmarks.com/llms.txt)

## Related benchmarks

- [OB-Company-Search-Multi-Turn](https://github.com/openbenchmarks-labs/OB-Company-Search-Multi-Turn): Multi-hop company research agents ([leaderboard](https://openbenchmarks.com/multi-turn-company-search))
- [OB-Company-Enrichment](https://github.com/openbenchmarks-labs/OB-Company-Enrichment): Core firmographic enrichment ([leaderboard](https://openbenchmarks.com/company-enrichment))
- [OB-Company-Funding](https://github.com/openbenchmarks-labs/OB-Company-Funding): Latest funding round accuracy and freshness ([leaderboard](https://openbenchmarks.com/company-funding))
- [All OpenBenchmarks leaderboards](https://openbenchmarks.com)

## License

MIT. See [LICENSE](LICENSE).
