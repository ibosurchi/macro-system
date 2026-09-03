"""H8 -- structured operational logging with redaction.

One JSON object per line on stdout. Deliberately not a logging framework: an
unattended run needs a machine-readable record of what happened, and a scheduler
already provides capture, retention and search. Adding a framework would be more
surface to secure for no operational gain.

REDACTION IS THE POINT
----------------------
Everything written here can reach a scheduler's log viewer, which for a public
repository is world-readable. Every value is therefore passed through
``redact`` before it is emitted, and the error summary is length-capped as well.
The redactor is applied at the LAST possible moment -- inside the emitter --
rather than trusted to callers, because a caller that forgets is exactly how a
key escapes.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Mapping

#: How much of an error message may survive into a log line or a heartbeat row.
#: Long enough to identify a fault, short enough that an accidental payload dump
#: cannot be exfiltrated through it.
ERROR_SUMMARY_LIMIT = 300

#: Replacement token. Deliberately obvious in a log.
REDACTED = "[REDACTED]"

#: Environment variables whose VALUES must never appear in output. Names are
#: safe to log; values are not. Any value at least this long is replaced
#: wherever it appears, including in the middle of a URL or a traceback.
SENSITIVE_ENV_NAMES: tuple[str, ...] = (
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_URL",
    "FRED_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "RUAPI_API_KEY",
    "OPENROUTER_API_KEY",
)

#: A secret shorter than this is not worth substring-matching: short values
#: produce false positives that would redact ordinary words. Real credentials in
#: this project are far longer.
_MIN_SECRET_LENGTH = 8

#: Structural patterns that look like credentials regardless of whether the
#: value happens to be in this process's environment. Belt and braces: the
#: environment sweep above cannot catch a secret this process never held.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Bearer / apikey headers.
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(apikey\s*[:=]\s*)[A-Za-z0-9._\-]{8,}"),
    # JWT-shaped tokens, which is what a Supabase service-role key is.
    re.compile(r"eyJ[A-Za-z0-9._\-]{10,}"),
    # A query string carrying an obvious credential parameter. Bare ``key`` is
    # accepted HERE, where the ``?``/``&`` prefix proves it is a URL parameter.
    re.compile(r"(?i)([?&](?:api_?key|token|key|secret)=)[^&\s]+"),
    # ``token: abc`` / ``api_key=abc`` in free text. Bare ``key`` is deliberately
    # NOT in this list: it is a common English word and matching it here would
    # redact ordinary prose, which trains an operator to ignore the marker.
    re.compile(
        r"(?i)\b((?:api_?key|service_role_key|access_token|token|secret|password)"
        r"\s*[:=]\s*)[^\s,;&\"']+"
    ),
)


def _secret_values() -> tuple[str, ...]:
    """Live secret values visible to this process, longest first.

    Longest first matters: redacting a short value that is a prefix of a longer
    one would leave the remainder of the longer one exposed.
    """
    found = []
    for name in SENSITIVE_ENV_NAMES:
        value = os.environ.get(name, "")
        if value and len(value) >= _MIN_SECRET_LENGTH:
            found.append(value)
    return tuple(sorted(set(found), key=len, reverse=True))


def redact(text: Any) -> str:
    """Remove anything that looks like a credential from ``text``."""
    rendered = "" if text is None else str(text)
    if not rendered:
        return rendered
    for value in _secret_values():
        rendered = rendered.replace(value, REDACTED)
    for pattern in _PATTERNS:
        rendered = pattern.sub(
            lambda m: (m.group(1) if m.lastindex else "") + REDACTED, rendered
        )
    return rendered


def error_summary(exc: BaseException | str | None) -> str:
    """A redacted, length-capped one-line description of a failure.

    Never a traceback: a traceback carries local variables and request URLs, and
    this string is persisted to the heartbeat where it is read long after the
    context that would justify keeping it.
    """
    if exc is None:
        return ""
    raw = str(exc) if isinstance(exc, BaseException) else str(exc)
    cleaned = redact(" ".join(raw.split()))
    if len(cleaned) > ERROR_SUMMARY_LIMIT:
        cleaned = cleaned[: ERROR_SUMMARY_LIMIT - 1] + "…"
    return cleaned


def error_class(exc: BaseException | None) -> str:
    """The exception's type name. Safe by construction -- it carries no data."""
    return type(exc).__name__ if exc is not None else ""


def utcnow() -> datetime:
    """The only clock this package uses. Always timezone-aware, always UTC."""
    return datetime.now(timezone.utc)


def emit(event: str, **fields: Any) -> dict[str, Any]:
    """Write one structured line to stdout and return what was written.

    Returning the payload lets a test assert on exactly the bytes that were
    emitted, rather than re-deriving what it thinks they should have been.
    """
    payload: dict[str, Any] = {
        "timestamp_utc": utcnow().isoformat(),
        "event": str(event),
    }
    for key, value in fields.items():
        if value is None:
            continue
        payload[key] = value if isinstance(value, (int, float, bool)) else redact(value)

    line = json.dumps(payload, sort_keys=True, default=str)
    # Redact once more over the FULLY RENDERED line. A nested structure coerced
    # by ``default=str`` never passed through the per-field pass above, and that
    # is precisely the gap a credential would slip through.
    sys.stdout.write(redact(line) + "\n")
    sys.stdout.flush()
    return payload


def emit_error(event: str, exc: BaseException, **fields: Any) -> dict[str, Any]:
    """Structured failure line. Never includes a traceback."""
    return emit(
        event,
        error_class=error_class(exc),
        error_summary=error_summary(exc),
        **fields,
    )


def render_run_record(
    *,
    job_key: str,
    run_id: str,
    status: str,
    exit_code: int,
    logical_bucket: str = "",
    records_written: int = 0,
    durable: bool = False,
    error_class_name: str = "",
    error_text: str = "",
    code_version: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The canonical terminal record for one job run, ready to emit."""
    record: dict[str, Any] = {
        "job_key": job_key,
        "run_id": run_id,
        "status": status,
        "exit_code": int(exit_code),
        "durable": bool(durable),
        "records_written": int(records_written),
        "code_version": code_version,
    }
    if logical_bucket:
        record["logical_bucket"] = logical_bucket
    if error_class_name:
        record["error_class"] = error_class_name
    if error_text:
        record["error_summary"] = error_summary(error_text)
    if extra:
        for key, value in extra.items():
            record.setdefault(key, value)
    return record


__all__ = [
    "ERROR_SUMMARY_LIMIT",
    "REDACTED",
    "SENSITIVE_ENV_NAMES",
    "emit",
    "emit_error",
    "error_class",
    "error_summary",
    "redact",
    "render_run_record",
    "utcnow",
]
