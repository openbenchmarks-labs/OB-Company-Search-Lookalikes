"""Env loading and the one HTTP client every tool adapter uses.

Every call made inside `capture_http_calls()` is also recorded, with auth
headers redacted, so each run keeps a full audit trail of what was sent.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .models import RawHttpCall

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
HTTP_TIMEOUT_SEC = 60
HTTP_ATTEMPTS = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}

# Headers that carry secrets — normalized to lowercase for case-insensitive
# matching. Anything matched here is redacted before the raw artifact lands
# on disk so the open-sourced traces can't leak API credentials.
REDACTED_HEADER_NAMES = {
    "x-api-key",
    "x-api-token",
    "authorization",
    "x-auth-token",
    "x-token",
    "api-key",
    "api_key",
    "apikey",
    "x-discolike-key",
}
REDACTED_PLACEHOLDER = "***REDACTED***"


def load_dotenv(path: Path = ENV_FILE) -> None:
    """Minimal .env loader. Strips a leading `export ` so shell-style lines
    (`export FOO=bar`) work the same as plain `FOO=bar`."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def require_env(*keys: str) -> dict[str, str]:
    """Read each key from env; raise if any is missing. Returns the dict
    of {key: value}. Use at the top of a tool adapter so the failure
    mode is clear."""
    load_dotenv()
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"missing required env var(s): {', '.join(missing)} — set in .env"
        )
    return {k: os.environ[k] for k in keys}


# Active capture buffer for HTTP calls. Set by `capture_http_calls()`.
# When set, every successful and failed `http_request()` call appends a
# `RawHttpCall` to this list. Used by the runner to record the full
# audit trail per (seed, tool, config) attempt.
_HTTP_TRACE: ContextVar[list[RawHttpCall] | None] = ContextVar(
    "lookalike_http_trace", default=None
)


@contextlib.contextmanager
def capture_http_calls() -> Iterator[list[RawHttpCall]]:
    """Context manager that captures every `http_request()` call made
    inside the block. Returns the list buffer so the caller can drain it
    after the block exits.

    Usage in the runner:
        with capture_http_calls() as calls:
            result = runner["run"](seed, k, config)
        attempt["vendor_calls"] = [raw_http_call_to_dict(c) for c in calls]
    """
    buf: list[RawHttpCall] = []
    token = _HTTP_TRACE.set(buf)
    try:
        yield buf
    finally:
        _HTTP_TRACE.reset(token)


def record_vendor_call(
    *,
    method: str,
    url: str,
    request_body: Any,
    response_status: int,
    response_body: Any,
    elapsed_ms: int,
) -> None:
    """Add a non-HTTP vendor invocation (for example a local vendor CLI) to the
    same raw audit trail as HTTP-backed adapters.

    request_headers is empty by construction: a CLI carries its credentials in
    the environment or a config file, not in a header we could capture, so
    there is nothing to redact and nothing to claim we captured.
    """
    trace = _HTTP_TRACE.get()
    if trace is None:
        return
    trace.append(
        RawHttpCall(
            method=method,
            url=url,
            request_headers={},
            request_body=request_body,
            response_status=response_status,
            response_body=response_body,
            response_text_preview=json.dumps(response_body, ensure_ascii=False)[:4096],
            elapsed_ms=elapsed_ms,
        )
    )


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Replace values of well-known auth headers with REDACTED_PLACEHOLDER
    so the open-source raw artifacts can't leak API keys."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        out[k] = REDACTED_PLACEHOLDER if k.lower() in REDACTED_HEADER_NAMES else v
    return out


def http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
    body_encoding: str = "json",
    timeout: int = HTTP_TIMEOUT_SEC,
) -> tuple[int, Any, int]:
    """Returns (status, json_or_text, elapsed_ms). Retries network errors,
    429 and 5xx with backoff, honouring Retry-After; raises the network
    error if the last attempt still fails. elapsed_ms is the time of the
    attempt that produced the response, without earlier failed attempts.

    When a `capture_http_calls()` context is active, every attempt that got
    a response is also appended to the active trace buffer as a fully
    redacted `RawHttpCall`. Adapters don't need to know — the capture is
    transparent."""
    data: bytes | None = None
    hdrs = dict(headers or {})
    if body is not None and method.upper() != "GET":
        if body_encoding == "form":
            data = urllib.parse.urlencode(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        else:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
    hdrs.setdefault("Accept", "application/json")
    # Several vendors (Ocean, Lusha) sit behind Cloudflare and return a 403
    # "Error 1010: Access denied" for the default Python urllib UA. A
    # mainstream desktop UA passes their bot filters reliably and is also
    # the right thing to do for any benchmark talking to public APIs.
    hdrs.setdefault(
        "User-Agent",
        "openbenchmarks-lookalikes/0.1 (+https://openbenchmarks.com/lookalikes)",
    )

    req = urllib.request.Request(url=url, data=data, method=method.upper(), headers=hdrs)
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        start = time.monotonic()
        try:
            status, parsed, response_text, retry_after = _send_once(req, timeout)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == HTTP_ATTEMPTS:
                raise
            time.sleep(2 ** attempt)
            continue
        elapsed_ms = int((time.monotonic() - start) * 1000)

        trace = _HTTP_TRACE.get()
        if trace is not None:
            trace.append(
                RawHttpCall(
                    method=method.upper(),
                    url=url,
                    request_headers=_redact_headers(hdrs),
                    request_body=body,  # already JSON-ish; safe to serialize
                    response_status=status,
                    response_body=parsed,
                    response_text_preview=response_text[:4096],
                    elapsed_ms=elapsed_ms,
                )
            )

        if status in RETRY_STATUSES and attempt < HTTP_ATTEMPTS:
            time.sleep(_retry_after_sec(retry_after) or 2 ** attempt)
            continue
        return status, parsed, elapsed_ms
    raise AssertionError("unreachable")


def _send_once(req: urllib.request.Request, timeout: int) -> tuple[int, Any, str, str | None]:
    """One request: (status, json_or_text, text, Retry-After header)."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            response_text = resp.read().decode("utf-8", errors="replace")
            status, headers = resp.status, resp.headers
    except urllib.error.HTTPError as e:
        response_text = e.read().decode("utf-8", errors="replace")
        status, headers = e.code, e.headers
    try:
        parsed: Any = json.loads(response_text)
    except json.JSONDecodeError:
        parsed = response_text
    return status, parsed, response_text, headers.get("Retry-After") if headers else None


def _retry_after_sec(value: str | None) -> float | None:
    try:
        seconds = float(value or "")
    except ValueError:
        return None
    return min(seconds, 60.0) if math.isfinite(seconds) and seconds > 0 else None
