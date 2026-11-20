"""Loads a tool's setups and price from `tools/<tool>.yaml` and pairs them with
the request template its adapter declares.

The spec runner (`spec_runner.py`) turns the resulting `VendorSpec` into one
request per setup and reads the response into `Candidate`s, calling the
adapter's hook functions for the parts a template cannot express.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .http_client import REDACTED_HEADER_NAMES

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"

# Path values in the item map / audit map: a dotted path string, or a list of
# fallback paths (first truthy wins).
PathRef = Any  # str | list[str] | None
Hook = Optional[Callable[..., Any]]


class CostSpec(BaseModel):
    """List price of one call. `per_call` is a flat fee per request; `per_result` is a fee per
    row the tool sends (seed and duplicates included, since that is what it bills for), capped
    at the depth asked for; `list` is both. `per_credit` is a fee per credit the tool reports
    using, priced by its adapter. `source` says where the rate comes from."""
    model_config = ConfigDict(extra="forbid")
    mode: str = "none"  # none | per_call | per_result | list | per_credit
    usd: Optional[float] = None             # flat fee per request (per_call, list)
    per_result_usd: Optional[float] = None  # fee per row sent (per_result, list)
    usd_per_credit: Optional[float] = None  # fee per credit used (per_credit)
    source: Optional[str] = None

    @model_validator(mode="after")
    def _check(self) -> "CostSpec":
        if self.mode not in ("none", "per_call", "per_result", "list", "per_credit"):
            raise ValueError(f"cost.mode must be none|per_call|per_result|list|per_credit, got {self.mode!r}")
        if self.mode == "per_call" and self.usd is None:
            raise ValueError("cost.usd is required for per_call")
        if self.mode == "per_result" and self.per_result_usd is None:
            raise ValueError("cost.per_result_usd is required for per_result")
        if self.mode == "list" and (self.usd is None or self.per_result_usd is None):
            raise ValueError("cost.usd and cost.per_result_usd are required for list")
        if self.mode == "per_credit" and self.usd_per_credit is None:
            raise ValueError("cost.usd_per_credit is required for per_credit")
        return self


class ToolConfig(BaseModel):
    """One `tools/<tool>.yaml`: the tool's slug, display name, price and setups."""
    model_config = ConfigDict(extra="forbid")
    tool: str
    name: str
    pricing: CostSpec = Field(default_factory=CostSpec)
    setups: dict[str, Optional[dict[str, Any]]]

    @property
    def configs(self) -> list[dict[str, Any]]:
        """Setups in file order, each as a config dict that starts with its name."""
        return [{"name": name, **(knobs or {})} for name, knobs in self.setups.items()]


def load_tool(filename: str) -> ToolConfig:
    path = TOOLS_DIR / f"{filename}.yaml"
    return ToolConfig(**yaml.safe_load(path.read_text(encoding="utf-8")))


class AuthHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")
    header: str
    env: str
    value_prefix: str = ""


class MergeDirective(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_config: str  # key in the config dict holding a sub-dict to merge
    into_body: str     # body key to shallow-merge that sub-dict into


class RequestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str
    path: str
    query: Optional[dict[str, Any]] = None
    body: Any = None
    merge: list[MergeDirective] = Field(default_factory=list)


class ItemMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: PathRef = None
    domain: PathRef = None
    description: PathRef = None
    rank: Optional[str] = None
    extra: dict[str, PathRef] = Field(default_factory=dict)


class ResponseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates_path: list[str]
    item: Optional[ItemMap] = None


class HooksSpec(BaseModel):
    """Hook stages (all optional per tool):
      - preflight(ctx) -> None        runs first, before templating; may make HTTP
                                      calls (captured); mutates ctx["vars"] and
                                      ctx["vars"]["_audit"].
      - build_request(ctx) -> None    runs before templating; pure var computation
                                      (objective, num_results, conditional query).
      - transform_candidates(raw_items, ctx) -> list[Candidate]
                                      replaces the declarative field-map when the
                                      tool needs cross-referencing / dedupe / sort.
                                      ctx["payload"] holds the full parsed response.
    """
    model_config = ConfigDict(extra="forbid")
    preflight: Hook = None
    build_request: Hook = None
    transform_candidates: Hook = None


class VendorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    name: str
    base_url: str
    requires_seed_domain: bool = False
    no_domain_error: Optional[str] = None  # custom message for the guard
    timeout_sec: int = 60
    cost: CostSpec = Field(default_factory=CostSpec)
    auth: list[AuthHeader] = Field(default_factory=list)
    request: RequestSpec
    response: ResponseSpec
    hooks: HooksSpec = Field(default_factory=HooksSpec)
    # audit: ordered map of config-key -> source, merged into the returned
    # config on success. source "$audit.X" reads ctx["vars"]["_audit"][X]
    # (also applied on the error path); any other value is a response path.
    audit: dict[str, str] = Field(default_factory=dict)
    configs: list[dict[str, Any]]

    @model_validator(mode="after")
    def _validate(self) -> "VendorSpec":
        if self.request.body is not None and self.request.method.upper() == "GET":
            raise ValueError(f"spec {self.slug}: GET request must not declare a body")
        if not self.hooks.transform_candidates and self.response.item is None:
            raise ValueError(
                f"spec {self.slug}: response.item is required when no transform_candidates hook is set"
            )
        for a in self.auth:
            if a.header.lower() not in REDACTED_HEADER_NAMES:
                raise ValueError(
                    f"spec {self.slug}: auth header {a.header!r} is not in REDACTED_HEADER_NAMES "
                    "— add it to http_client.REDACTED_HEADER_NAMES so it is redacted in audit artifacts"
                )
        return self

    def auth_env_keys(self) -> list[str]:
        return [a.env for a in self.auth]


def build_spec(filename: str, request_template: dict[str, Any]) -> VendorSpec:
    """Join `tools/<filename>.yaml` (slug, name, price, setups) with an adapter's
    request template (base_url, auth, request, response, hooks)."""
    tool = load_tool(filename)
    return VendorSpec(slug=tool.tool, name=tool.name, cost=tool.pricing, configs=tool.configs,
                      **request_template)
