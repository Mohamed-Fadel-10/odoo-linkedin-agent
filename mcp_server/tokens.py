from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path


DEFAULT_TTL_SECONDS = 15 * 60
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def ensure_token_secret() -> str:
    existing = os.getenv("CONFIRMATION_TOKEN_SECRET", "").strip()
    if existing:
        return existing

    generated = secrets.token_urlsafe(32)
    _upsert_env_value("CONFIRMATION_TOKEN_SECRET", generated)
    os.environ["CONFIRMATION_TOKEN_SECRET"] = generated
    return generated


def _upsert_env_value(key: str, value: str) -> None:
    lines: list[str] = []
    found = False
    if ENV_PATH.is_file():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def issue_confirmation_token(
    *,
    run_id: str,
    selected_ids: list[str],
    secret: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    issued_at = int(now if now is not None else time.time())
    expires_at = issued_at + ttl_seconds
    payload = {
        "run_id": run_id,
        "selected_ids": sorted(selected_ids),
        "exp": expires_at,
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    signature = hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{body}.{signature}"


def verify_confirmation_token(
    token: str,
    *,
    run_id: str,
    selected_ids: list[str],
    secret: str,
    now: float | None = None,
) -> str | None:
    if not token or "." not in token:
        return "confirmation_token is missing or malformed"

    body, signature = token.rsplit(".", 1)
    expected = hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return "confirmation_token signature is invalid"

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return "confirmation_token payload is invalid"

    if not isinstance(payload, dict):
        return "confirmation_token payload is invalid"
    if payload.get("run_id") != run_id:
        return "confirmation_token does not match run_id"
    token_ids = payload.get("selected_ids")
    if not isinstance(token_ids, list) or [str(x) for x in token_ids] != sorted(selected_ids):
        return "confirmation_token does not match selected_ids"

    exp = payload.get("exp")
    if not isinstance(exp, int):
        return "confirmation_token expiry is invalid"
    current = now if now is not None else time.time()
    if current > exp:
        return "confirmation_token has expired"
    return None
