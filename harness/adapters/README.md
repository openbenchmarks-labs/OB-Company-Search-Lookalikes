# adapters

One file per tool. Each sends the tool's request for one seed and turns the reply into candidates. Setups and prices live in `tools/<tool>.yaml`.

- `__init__.py` — The list of tools the runner calls, one line per tool.
- `companyenrich.py` — CompanyEnrich company search in lookalike mode, with optional country and size filters.
- `cufinder.py` — CUFinder lookalike finder; returns at most 10 companies.
- `discolike.py` — Discolike domain lookalikes.
- `exa.py` — Exa company search with a seed-first sentence; one result per domain.
- `extruct.py` — Extruct similar companies for the seed's domain.
- `model_only.py` — Model-only baselines (GPT-5.6 Sol, Claude Opus 5.5, Kimi K3): the model lists companies from its own knowledge, then describes each from its fetched homepage without seeing the seed.
- `lusha.py` — Lusha lookalikes. Not on the board: it needs 5 or more seeds per request.
- `ocean.py` — Ocean.io company search seeded with the seed's domain, with optional filters.
- `parallel.py` — Parallel entity search with a seed-first sentence; keeps profile sites out of the domain.
- `parallel_task.py` — Parallel Task API: one research task per seed on the base processor, returning up to K companies.
- `parallel_task_core.py` — Parallel Task API on the core processor, with the count in the output schema.
- `predictleads.py` — PredictLeads similar companies; returns about 25 companies.
- `seltz.py` — Seltz company search; reads each company out of a Markdown page.
- `zoominfo.py` — ZoomInfo similar companies through the `gtm` command-line tool; returns at most 25.
