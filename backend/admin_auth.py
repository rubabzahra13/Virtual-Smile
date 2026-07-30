"""Shared admin password → HMAC session token."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import Header, HTTPException


TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


def _admin_password() -> str:
    return (os.getenv("ADMIN_PASSWORD") or "").strip()


def _session_secret() -> str:
    secret = (os.getenv("ADMIN_SESSION_SECRET") or "").strip()
    if secret:
        return secret
    # Fallback so local/dev still works if only password is set.
    return _admin_password() or "dev-admin-secret"


def admin_configured() -> bool:
    return bool(_admin_password())


def issue_token(password: str) -> str:
    expected = _admin_password()
    if not expected:
        raise HTTPException(status_code=503, detail="Admin password is not configured.")
    if not hmac.compare_digest(password.strip(), expected):
        raise HTTPException(status_code=401, detail="Invalid password.")
    issued_at = int(time.time())
    payload = f"admin:{issued_at}"
    sig = hmac.new(
        _session_secret().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}:{sig}"


def verify_token(token: str) -> bool:
    try:
        prefix, issued_s, sig = token.rsplit(":", 2)
    except ValueError:
        return False
    if prefix != "admin":
        return False
    try:
        issued_at = int(issued_s)
    except ValueError:
        return False
    if time.time() - issued_at > TOKEN_TTL_SECONDS:
        return False
    payload = f"{prefix}:{issued_at}"
    expected = hmac.new(
        _session_secret().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def require_admin(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing admin token.")
    token = authorization.split(" ", 1)[1].strip()
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired admin token.")
    return token
