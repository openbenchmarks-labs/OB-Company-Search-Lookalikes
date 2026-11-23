"""Model-only baselines: a frontier model answers the lookalike request from its own knowledge,
with no web access, and is scored like any vendor.

Each model is registered as its own tool (see MODEL_VENDORS and tools/model-only-*.yaml). It
receives the same natural-language query the text-query tools (Exa, Parallel, Seltz) receive,
returns K companies as JSON, and goes through the unchanged pipeline: seed and duplicate removal,
the same judge panel, Precision@10 / @100.

Like a vendor, the model returns each company's description and firmographics (industry,
country, headcount band, founding year), and the judges see them the way they see any vendor's.
Those are the model's own claims rather than a database's, so every returned domain is also
checked in DNS and the result kept on the candidate (`extra.domain_resolves`): the share of
unverifiable companies is reported, and P@100 can be read with them counted as misses.

Calls go through http_client.http_request, so each raw file records the literal request and response
with the API key redacted, like every other tool.

Two modes, chosen by the config name:

- `model_only`: one call returns the companies with their descriptions.
  The model writes each description after reading the seed's anchor and capabilities, so it can
  describe a weak match in the terms the judges check, which makes the scoring circular.
- `list_then_describe` (default): the list call returns names and domains only. The harness then
  fetches each company's homepage, and the same model describes the company from that page in
  its own call, which sees only the company's name, domain and page text: no seed, no query, no
  other companies. The judges read those descriptions. The page text is in the description
  request, so the raw file records what the model was shown.
"""
from __future__ import annotations

import json
import re
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from ..http_client import http_request, require_env
from ..models import Candidate, RunResult, Seed, take_top
from ..spec_runner import _without_duplicates, _without_seed
from ..tool_specs import load_tool

SYSTEM_PROMPT = """You are a company lookalike service. Given a seed company, you return the companies most similar to it, the way a B2B lookalike API would.

You have no web access. Answer only from what you already know.

Rules:
- List real companies that exist today. Never invent a company or a domain. If you are unsure a company exists, leave it out.
- Give each company's primary website domain (for example "stripe.com"), not a LinkedIn or Crunchbase URL.
- Describe each company in one to three sentences: what it sells and to whom.
- Give its industry, headquarters country (ISO alpha-2 code), headcount band (1-10, 11-50, 51-200, 201-500, 501-1K, 1K-5K, 5K-10K, 10K+) and founding year, as best you know them.
- Do not include the seed company itself, its parent or its subsidiaries.
- Rank the most similar company first.
- Return {k} companies in total.

Return JSON only, in this shape:
{{"companies": [{{"name": "Company name", "domain": "example.com", "description": "What it sells and to whom.", "industry": "Software", "country": "US", "employees": "51-200", "founded_year": 2015}}]}}"""

USER_PROMPT = """Seed company: {seed_name} ({seed_domain})

{query}

Return total {k} companies."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "domain": {"type": "string"},
                    "description": {"type": "string"},
                    "industry": {"type": "string"},
                    "country": {"type": "string"},
                    "employees": {"type": "string"},
                    "founded_year": {"type": ["integer", "null"]},
                },
                "required": ["name", "domain", "description", "industry", "country", "employees", "founded_year"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["companies"],
    "additionalProperties": False,
}


LIST_SYSTEM_PROMPT = """You are a company lookalike service. Given a seed company, you return the companies most similar to it, the way a B2B lookalike API would.

You have no web access. Answer only from what you already know.

Rules:
- List real companies that exist today. Never invent a company or a domain. If you are unsure a company exists, leave it out.
- Give each company's primary website domain (for example "stripe.com"), not a LinkedIn or Crunchbase URL.
- Do not include the seed company itself, its parent or its subsidiaries.
- Rank the most similar company first.
- Return {k} companies in total.

Return JSON only, in this shape:
{{"companies": [{{"name": "Company name", "domain": "example.com"}}]}}"""

LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "domain": {"type": "string"}},
                "required": ["name", "domain"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["companies"],
    "additionalProperties": False,
}

DESCRIBE_SYSTEM_PROMPT = """You describe a company from its website.

You are given a company's name, its website domain, and the text of the page that domain serves, fetched just now.
- Describe the company in one to three sentences: what it sells and to whom. Base this on the page; use your own knowledge only for what the page does not say.
- If the domain redirects to, or now belongs to, a different company (for example after an acquisition), say so and describe what the site actually shows.
- If the page could not be fetched or says nothing about the company, say that in the description and describe it only as far as you are sure.
- Give its industry, headquarters country (ISO alpha-2 code), headcount band (1-10, 11-50, 51-200, 201-500, 501-1K, 1K-5K, 5K-10K, 10K+) and founding year, as best you know them. Use null where you do not know. Never guess.

Return JSON only, in this shape:
{"description": "What it sells and to whom.", "industry": "Software", "country": "US", "employees": "51-200", "founded_year": 2015}"""

DESCRIBE_USER_PROMPT = "Company: {name}\nWebsite: {domain}\n\n{page}"

PAGE_TEXT_CHARS = 6000
PAGE_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/130.0 Safari/537.36")

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "industry": {"type": ["string", "null"]},
        "country": {"type": ["string", "null"]},
        "employees": {"type": ["string", "null"]},
        "founded_year": {"type": ["integer", "null"]},
    },
    "required": ["description", "industry", "country", "employees", "founded_year"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ModelVendor:
    slug: str
    name: str
    model: str            # the API model id
    transport: str        # openai | anthropic
    url: str
    key_env: str
    # USD per million tokens (input, output); None until the rate is pinned, so cost stays null
    # rather than guessed.
    usd_per_m: tuple[float, float] | None = None
    max_tokens: int = 32_000  # 100 companies with descriptions
    describe_max_tokens: int = 8_000
    # Description calls in flight at once for this model, across all cells (provider rate limits).
    describe_concurrency: int = 48


# GPT-5.6 Sol is reached through the Azure OpenAI NEXTGEN deployment (OpenAI-v1 compatible route,
# the same one the judge panel uses), not api.openai.com. The resource URL comes from the env at call
# time, so the vendor's url is a sentinel that endpoint_url() resolves.
AZURE_NEXTGEN_URL_ENV = "AZURE_OPENAI_NEXTGEN_DEPLOYMENT_URL"
AZURE_NEXTGEN_KEY_ENV = "AZURE_OPENAI_NEXTGEN_DEPLOYMENT_KEY"
AZURE_NEXTGEN_SENTINEL = "azure-nextgen:"


def endpoint_url(v: "ModelVendor") -> str:
    """The URL a vendor's calls go to. `azure-nextgen:<path>` is the NEXTGEN resource's base URL
    from the env plus that path."""
    if v.url.startswith(AZURE_NEXTGEN_SENTINEL):
        base = require_env(AZURE_NEXTGEN_URL_ENV)[AZURE_NEXTGEN_URL_ENV].rstrip("/")
        return base + v.url[len(AZURE_NEXTGEN_SENTINEL):]
    return v.url


MODEL_VENDORS = (
    ModelVendor("model-only-gpt-5.6-sol", "GPT-5.6 Sol (model only)", "gpt-5.6-sol", "openai",
                AZURE_NEXTGEN_SENTINEL + "/chat/completions", AZURE_NEXTGEN_KEY_ENV, usd_per_m=(4.00, 20.00)),
    ModelVendor("model-only-claude-opus-5-5", "Claude Opus 5.5 (model only)", "claude-opus-5-5", "anthropic",
                "https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY", describe_concurrency=32),
    # Kimi K3 spends most of its budget reasoning (about 25k tokens before the list), so 32k
    # truncated the JSON on about a third of seeds.
    ModelVendor("model-only-kimi-k3", "Kimi K3 (model only)", "accounts/fireworks/models/kimi-k3", "openai",
                "https://api.fireworks.ai/inference/v1/chat/completions", "FIREWORKS_API_KEY", max_tokens=96_000,
                describe_max_tokens=24_000, describe_concurrency=24),
)


def build_messages(seed: Seed, k: int) -> tuple[str, str]:
    """The system and user prompt for one seed. The query is the seed's authored query, the
    same string the text-query vendors receive."""
    query = seed.query or (
        f"Find companies similar to {seed.seed_name}: {seed.anchor or seed.description or ''}".strip()
    )
    return (
        SYSTEM_PROMPT.format(k=k),
        USER_PROMPT.format(seed_name=seed.seed_name, seed_domain=seed.seed_domain or "no domain", query=query, k=k),
    )


def _request(
    v: ModelVendor, system: str, user: str, key: str, schema: dict[str, Any] = RESPONSE_SCHEMA,
    schema_name: str = "lookalikes", max_tokens: int | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    max_tokens = max_tokens or v.max_tokens
    if v.transport == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        body = {
            "model": v.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        return headers, body
    headers = {"Authorization": f"Bearer {key}"}
    body: dict[str, Any] = {
        "model": v.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_completion_tokens": max_tokens,
    }
    if "fireworks" in v.url:
        body["response_format"] = {"type": "json_object"}
    else:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema, "strict": True},
        }
    return headers, body


def _text_and_usage(v: ModelVendor, payload: Any) -> tuple[str, int, int]:
    if not isinstance(payload, dict):
        return "", 0, 0
    if v.transport == "anthropic":
        text = "".join(b.get("text", "") for b in payload.get("content") or [] if b.get("type") == "text")
        u = payload.get("usage") or {}
        tokens_in = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + int(
            u.get("cache_creation_input_tokens") or 0)
        return text, tokens_in, int(u.get("output_tokens") or 0)
    choice = (payload.get("choices") or [{}])[0]
    u = payload.get("usage") or {}
    return (choice.get("message") or {}).get("content") or "", int(u.get("prompt_tokens") or 0), int(
        u.get("completion_tokens") or 0)


def parse_companies(text: str) -> list[dict[str, str]]:
    """The company list from the model's JSON, tolerating a fenced block or leading prose."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    rows = data.get("companies") if isinstance(data, dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and (r.get("name") or r.get("domain"))]


def normalize_domain(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    host = re.sub(r"^https?://", "", value.strip().lower()).split("/")[0]
    host = host[4:] if host.startswith("www.") else host
    return host or None


def domain_resolves(domain: str | None) -> bool | None:
    if not domain:
        return None
    try:
        socket.getaddrinfo(domain, None)
        return True
    except (OSError, UnicodeError):  # UnicodeError: a malformed host such as ".example.com"
        return False



def _call(v: ModelVendor, system: str, user: str, key: str, **request_kw: Any) -> tuple[int, Any, int]:
    headers, body = _request(v, system, user, key, **request_kw)
    return http_request("POST", endpoint_url(v), headers=headers, body=body, timeout=1200)


def run_model(v: ModelVendor, seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    key = require_env(v.key_env)[v.key_env]
    system, user = build_messages(seed, k)
    status, payload, elapsed_ms = _call(v, system, user, key)
    if status >= 300:
        return RunResult(seed.seed_slug, v.slug, config["name"], config, [], elapsed_ms,
                         error=f"HTTP {status}: {str(payload)[:200]}", requested_k=k)
    text, tokens_in, tokens_out = _text_and_usage(v, payload)
    rows = parse_companies(text)
    if not rows:
        return RunResult(seed.seed_slug, v.slug, config["name"], config, [], elapsed_ms,
                         error="model returned no parseable companies", requested_k=k)
    candidates = []
    for row in rows:
        domain = normalize_domain(row.get("domain"))
        candidates.append(Candidate(
            name=str(row.get("name") or domain or "").strip(),
            domain=domain,
            description=(str(row.get("description") or "").strip() or None),
            extra={
                "industry": row.get("industry") or None,
                "country": row.get("country") or None,
                "employees": row.get("employees") or None,
                "founded_year": row.get("founded_year"),
                "domain_resolves": domain_resolves(domain),
                "source": "model_knowledge",
            },
        ))
    cost = (tokens_in * v.usd_per_m[0] + tokens_out * v.usd_per_m[1]) / 1e6 if v.usd_per_m else None
    # Seed removal, de-duplication and the cut to K, as the spec runner does for every other tool.
    sent = len(candidates)
    kept, duplicates = _without_duplicates(_without_seed(candidates, seed))
    return RunResult(seed.seed_slug, v.slug, config["name"], {**config, "model": v.model,
                     "input_tokens": tokens_in, "output_tokens": tokens_out},
                     take_top(kept, k), elapsed_ms, cost_usd=round(cost, 6) if cost is not None else None,
                     requested_k=k, duplicates_removed=duplicates, sent_count=sent)


_DESCRIBE_SLOTS: dict[str, threading.BoundedSemaphore] = {}
_DESCRIBE_SLOTS_LOCK = threading.Lock()


def _describe_slot(v: ModelVendor) -> threading.BoundedSemaphore:
    with _DESCRIBE_SLOTS_LOCK:
        if v.slug not in _DESCRIBE_SLOTS:
            _DESCRIBE_SLOTS[v.slug] = threading.BoundedSemaphore(v.describe_concurrency)
        return _DESCRIBE_SLOTS[v.slug]


def _parse_object(text: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


_PAGE_CACHE: dict[str, dict[str, Any]] = {}
_PAGE_CACHE_LOCK = threading.Lock()


def _page_text(raw: str) -> str:
    """Title, meta description and the visible text of an HTML page, whitespace collapsed."""
    import html as _html

    title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    meta = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', raw, re.I) or re.search(
        r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']', raw, re.I)
    body = re.sub(r"(?is)<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", raw)
    body = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))).strip()
    parts = []
    if title:
        parts.append("Title: " + _html.unescape(re.sub(r"\s+", " ", title.group(1))).strip())
    if meta:
        parts.append("Meta description: " + _html.unescape(meta.group(1)).strip())
    parts.append("Visible text: " + body)
    return "\n".join(parts)[:PAGE_TEXT_CHARS]


def fetch_page(domain: str | None) -> dict[str, Any]:
    """The company's homepage, fetched once per domain per process (every model describing that
    company sees the same page). https first, then https://www., then http."""
    if not domain:
        return {"ok": False, "error": "no domain"}
    with _PAGE_CACHE_LOCK:
        if domain in _PAGE_CACHE:
            return _PAGE_CACHE[domain]
    import ssl
    import urllib.request

    result: dict[str, Any] = {"ok": False, "error": "not fetched"}
    for url in (f"https://{domain}", f"https://www.{domain}", f"http://{domain}"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": PAGE_UA, "Accept": "text/html,*/*",
                                                       "Accept-Language": "en-US,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as resp:
                raw = resp.read(2_000_000).decode(resp.headers.get_content_charset() or "utf-8", "ignore")
                text = _page_text(raw)
                if len(text) < 80 or "just a moment" in text[:200].lower():
                    result = {"ok": False, "status": resp.status, "url": resp.geturl(), "error": "page blocked or empty"}
                    continue
                result = {"ok": True, "status": resp.status, "url": resp.geturl(), "text": text}
                break
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    with _PAGE_CACHE_LOCK:
        _PAGE_CACHE[domain] = result
    return result


def _page_block(page: dict[str, Any]) -> str:
    if page.get("ok"):
        return f"Fetched {page['url']} (HTTP {page['status']}):\n{page['text']}"
    where = f" ({page['url']})" if page.get("url") else ""
    return f"The page could not be fetched{where}: {page.get('error')}."


def describe_company(
    v: ModelVendor, name: str, domain: str | None, key: str,
) -> tuple[dict[str, Any], int, int, str | None]:
    """One description call: the model sees only this company's name, domain and fetched homepage
    text. Retries rate limits and server errors with backoff. Returns (fields, tokens_in,
    tokens_out, error); fields also carries the page fetch outcome."""
    page = fetch_page(domain)
    user = DESCRIBE_USER_PROMPT.format(name=name or "(no name)", domain=domain or "(no domain)",
                                       page=_page_block(page))
    tokens_in = tokens_out = 0
    last = None
    for attempt in range(6):
        with _describe_slot(v):
            status, payload, _ = _call(v, DESCRIBE_SYSTEM_PROMPT, user, key, schema=DESCRIBE_SCHEMA,
                                       schema_name="company_description", max_tokens=v.describe_max_tokens)
        if status < 300:
            text, i, o = _text_and_usage(v, payload)
            tokens_in += i
            tokens_out += o
            fields = _parse_object(text)
            if fields and fields.get("description"):
                return {**fields, "_page": page}, tokens_in, tokens_out, None
            last = "unparseable description"
        else:
            last = f"HTTP {status}: {str(payload)[:120]}"
            if status not in (408, 409, 425, 429) and status < 500:
                break
        time.sleep(min(60, 4 * 2 ** attempt))
    return {"_page": page}, tokens_in, tokens_out, last


def run_list_then_describe(v: ModelVendor, seed: Seed, k: int, config: dict[str, Any]) -> RunResult:
    """List call (names and domains, with the seed's query), then one blind description call per
    company kept for judging."""
    key = require_env(v.key_env)[v.key_env]
    _, user = build_messages(seed, k)
    # Rate limits and server errors on the list call are retried with backoff (Fireworks throttles
    # Kimi K3 hard); the latency recorded is the call that answered.
    for attempt in range(5):
        status, payload, list_ms = _call(v, LIST_SYSTEM_PROMPT.format(k=k), user, key,
                                         schema=LIST_SCHEMA, schema_name="lookalike_list")
        if status < 300 or (status not in (408, 409, 425, 429) and status < 500):
            break
        time.sleep(min(120, 15 * 2 ** attempt))
    if status >= 300:
        return RunResult(seed.seed_slug, v.slug, config["name"], config, [], list_ms,
                         error=f"HTTP {status}: {str(payload)[:200]}", requested_k=k)
    text, list_in, list_out = _text_and_usage(v, payload)
    rows = parse_companies(text)
    if not rows:
        return RunResult(seed.seed_slug, v.slug, config["name"], config, [], list_ms,
                         error="model returned no parseable companies", requested_k=k)
    listed = [Candidate(name=str(r.get("name") or "").strip(), domain=normalize_domain(r.get("domain")),
                        description=None, extra={}) for r in rows]
    sent = len(listed)
    kept, duplicates = _without_duplicates(_without_seed(listed, seed))
    kept = take_top(kept, k)

    started = time.monotonic()
    # Each worker runs in a copy of this context, so the raw-file capture (a ContextVar) records
    # every description call, like the list call.
    with ThreadPoolExecutor(max_workers=max(1, min(len(kept), v.describe_concurrency))) as ex:
        futures = [ex.submit(copy_context().run, describe_company, v, c.name, c.domain, key) for c in kept]
        described = [f.result() for f in futures]
    describe_ms = int((time.monotonic() - started) * 1000)

    desc_in = desc_out = failed = 0
    for cand, (fields, i, o, err) in zip(kept, described):
        desc_in += i
        desc_out += o
        failed += err is not None
        cand.description = str(fields.get("description") or "").strip() or None
        cand.extra = {
            "industry": fields.get("industry") or None,
            "country": fields.get("country") or None,
            "employees": fields.get("employees") or None,
            "founded_year": fields.get("founded_year"),
            "domain_resolves": domain_resolves(cand.domain),
            "source": "model_knowledge",
            "description_source": "separate_call_with_homepage",
            "page_fetched": bool((fields.get("_page") or {}).get("ok")),
            "page_url": (fields.get("_page") or {}).get("url"),
            **({"page_error": (fields.get("_page") or {}).get("error")} if not (fields.get("_page") or {}).get("ok") else {}),
            **({"description_error": err} if err else {}),
        }
    tokens_in, tokens_out = list_in + desc_in, list_out + desc_out
    cost = (tokens_in * v.usd_per_m[0] + tokens_out * v.usd_per_m[1]) / 1e6 if v.usd_per_m else None
    return RunResult(
        seed.seed_slug, v.slug, config["name"],
        {**config, "model": v.model, "input_tokens": tokens_in, "output_tokens": tokens_out,
         "list_input_tokens": list_in, "list_output_tokens": list_out,
         "describe_input_tokens": desc_in, "describe_output_tokens": desc_out,
         "describe_calls": len(kept), "describe_failures": failed,
         "list_latency_ms": list_ms, "describe_wall_ms": describe_ms},
        kept, list_ms + describe_ms, cost_usd=round(cost, 6) if cost is not None else None,
        requested_k=k, duplicates_removed=duplicates, sent_count=sent,
    )


def _module_for(v: ModelVendor) -> ModuleType:
    """A module per model, so the runner's REGISTRY treats each as a tool. Name and setups come
    from tools/<slug>.yaml; the setup name picks the mode."""
    tool = load_tool(v.slug)
    mod = ModuleType(f"{__name__}.{v.slug.replace('-', '_').replace('.', '_')}")
    mod.VENDOR_SLUG = tool.tool
    mod.VENDOR_NAME = tool.name
    mod.CONFIGS = tool.configs
    mod.run = lambda seed, k, config, _v=v: (
        run_model(_v, seed, k, config) if config.get("name") == "model_only"
        else run_list_then_describe(_v, seed, k, config)
    )
    sys.modules[mod.__name__] = mod
    return mod


RUNNERS = {v.slug: _module_for(v) for v in MODEL_VENDORS}
