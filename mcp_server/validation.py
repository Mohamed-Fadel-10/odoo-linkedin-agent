from __future__ import annotations

import re
from uuid import UUID


_COMPANY_RE = re.compile(r"^[\w .+&'’\-/,()]{1,200}$", re.UNICODE)
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)
MAX_SELECTED_IDS = 200


def validate_company(company: str) -> str:
    cleaned = company.strip()
    if not cleaned:
        raise ValueError("company is required")
    if len(cleaned) > 200:
        raise ValueError("company exceeds max length 200")
    if not _COMPANY_RE.match(cleaned):
        raise ValueError("company contains invalid characters")
    return cleaned


def validate_domain(domain: str) -> str:
    cleaned = domain.strip().lower().removeprefix("http://").removeprefix("https://")
    cleaned = cleaned.split("/")[0]
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    if not cleaned or not _DOMAIN_RE.match(cleaned):
        raise ValueError(f"invalid domain format: {domain!r}")
    return cleaned


def validate_run_id(run_id: str) -> str:
    cleaned = run_id.strip()
    try:
        return str(UUID(cleaned))
    except ValueError as exc:
        raise ValueError("run_id must be a UUID") from exc


def validate_selected_ids(
    selected_ids: list[str] | None,
    *,
    required: bool = False,
) -> list[str] | None:
    if selected_ids is None:
        if required:
            raise ValueError("selected_ids is required for a real import")
        return None
    if not isinstance(selected_ids, list):
        raise ValueError("selected_ids must be a list")
    if len(selected_ids) > MAX_SELECTED_IDS:
        raise ValueError(f"selected_ids exceeds max size {MAX_SELECTED_IDS}")
    cleaned: list[str] = []
    for item in selected_ids:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("selected_ids must contain non-empty strings")
        cleaned.append(item.strip())
    return cleaned
