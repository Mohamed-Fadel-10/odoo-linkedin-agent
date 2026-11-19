from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_HERMES_BIN = "hermes"
DEFAULT_DISCOVER_TIMEOUT_SECONDS = 120
DEFAULT_DISCOVERY_REUSE_MINUTES = 30
PAIRING_HEADER = "X-Extension-Pairing-Token"


class ExtensionBackendSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(default=DEFAULT_HOST)
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    extension_id: str = ""
    pairing_token: str
    hermes_bin: str = Field(default=DEFAULT_HERMES_BIN)
    discover_timeout_seconds: int = Field(
        default=DEFAULT_DISCOVER_TIMEOUT_SECONDS, ge=1, le=600
    )
    discovery_reuse_minutes: int = Field(
        default=DEFAULT_DISCOVERY_REUSE_MINUTES, ge=1, le=1440
    )
    hermes_model: str | None = None
    hermes_provider: str | None = None
    hermes_reasoning: str | None = None
    hermes_toolsets: str | None = None
    hermes_max_turns: int | None = None

    @property
    def discovery_reuse_seconds(self) -> int:
        return self.discovery_reuse_minutes * 60


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


def ensure_pairing_token() -> str:
    existing = os.getenv("EXTENSION_PAIRING_TOKEN", "").strip()
    if existing and not existing.startswith("generate-a-"):
        return existing
    generated = secrets.token_urlsafe(32)
    _upsert_env_value("EXTENSION_PAIRING_TOKEN", generated)
    os.environ["EXTENSION_PAIRING_TOKEN"] = generated
    return generated


def _optional_env(name: str) -> str | None:
    raw = os.getenv(name, "").strip()
    return raw or None


def load_settings() -> ExtensionBackendSettings:
    raw_port = os.getenv("EXTENSION_HTTP_PORT", str(DEFAULT_PORT)).strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError(
            f"EXTENSION_HTTP_PORT must be an integer, got {raw_port!r}"
        ) from exc

    extension_id = os.getenv("EXTENSION_ID", "").strip()
    if extension_id.startswith("your_"):
        extension_id = ""

    reuse_raw = os.getenv(
        "DISCOVERY_REUSE_MINUTES", str(DEFAULT_DISCOVERY_REUSE_MINUTES)
    ).strip()
    try:
        reuse_minutes = int(reuse_raw)
    except ValueError as exc:
        raise ValueError(
            f"DISCOVERY_REUSE_MINUTES must be an integer, got {reuse_raw!r}"
        ) from exc

    from extension_backend.hermes_runner import validate_hermes_options

    hermes_opts = validate_hermes_options(
        model=_optional_env("HERMES_MODEL"),
        provider=_optional_env("HERMES_PROVIDER"),
        reasoning=_optional_env("HERMES_REASONING"),
        toolsets=_optional_env("HERMES_TOOLSETS"),
        max_turns=_optional_env("HERMES_MAX_TURNS"),
    )

    return ExtensionBackendSettings(
        host=DEFAULT_HOST,
        port=port,
        extension_id=extension_id,
        pairing_token=ensure_pairing_token(),
        hermes_bin=(
            os.getenv("HERMES_BIN", DEFAULT_HERMES_BIN).strip() or DEFAULT_HERMES_BIN
        ),
        discover_timeout_seconds=DEFAULT_DISCOVER_TIMEOUT_SECONDS,
        discovery_reuse_minutes=reuse_minutes,
        hermes_model=str(hermes_opts["model"]) if "model" in hermes_opts else None,
        hermes_provider=(
            str(hermes_opts["provider"]) if "provider" in hermes_opts else None
        ),
        hermes_reasoning=(
            str(hermes_opts["reasoning"]) if "reasoning" in hermes_opts else None
        ),
        hermes_toolsets=(
            str(hermes_opts["toolsets"]) if "toolsets" in hermes_opts else None
        ),
        hermes_max_turns=(
            int(hermes_opts["max_turns"]) if "max_turns" in hermes_opts else None
        ),
    )
