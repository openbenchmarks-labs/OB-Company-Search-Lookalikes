"""Parallel Task API adapter: one web-research task per seed that returns its lookalikes.

The Task API runs asynchronously:
  1. POST /v1/tasks/runs with the processor, the input and an output schema -> run_id
  2. GET  /v1/tasks/runs/{run_id}/result?timeout=... blocks until the run finishes
     (HTTP 408 means it is still running; ask again)

The input is the seed's authored query, the string Exa and Parallel entity search receive, with the
count stated up front ("Return 100 companies similar to ..."). The output schema asks for up to K
companies with name, domain and description, most similar first. The schema cannot enforce a list
length, so a short list is scored as misses, like any tool that returns fewer than K.

The task writes each company's description after reading the query, which carries the seed's
identity, and the judges read those descriptions. Keep that in mind when comparing it with tools
whose descriptions come from a company database.

Each list item's citations (from the run's `basis`) are kept on the candidate for the audit trail.

`parallel_task_core.py` is the same task on the core processor with the count in the schema.

Three API behaviours are handled here:
  - A schema rejection on create has arrived both as an error status and as HTTP 200 with an
    error body, so a create is treated as rejected whenever the reply has no `run_id`.
  - "Still running" is documented as HTTP 408 but has also arrived as HTTP 200 with
    {"type": "error", "error": {"message": "Run still active."}}.
  - A result call that never answers (a dropped long-poll) is asked again rather than failing the
    cell, since the run is already paid for.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

from ..http_client import http_request, require_env
from ..models import Candidate, RunResult, Seed, take_top
from ..spec_runner import _normalized_domain, _without_duplicates, _without_seed
from ..tool_specs import ToolConfig, load_tool

BASE_URL = "https://api.parallel.ai"
RESULT_WAIT_SEC = 120      # one blocking result call; 408 means still running
MAX_WAIT_SEC = 3600        # give up on a run after an hour
RESULT_RETRY_PAUSE_SEC = 5

COMPANY_FIELDS = {
    "name": {"type": "string", "description": "The company's name."},
    "domain": {
        "type": "string",
        "description": "The company's primary website domain, for example stripe.com. Not a LinkedIn or Crunchbase URL.",
    },
    "description": {
        "type": "string",
        "description": "One to three sentences on what the company sells and to whom.",
    },
}


def output_schema(k: int, count_in_schema: bool = False, min_items: bool = False) -> dict[str, Any]:
    if count_in_schema:
        about = (f"Must contain exactly {k} real companies, most similar to the seed company first. "
                 "Exclude the seed company itself, its parent and its subsidiaries.")
    else:
        about = (f"Up to {k} real companies most similar to the seed company, ranked most similar first. "
                 "Exclude the seed company itself, its parent and its subsidiaries.")
    companies: dict[str, Any] = {
        "type": "array",
        "description": about,
        "items": {
            "type": "object",
            "properties": COMPANY_FIELDS,
            "required": list(COMPANY_FIELDS),
            "additionalProperties": False,
        },
    }
    if min_items:
        companies["minItems"] = k
    return {
        "type": "json",
        "json_schema": {
            "type": "object",
            "properties": {"companies": companies},
            "required": ["companies"],
            "additionalProperties": False,
        },
    }


def build_input(seed: Seed, k: int) -> str:
    """The seed's authored query with the count stated up front: "Find companies similar to X ..."
    becomes "Return K companies similar to X ..."."""
    query = seed.query or f"Find companies similar to {seed.seed_name} ({seed.seed_domain or 'no domain'})."
    lead = "Find companies similar to "
    if query.startswith(lead):
        return f"Return {k} companies similar to " + query[len(lead):]
    return f"Return {k} companies. {query}"


def build_request(seed: Seed, k: int, config: dict[str, Any], min_items: bool | None = None) -> dict[str, Any]:
    """The create-run body. With `count_in_schema`, the schema also carries `minItems: K`
    unless `min_items=False`."""
    count = bool(config.get("count_in_schema"))
    schema = output_schema(k, count_in_schema=count, min_items=count if min_items is None else min_items)
    return {"processor": config["processor"], "input": build_input(seed, k), "task_spec": {"output_schema": schema}}


def _citations(basis: list[dict[str, Any]] | None) -> dict[int, list[str]]:
    """Citation URLs per list index, from basis fields like `companies.3` or `companies.3.domain`."""
    out: dict[int, list[str]] = {}
    for b in basis or []:
        parts = str(b.get("field") or "").split(".")
        if len(parts) >= 2 and parts[0] == "companies" and parts[1].isdigit():
            urls = [c.get("url") for c in b.get("citations") or [] if c.get("url")]
            seen = out.setdefault(int(parts[1]), [])
            seen += [u for u in urls if u not in seen]
    return out


def _resume_run_id(seed: Seed, slug: str) -> str | None:
    """A run already created for this seed, from PARALLEL_TASK_RESUME (a JSON file of
    {"<tool slug>/<seed slug>": "<run_id>"}). Lets a run whose result fetch failed be fetched
    again instead of paying for a new one."""
    path = os.environ.get("PARALLEL_TASK_RESUME")
    if not path:
        return None
    with open(path) as f:
        return json.load(f).get(f"{slug}/{seed.seed_slug}")


def _create_rejected(status: int, payload: Any) -> bool:
    return status >= 300 or not isinstance(payload, dict) or not payload.get("run_id")


def _still_active(status: int, result: Any) -> bool:
    if status == 408:
        return True
    if isinstance(result, dict) and result.get("type") == "error":
        msg = str((result.get("error") or {}).get("message") or "")
        return "still active" in msg.lower()
    return False


def _run_seconds(result: dict[str, Any]) -> float | None:
    """Server-side run time from the run's created_at and modified_at."""
    run = result.get("run") or {}
    try:
        start, end = (datetime.fromisoformat(str(run[f]).replace("Z", "+00:00")) for f in ("created_at", "modified_at"))
    except (KeyError, ValueError):
        return None
    return (end - start).total_seconds()


def run_task(tool: ToolConfig, seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    slug = tool.tool
    headers = {"x-api-key": require_env("PARALLEL_API_KEY")["PARALLEL_API_KEY"]}
    started = time.monotonic()
    resumed = _resume_run_id(seed, slug)
    if resumed:
        status, payload = 202, {"run_id": resumed}
    else:
        status, payload, _ = http_request("POST", f"{BASE_URL}/v1/tasks/runs", headers=headers,
                                          body=build_request(seed, k, config), timeout=60)
    min_items_rejected = None
    if not resumed and config.get("count_in_schema") and _create_rejected(status, payload):
        # minItems is listed as unsupported; send the same run without it and record why.
        min_items_rejected = f"HTTP {status}: {str(payload)[:300]}"
        status, payload, _ = http_request("POST", f"{BASE_URL}/v1/tasks/runs", headers=headers,
                                          body=build_request(seed, k, config, min_items=False), timeout=60)
    if _create_rejected(status, payload):
        return RunResult(seed.seed_slug, slug, config["name"], config, [],
                         int((time.monotonic() - started) * 1000),
                         error=f"create HTTP {status}: {str(payload)[:200]}", requested_k=k)

    run_id = payload["run_id"]
    status, result = 408, {"detail": "not fetched"}
    poll_errors: list[str] = []
    while True:
        try:
            status, result, _ = http_request(
                "GET", f"{BASE_URL}/v1/tasks/runs/{run_id}/result?timeout={RESULT_WAIT_SEC}",
                headers=headers, timeout=RESULT_WAIT_SEC + 60)
        except (TimeoutError, OSError) as exc:  # socket timeout, URLError, connection reset
            poll_errors.append(f"{type(exc).__name__}: {exc}")
            status, result = 408, {"detail": "result call failed", "errors": poll_errors[-3:]}
            time.sleep(RESULT_RETRY_PAUSE_SEC)
        if _still_active(status, result):
            status = 408
        if status != 408 or time.monotonic() - started > MAX_WAIT_SEC:
            break

    elapsed_ms = int((time.monotonic() - started) * 1000)
    if resumed and isinstance(result, dict) and _run_seconds(result) is not None:
        # The wall clock started after the run did; the run's own timestamps give its latency.
        elapsed_ms = int(_run_seconds(result) * 1000)
    out_config = {**config, "run_id": run_id, **({"resumed_run": True} if resumed else {}),
                  **({"min_items_rejected": min_items_rejected} if min_items_rejected else {}),
                  **({"result_poll_errors": poll_errors} if poll_errors else {})}
    if status >= 300 or not isinstance(result, dict):
        return RunResult(seed.seed_slug, slug, config["name"], out_config, [], elapsed_ms,
                         error=f"result HTTP {status}: {str(result)[:200]}", requested_k=k)

    output = result.get("output") or {}
    content = output.get("content")
    rows = (content.get("companies") or []) if isinstance(content, dict) else []
    cites = _citations(output.get("basis"))
    listed = [
        Candidate(
            name=str(row.get("name") or "").strip(),
            domain=_normalized_domain(row.get("domain")),
            description=(str(row.get("description") or "").strip() or None),
            extra={"basis_citations": cites.get(i, [])[:5]},
        )
        for i, row in enumerate(rows)
        if isinstance(row, dict) and (row.get("name") or row.get("domain"))
    ]
    sent = len(listed)
    kept, duplicates = _without_duplicates(_without_seed(listed, seed))
    return RunResult(seed.seed_slug, slug, config["name"], out_config, take_top(kept, k), elapsed_ms,
                     cost_usd=tool.pricing.usd, requested_k=k, duplicates_removed=duplicates, sent_count=sent)


TOOL = load_tool("parallel-task")
VENDOR_SLUG = TOOL.tool
VENDOR_NAME = TOOL.name
CONFIGS: list[dict[str, Any]] = TOOL.configs


def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    return run_task(TOOL, seed, k, config)
