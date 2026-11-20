"""Seltz adapter: one search in Seltz's `companies` scope per seed.

Seltz's `companies` scope returns web `documents`, each a Markdown blob under
`content`, with the company's LinkedIn page as the top-level `url`. There is no
structured company object: the company name is an H1, the pitch lives under an
`## About` heading, and the firmographics are a `* Key: value` list under
`## Company Details` — including the canonical `Website`. So `parse_companies`
reads name / domain / description out of the Markdown instead of declarative
fields, and deliberately keeps the aggregator `url` (LinkedIn) out of `domain`.
Seltz can serve 100 results, so the benchmark's K=100 is always requested in full.
"""
from __future__ import annotations

from typing import Any

from ..models import Candidate, RunResult, Seed
from ..spec_runner import run_from_spec
from ..tool_specs import build_spec

# Hosts that are directory/profile pages, never a company's own website. Kept in
# sync with the same list in the Exa/Parallel hooks: a LinkedIn or Crunchbase URL
# must not land in `domain`, or the cross-vendor dedupe would key on it wrongly.
AGGREGATOR_HOSTS = {
    "linkedin.com", "tracxn.com", "platform.tracxn.com", "crunchbase.com",
    "wikipedia.org", "en.wikipedia.org", "bloomberg.com", "pitchbook.com",
}

def _compose_query(seed: Seed) -> str:
    """Compose the vendor query "<anchor> for <cap1>, <cap2>, ..., and <capN>"
    from the seed's structured anchor + capabilities."""
    caps = [c.strip() for c in seed.capabilities if c and c.strip()]
    if not caps:
        return (seed.anchor or seed.description or seed.seed_name).strip()
    if len(caps) == 1:
        joined = caps[0]
    elif len(caps) == 2:
        joined = f"{caps[0]} and {caps[1]}"
    else:
        joined = ", ".join(caps[:-1]) + ", and " + caps[-1]
    anchor = (seed.anchor or "").strip()
    return f"{anchor} for {joined}" if anchor else joined


def build_query(ctx: dict[str, Any]) -> None:
    """build_request: set the natural-language query from the seed's anchor +
    capabilities."""
    ctx["vars"]["query"] = _compose_query(ctx["seed"])


def _host(value: Any) -> str | None:
    """Normalize a website URL to a bare host, dropping aggregator domains."""
    if not isinstance(value, str) or not value.strip():
        return None
    host = value.strip().replace("https://", "").replace("http://", "").split("/")[0].lower()
    host = host[4:] if host.startswith("www.") else host
    if not host:
        return None
    if any(host == h or host.endswith("." + h) for h in AGGREGATOR_HOSTS):
        return None
    return host


def _section(content: str, title: str) -> str:
    """Return the text under `## <title>`, up to the next `## ` heading."""
    out: list[str] = []
    capturing = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            capturing = stripped[3:].strip().casefold() == title.casefold()
            continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def _details(content: str) -> dict[str, str]:
    """Parse the `* Key: value` list under `## Company Details` into a dict.

    Scoped to that section so `* ` bullets that occasionally appear inside the
    `## About` prose are never mistaken for firmographic fields. Falls back to
    the whole blob if the section header is absent."""
    block = _section(content, "Company Details") or content
    fields: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("* ") and ":" in stripped:
            key, value = stripped[2:].split(":", 1)
            key = key.strip().casefold()
            value = value.strip()
            if key and value:
                fields.setdefault(key, value)
    return fields


def _name(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return ""


def _subtitle(content: str) -> str | None:
    """The `**Industry · Type · Location**` line just under the H1."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4:
            return stripped.strip("*").strip()
    return None


def _description(content: str, fields: dict[str, str]) -> str | None:
    about = _section(content, "About")
    if about:
        return " ".join(about.split())  # collapse whitespace; no length cap
    return _subtitle(content)


def parse_companies(raw_items: list[Any], ctx: dict[str, Any]) -> list[Candidate]:
    """transform_candidates: one Candidate per document, parsed from Markdown.

    Order is preserved — Seltz returns documents in relevance rank, and the
    spec runner assigns rank by position after seed-removal and dedupe."""
    candidates: list[Candidate] = []
    for doc in raw_items:
        if not isinstance(doc, dict):
            continue
        content = doc.get("content") or ""
        if not isinstance(content, str):
            continue
        fields = _details(content)
        source_url = doc.get("url")

        name = _name(content) or fields.get("legal name") or fields.get("also known as") or ""
        domain = _host(fields.get("website"))
        linkedin_url = fields.get("linkedin") or (
            source_url if isinstance(source_url, str) and "linkedin.com" in source_url else None
        )
        name = name.strip()
        if not name and not domain:
            continue

        candidates.append(
            Candidate(
                name=name or (domain or ""),
                domain=domain,
                description=_description(content, fields),
                extra={
                    "linkedin_url": linkedin_url,
                    "source_url": source_url,
                    "industry": fields.get("industry"),
                    "company_type": fields.get("type"),
                    "size": fields.get("size"),
                    "employees": fields.get("employees"),
                    "founded": fields.get("founded"),
                    "headquarters": fields.get("headquarters"),
                    "region": fields.get("region"),
                    "business_model": fields.get("business model"),
                    "crunchbase": fields.get("crunchbase"),
                },
            )
        )
    return candidates


SPEC = build_spec("seltz", {
    "base_url": "https://api.seltz.ai/v1",
    "requires_seed_domain": False,
    "timeout_sec": 180,
    "auth": [{"header": "x-api-key", "env": "SELTZ_API_KEY"}],
    "request": {
        "method": "POST",
        "path": "/search",
        "body": {
            "query": "$query",          # composed from the seed's anchor + capabilities (build_query)
            "scope": "companies",       # always target Seltz's companies vertical
            "max_results": "$k",        # benchmark depth (K=100)
        },
    },
    "response": {"candidates_path": ["documents"]},
    "hooks": {"build_request": build_query, "transform_candidates": parse_companies},
})
VENDOR_SLUG = SPEC.slug
VENDOR_NAME = SPEC.name
CONFIGS: list[dict[str, Any]] = SPEC.configs


def run(seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    return run_from_spec(SPEC, seed, k, config)
