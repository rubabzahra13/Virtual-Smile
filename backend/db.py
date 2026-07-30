"""Supabase client for Virtual Smile persistence."""

from __future__ import annotations

import logging
import os
import time
from functools import lru_cache
from typing import Any, Callable, Optional, TypeVar
from urllib.parse import urlparse

from supabase import Client, create_client

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DatabaseNotConfigured(RuntimeError):
    """Raised when Supabase env vars are missing."""


def _is_transient_db_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    needles = (
        "resource temporarily unavailable",
        "temporarily unavailable",
        "connection reset",
        "connecterror",
        "readerror",
        "writeerror",
        "timeout",
        "timed out",
        "server disconnected",
        "remoteprotocolerror",
    )
    if any(n in name for n in ("timeout", "connect", "read", "write", "protocol")):
        return True
    return any(n in msg for n in needles)


def db_retry(fn: Callable[[], T], *, attempts: int = 3, label: str = "supabase") -> T:
    """Retry transient Supabase/httpx failures (e.g. errno 35 EAGAIN)."""
    last: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except DatabaseNotConfigured:
            raise
        except Exception as exc:
            last = exc
            if not _is_transient_db_error(exc) or attempt >= attempts:
                raise
            delay = 0.2 * attempt
            logger.warning(
                "%s failed (attempt %s/%s): %s; retrying in %.1fs",
                label,
                attempt,
                attempts,
                exc,
                delay,
            )
            time.sleep(delay)
    assert last is not None
    raise last


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise DatabaseNotConfigured(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set."
        )
    # Local environments can inherit HTTP(S)_PROXY values that block direct
    # access to Supabase. Ensure Supabase host bypasses proxy via NO_PROXY.
    host = (urlparse(url).hostname or "").strip()
    if host:
        existing = (os.getenv("NO_PROXY") or os.getenv("no_proxy") or "").strip()
        entries = [item.strip() for item in existing.split(",") if item.strip()]
        if host not in entries:
            entries.append(host)
            os.environ["NO_PROXY"] = ",".join(entries)
    return create_client(url, key)


def db_ready() -> bool:
    try:
        get_supabase()
        return True
    except DatabaseNotConfigured:
        return False


def normalize_email(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def normalize_phone(phone: Optional[str]) -> str:
    """Normalize to +92XXXXXXXXXX when possible."""
    raw = (phone or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits.startswith("92") and len(digits) >= 12:
        return f"+{digits[:12]}"
    if digits.startswith("0") and len(digits) >= 11:
        return f"+92{digits[1:11]}"
    if len(digits) == 10 and digits.startswith("3"):
        return f"+92{digits}"
    if raw.startswith("+") and digits:
        return f"+{digits}"
    return raw


def extract_concerns_treatments(findings: Any) -> tuple[list[str], list[str]]:
    concerns: list[str] = []
    treatments: list[str] = []
    if not isinstance(findings, dict):
        return concerns, treatments

    visible = findings.get("visible_concerns")
    if isinstance(visible, list):
        for item in visible:
            text = str(item).strip()
            if text and text not in concerns:
                concerns.append(text)

    details = findings.get("concern_details")
    if isinstance(details, list):
        for row in details:
            if not isinstance(row, dict):
                continue
            concern = str(row.get("concern") or "").strip()
            if concern and concern not in concerns:
                concerns.append(concern)
            opts = row.get("treatment_options")
            if isinstance(opts, list):
                for opt in opts:
                    t = str(opt).strip()
                    if t and t not in treatments:
                        treatments.append(t)
            elif isinstance(opts, str) and opts.strip():
                for part in opts.split(","):
                    t = part.strip()
                    if t and t not in treatments:
                        treatments.append(t)

    roadmap = findings.get("treatment_roadmap")
    if isinstance(roadmap, list):
        for step in roadmap:
            if isinstance(step, dict):
                label = str(step.get("treatment") or step.get("title") or step.get("phase") or "").strip()
                if label and label not in treatments:
                    treatments.append(label)
            else:
                label = str(step).strip()
                if label and label not in treatments:
                    treatments.append(label)

    return concerns, treatments
