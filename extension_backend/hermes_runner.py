from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from uuid import UUID


logger = logging.getLogger("extension_backend.hermes")

FIND_EMPLOYEES_TOOL = "mcp__odoo_linkedin_agent__find_employees"

_REASONING_LEVELS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
)
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+@:-]{0,200}$")
_TOOLSET_TOKEN_RE = re.compile(r"^[A-Za-z0-9*][A-Za-z0-9*._-]{0,100}$")

_RUN_ID_JSON_RE = re.compile(
    r"\{[^{}]*[\"']run_id[\"']\s*:\s*[\"']([0-9a-fA-F-]{36})[\"'][^{}]*\}",
    re.DOTALL,
)
_RUN_ID_NEAR_UUID_RE = re.compile(
    r"run_id[\"'\s:=]+[\"']?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})[\"']?",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def build_discover_prompt(*, company: str, domain: str) -> str:
    return (
        f"Call {FIND_EMPLOYEES_TOOL} once with company={company!r} "
        f"domain={domain!r}. No other tools. No explanation. "
        'Final output only {"run_id":"..."}.'
    )


def _looks_like_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except ValueError:
        return False


def _run_id_from_mapping(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    run_id = payload.get("run_id")
    if isinstance(run_id, str) and _looks_like_uuid(run_id.strip()):
        return run_id.strip()
    return None


def extract_run_id(hermes_stdout: str) -> str:
    text = (hermes_stdout or "").strip()
    if not text:
        raise ValueError("Hermes returned empty output")

    candidates: list[str] = [text]
    for match in _FENCE_RE.finditer(text):
        fenced = match.group(1).strip()
        if fenced:
            candidates.append(fenced)

    for blob in candidates:
        for line in reversed(blob.splitlines()):
            candidate = line.strip().rstrip(",")
            if not candidate.startswith("{"):
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            run_id = _run_id_from_mapping(payload)
            if run_id:
                return run_id

        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            payload = None
        run_id = _run_id_from_mapping(payload)
        if run_id:
            return run_id

        match = _RUN_ID_JSON_RE.search(blob)
        if match and _looks_like_uuid(match.group(1)):
            return match.group(1)

        near = _RUN_ID_NEAR_UUID_RE.search(blob)
        if near and _looks_like_uuid(near.group(1)):
            return near.group(1)

    for blob in candidates:
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("error"):
            raise ValueError(f"Discovery tool failed: {payload['error']}")

    lowered = text.lower()
    if "hunter" in lowered or "pagination" in lowered:
        raise ValueError(
            "Hermes finished without a run_id after a Hunter/provider failure. "
            "Check HUNTER_API_KEY / plan limits (Free plans often allow only 10 "
            "results per Domain Search). "
            f"Reply length {len(text)}."
        )
    raise ValueError(
        "Hermes output did not contain a run_id JSON object "
        f"(reply length {len(text)}). "
        "Check that odoo-linkedin-agent is enabled in Hermes and can call "
        f"{FIND_EMPLOYEES_TOOL}."
    )


def _validate_optional_token(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if not _SAFE_TOKEN_RE.match(cleaned):
        raise ValueError(
            f"{name} must be a non-empty token "
            "(letters, digits, . _ / + @ : -); got invalid value"
        )
    return cleaned


def _validate_reasoning(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    if not cleaned:
        return None
    if cleaned not in _REASONING_LEVELS:
        allowed = ", ".join(sorted(_REASONING_LEVELS))
        raise ValueError(f"HERMES_REASONING must be one of: {allowed}")
    return cleaned


def _validate_toolsets(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    if not parts:
        return None
    for part in parts:
        if not _TOOLSET_TOKEN_RE.match(part):
            raise ValueError(
                "HERMES_TOOLSETS entries must be comma-separated toolset names "
                "(letters, digits, *, ., _, -)"
            )
    return ",".join(parts)


def _validate_max_turns(value: int | str | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            parsed = int(cleaned)
        except ValueError as exc:
            raise ValueError("HERMES_MAX_TURNS must be a positive integer") from exc
    else:
        parsed = int(value)
    if parsed < 1 or parsed > 10_000:
        raise ValueError("HERMES_MAX_TURNS must be between 1 and 10000")
    return parsed


def validate_hermes_options(
    *,
    model: str | None = None,
    provider: str | None = None,
    reasoning: str | None = None,
    toolsets: str | None = None,
    max_turns: int | str | None = None,
) -> dict[str, str | int]:
    out: dict[str, str | int] = {}
    model_v = _validate_optional_token("HERMES_MODEL", model)
    if model_v is not None:
        out["model"] = model_v
    provider_v = _validate_optional_token("HERMES_PROVIDER", provider)
    if provider_v is not None:
        out["provider"] = provider_v
    reasoning_v = _validate_reasoning(reasoning)
    if reasoning_v is not None:
        out["reasoning"] = reasoning_v
    toolsets_v = _validate_toolsets(toolsets)
    if toolsets_v is not None:
        out["toolsets"] = toolsets_v
    max_turns_v = _validate_max_turns(max_turns)
    if max_turns_v is not None:
        out["max_turns"] = max_turns_v
    return out


def build_hermes_command(
    *,
    hermes_bin: str,
    prompt: str,
    model: str | None = None,
    provider: str | None = None,
    reasoning: str | None = None,
    toolsets: str | None = None,
    max_turns: int | str | None = None,
) -> list[str]:
    opts = validate_hermes_options(
        model=model,
        provider=provider,
        reasoning=reasoning,
        toolsets=toolsets,
        max_turns=max_turns,
    )
    cmd: list[str] = [hermes_bin]
    if "model" in opts:
        cmd.extend(["-m", str(opts["model"])])
    if "provider" in opts:
        cmd.extend(["--provider", str(opts["provider"])])
    if "reasoning" in opts:
        cmd.extend(["--reasoning", str(opts["reasoning"])])
    if "toolsets" in opts:
        cmd.extend(["-t", str(opts["toolsets"])])
    if "max_turns" in opts:
        cmd.extend(["--max-turns", str(opts["max_turns"])])
    cmd.extend(["-z", prompt])
    return cmd


def run_hermes_discover(
    *,
    company: str,
    domain: str,
    hermes_bin: str,
    timeout_seconds: int,
    model: str | None = None,
    provider: str | None = None,
    reasoning: str | None = None,
    toolsets: str | None = None,
    max_turns: int | str | None = None,
) -> str:
    prompt = build_discover_prompt(company=company, domain=domain)
    cmd = build_hermes_command(
        hermes_bin=hermes_bin,
        prompt=prompt,
        model=model,
        provider=provider,
        reasoning=reasoning,
        toolsets=toolsets,
        max_turns=max_turns,
    )
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ValueError(
            f"Hermes binary not found ({hermes_bin!r}). "
            "Install Hermes or set HERMES_BIN in .env, then retry."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"Hermes discovery timed out after {timeout_seconds}s"
        ) from exc
    finally:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "discovery_timing hermes_ms=%s company=%s domain=%s",
            elapsed_ms,
            company,
            domain,
        )

    if completed.returncode != 0:
        hint = _public_failure_hint(completed.stderr or "", completed.stdout or "")
        raise ValueError(
            f"Hermes discovery failed (exit {completed.returncode})"
            + (f": {hint}" if hint else "")
        )
    return extract_run_id(completed.stdout)


def _public_failure_hint(stderr: str, stdout: str) -> str:
    blob = f"{stderr}\n{stdout}".lower()
    if "no module named 'mcp_server'" in blob or "module not found" in blob:
        return (
            "MCP server failed to start (Python path). "
            "Re-run hermes mcp add for odoo-linkedin-agent"
        )
    if "find_employees" in blob and ("unavailable" in blob or "no matches" in blob):
        return (
            "find_employees tool not available; "
            "register odoo-linkedin-agent with hermes mcp add"
        )
    if "tool" in blob and ("not found" in blob or "unknown" in blob):
        return "required MCP tool missing; check hermes mcp list for odoo-linkedin-agent"
    if "max-turns" in blob or "unrecognized arguments" in blob:
        return (
            "Hermes rejected a CLI flag (check HERMES_* settings; "
            "--max-turns may require a newer Hermes or leave HERMES_MAX_TURNS empty)"
        )
    if "rate" in blob and "limit" in blob:
        return "provider or model rate-limited"
    return "check that odoo-linkedin-agent is listed in hermes mcp list"
