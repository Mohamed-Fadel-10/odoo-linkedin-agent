from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict

from mcp_server.tokens import ensure_token_secret, issue_confirmation_token, verify_confirmation_token
from mcp_server.validation import (
    validate_company,
    validate_domain,
    validate_run_id,
    validate_selected_ids,
)
from odoo.client import OdooClient
from pipeline.importer import import_records
from pipeline.processor import OdooContactLookup, RecordStatus, process_provider_result
from pipeline.run_store import RunStore, new_run_id
from providers.hunter import HunterProvider


load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("odoo_linkedin_mcp")

mcp = MCPServer(
    "odoo-linkedin-agent",
    instructions=(
        "Use find_employees, then import_contacts dry_run=True for preview, "
        "then dry_run=False with confirmation_token after user approval."
    ),
)


class CompactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    name: str | None = None
    title: str | None = None
    status: str
    reason: str | None = None


def _provider() -> HunterProvider:
    return HunterProvider()


def _odoo() -> OdooClient:
    return OdooClient()


def _store() -> RunStore:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise ValueError("DATABASE_URL is not configured")
    return RunStore(url)


def find_employees_impl(company: str, domain: str) -> dict[str, Any]:
    company_clean = validate_company(company)
    domain_clean = validate_domain(domain)
    run_id = new_run_id()
    total_started = time.perf_counter()

    provider = _provider()
    hunter_started = time.perf_counter()
    provider_result = provider.find_employees(company_clean, domain_clean)
    hunter_ms = int((time.perf_counter() - hunter_started) * 1000)

    lookup = OdooContactLookup(_odoo())
    process_started = time.perf_counter()
    processed = process_provider_result(provider_result, run_id=run_id, odoo=lookup)
    process_ms = int((time.perf_counter() - process_started) * 1000)

    store = _store()
    store.save_processing_result(processed, company=company_clean, domain=domain_clean)

    total_ms = int((time.perf_counter() - total_started) * 1000)
    logger.info(
        "discovery_timing total_ms=%s hunter_ms=%s processing_odoo_ms=%s "
        "company=%s domain=%s",
        total_ms,
        hunter_ms,
        process_ms,
        company_clean,
        domain_clean,
    )

    compact = [
        CompactRecord(
            record_id=r.record_id,
            name=r.name,
            title=r.title,
            status=r.status.value,
            reason=r.reason,
        ).model_dump()
        for r in processed.all_records
    ]
    return {
        "run_id": run_id,
        "company": company_clean,
        "domain": domain_clean,
        "counts": processed.counts,
        "records": compact,
    }


def import_contacts_impl(
    run_id: str,
    dry_run: bool = True,
    selected_ids: list[str] | None = None,
    confirmation_token: str | None = None,
) -> dict[str, Any]:
    run_uuid = validate_run_id(run_id)
    store = _store()
    stored = store.get_run(run_uuid)
    if stored is None:
        raise ValueError(f"unknown run_id: {run_uuid}")

    known_ids = {r.record_id for r in stored.records}
    selected = validate_selected_ids(selected_ids, required=not dry_run)
    if selected is not None:
        unknown = [item for item in selected if item not in known_ids]
        if unknown:
            raise ValueError(f"selected_ids contains unknown record ids: {unknown[:5]}")

    if dry_run:
        preview_ids = selected or [
            r.record_id for r in stored.records if r.status == RecordStatus.ACCEPTED
        ]
        odoo = _odoo()
        batch = import_records(stored, preview_ids, dry_run=True, odoo=odoo)
        secret = ensure_token_secret()
        token = issue_confirmation_token(
            run_id=run_uuid,
            selected_ids=preview_ids,
            secret=secret,
        )
        return {
            "run_id": run_uuid,
            "dry_run": True,
            "selected_ids": preview_ids,
            "confirmation_token": token,
            "planned": [item.model_dump() for item in batch.results],
            "summary": {
                "would_create": batch.created,
                "duplicates": batch.duplicates,
                "not_importable": batch.not_importable,
                "unknown": batch.unknown,
                "failed": batch.failed,
                "skipped": batch.skipped,
            },
        }

    if selected is None:
        raise ValueError("selected_ids is required when dry_run=False")
    if not confirmation_token:
        raise ValueError(
            "confirmation_token is required when dry_run=False; "
            "run import_contacts with dry_run=True first"
        )

    secret = ensure_token_secret()
    token_error = verify_confirmation_token(
        confirmation_token,
        run_id=run_uuid,
        selected_ids=selected,
        secret=secret,
    )
    if token_error:
        raise ValueError(token_error)

    odoo = _odoo()
    with store.connection() as conn:
        locked = store.lock_run(conn, run_uuid)
        if locked is None:
            raise ValueError(f"unknown run_id: {run_uuid}")

        prior: dict[str, str] = {}
        for record_id in selected:
            row = store.get_import_state(conn, run_uuid, record_id)
            if row and row.get("import_status"):
                prior[record_id] = str(row["import_status"])

        batch = import_records(
            stored,
            selected,
            dry_run=False,
            odoo=odoo,
            prior_import_status=prior,
        )

        for item in batch.results:
            action = "import"
            outcome = item.outcome
            if outcome == "would_create":
                continue
            store.commit_import_outcome(
                conn,
                run_id=run_uuid,
                record_id=item.record_id,
                action=action,
                outcome=outcome,
                reason=item.reason,
                partner_id=item.partner_id,
            )
        conn.commit()

    return {
        "run_id": run_uuid,
        "dry_run": False,
        "selected_ids": selected,
        "results": [item.model_dump() for item in batch.results],
        "summary": {
            "created": batch.created,
            "duplicates": batch.duplicates,
            "not_importable": batch.not_importable,
            "unknown": batch.unknown,
            "failed": batch.failed,
            "skipped": batch.skipped,
            "company_id": batch.company_id,
        },
    }


def get_report_impl(run_id: str) -> dict[str, Any]:
    run_uuid = validate_run_id(run_id)
    store = _store()
    stored = store.get_run(run_uuid)
    if stored is None:
        raise ValueError(f"unknown run_id: {run_uuid}")

    rows = store.get_record_rows(run_uuid)
    import_counts = store.import_counts(run_uuid)

    processing_counts = {
        "Accepted": stored.counts.get("accepted", 0),
        "Duplicate": stored.counts.get("duplicates", 0),
        "Possible Duplicate": stored.counts.get("possible_duplicates", 0),
        "Skipped": stored.counts.get("skipped", 0),
        "Rejected": stored.counts.get("rejected", 0),
        "Total": stored.counts.get("total", 0),
    }
    import_report_counts = {
        "Created": import_counts.get("created", 0),
        "Duplicate": import_counts.get("duplicate", 0),
        "Possible Duplicate": import_counts.get("possible_duplicate", 0),
        "Skipped": import_counts.get("skipped", 0) + import_counts.get("not_importable", 0),
        "Failed": import_counts.get("failed", 0),
        "Not attempted": import_counts.get("not_attempted", 0),
    }

    per_record: list[dict[str, Any]] = []
    for row in rows:
        payload = row["record"]
        per_record.append(
            {
                "record_id": row["record_id"],
                "name": payload.get("name") if isinstance(payload, dict) else None,
                "status": row["status"],
                "reason": row["reason"],
                "import_status": row["import_status"] or "not_attempted",
                "partner_id": row["partner_id"],
            }
        )

    return {
        "run_id": run_uuid,
        "company": stored.company,
        "domain": stored.domain,
        "processing_counts": processing_counts,
        "import_counts": import_report_counts,
        "records": per_record,
    }


def _safe_public_message(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    lowered = message.lower()
    for marker in (
        "password",
        "api_key",
        "authorization",
        "bearer ",
        "secret",
        "postgresql://",
        "database_url",
        "hunter_api",
        "odoo_api",
    ):
        if marker in lowered:
            return f"{type(exc).__name__}: request failed"
    return message


def _tool_error(exc: Exception) -> dict[str, Any]:
    message = _safe_public_message(exc)
    if isinstance(exc, ValueError):
        logger.warning("tool validation/error: %s", message)
    else:
        logger.error("tool failed: %s: %s", type(exc).__name__, message)
    return {"error": message}


@mcp.tool()
def find_employees(company: str, domain: str) -> dict[str, Any]:
    """Discover contacts for a company domain."""
    try:
        return find_employees_impl(company, domain)
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@mcp.tool()
def import_contacts(
    run_id: str,
    dry_run: bool = True,
    selected_ids: list[str] | None = None,
    confirmation_token: str | None = None,
) -> dict[str, Any]:
    """Dry-run preview or real import with confirmation_token."""
    try:
        return import_contacts_impl(
            run_id,
            dry_run=dry_run,
            selected_ids=selected_ids,
            confirmation_token=confirmation_token,
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@mcp.tool()
def get_report(run_id: str) -> dict[str, Any]:
    """Report for a prior run_id."""
    try:
        return get_report_impl(run_id)
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


if __name__ == "__main__":
    mcp.run()
