"""Parallel Task API on the core processor, with the count in the schema.

Parallel's suggestion for longer lists: the core processor (base is sized for enrichments of a
few fields), and the array described as holding exactly K companies with `minItems: K`. Parallel's
docs list `minItems` as unsupported; if the create call rejects it, the run is sent again without
it and the rejection is kept in the cell's config. Everything else is `parallel_task.run_task`.
"""
from __future__ import annotations

from typing import Any

from ..models import RunResult, Seed
from ..tool_specs import load_tool
from .parallel_task import run_task

TOOL = load_tool("parallel-task-core")
VENDOR_SLUG = TOOL.tool
VENDOR_NAME = TOOL.name
CONFIGS: list[dict[str, Any]] = TOOL.configs


def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    return run_task(TOOL, seed, k, config)
