"""context.dev company search, ultra mode: the fast adapter's request and paging, with
`mode: ultra`. A separate tool on the board, since the two modes differ in depth and price."""
from __future__ import annotations

from typing import Any

from ..models import RunResult, Seed
from ..spec_runner import run_from_spec
from ..tool_specs import build_spec
from .context_company_fast import request_template

SPEC = build_spec("context-company-ultra", request_template("ultra"))
VENDOR_SLUG = SPEC.slug
VENDOR_NAME = SPEC.name
CONFIGS: list[dict[str, Any]] = SPEC.configs


def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    return run_from_spec(SPEC, seed, k, config)
