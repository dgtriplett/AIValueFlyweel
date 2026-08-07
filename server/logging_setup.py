"""Structured logging and request correlation.

WHY
---
The app previously logged with `print()`. That is unusable for supporting a customer:
no level, no timestamp you can filter, no way to tie the five lines a failing request
produced to each other, and no way to turn detail up without a redeploy.

WHAT THIS GIVES
---------------
  - JSON lines when running in Databricks Apps (log aggregation can parse them),
    human-readable when running locally.
  - A request id on every line emitted while handling a request, so `grep <id>` in
    the app logs yields exactly that request's story.
  - Token redaction, because OAuth tokens genuinely do end up in exception strings
    and log output is the easiest place to leak one.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from contextvars import ContextVar

# Set per request by the middleware. A ContextVar (not a global) is what makes this
# correct under async concurrency: each request gets its own value even though
# handlers interleave on one thread.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# Anything that looks like a bearer token, an OAuth JWT, or a password in a DSN.
# Applied to the FINAL message text rather than at each call site, because the leak
# is usually in an exception string nobody thought about.
_REDACTIONS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{20,}", re.I), r"\1<redacted>"),
    (re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}"), "<redacted-jwt>"),
    (re.compile(r"(password=)[^\s&;'\"]+", re.I), r"\1<redacted>"),
    (re.compile(r"(token[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._\-]{20,}", re.I),
     r"\1<redacted>"),
]


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            # Truncated: a full trace per line makes aggregated logs unreadable, and
            # the type plus message is what identifies the failure.
            payload["error"] = redact(
                self.formatException(record.exc_info))[-1500:]
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Readable single lines for local development."""

    def format(self, record: logging.LogRecord) -> str:
        rid = getattr(record, "request_id", "-")
        prefix = f"{record.levelname:5s} {record.name:28s}"
        suffix = f" [{rid[:8]}]" if rid and rid != "-" else ""
        text = f"{prefix}{suffix}  {redact(record.getMessage())}"
        if record.exc_info:
            text += "\n" + redact(self.formatException(record.exc_info))
        return text


def configure(*, json_output: bool | None = None, level: str | None = None) -> None:
    """Install handlers. Idempotent — safe if called more than once.

    Defaults to JSON in Databricks Apps and text locally; LOG_LEVEL overrides the
    level so detail can be turned up on a deployed app without a code change.
    """
    if json_output is None:
        json_output = bool(os.environ.get("DATABRICKS_APP_NAME"))
    resolved = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else TextFormatter())
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)
    root.setLevel(resolved)

    # uvicorn's access log duplicates our own request line with no request id, so
    # it is silenced; errors from it are still wanted.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("uvicorn.error").setLevel(resolved)
    # These are chatty at DEBUG and rarely what you are debugging.
    for noisy in ("asyncpg", "httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(max(logging.INFO,
                                              logging.getLevelName(resolved)))


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def install_middleware(app) -> None:
    """Correlate every log line emitted while handling a request.

    Also emits one completion line per request with method, path, status, and
    duration — the minimum needed to answer "was it slow or did it fail?" without
    reproducing the problem.
    """
    from starlette.middleware.base import BaseHTTPMiddleware

    logger = logging.getLogger("grid_atlas.request")

    class RequestContextMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # Honour an upstream id when one is present so a trace spans hops.
            incoming = request.headers.get("x-request-id")
            request_id = incoming or new_request_id()
            token = request_id_var.set(request_id)
            started = time.monotonic()
            status = 500
            try:
                response = await call_next(request)
                status = response.status_code
                response.headers["X-Request-Id"] = request_id
                return response
            finally:
                elapsed = (time.monotonic() - started) * 1000
                # Static assets and health checks would drown the useful lines.
                path = request.url.path
                if not (path.startswith("/assets") or path.startswith("/console")
                        or path == "/api/health"):
                    record_level = (logging.WARNING if status >= 500
                                    else logging.INFO)
                    logger.log(record_level, "%s %s -> %d in %dms",
                               request.method, path, status, elapsed)
                request_id_var.reset(token)

    app.add_middleware(RequestContextMiddleware)
