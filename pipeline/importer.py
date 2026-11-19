from __future__ import annotations

from typing import Any, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from odoo.client import CompanyCreateData, PartnerCreateData
from pipeline.processor import ProcessedRecord, RecordStatus, build_contact_comment
from pipeline.run_store import StoredRun, status_is_importable
from providers.base import CompanyInfo


class ImportClient(Protocol):
    def ensure_company(
        self,
        name: str,
        website: str | None = None,
        *,
        company_data: CompanyCreateData | dict[str, Any] | None = None,
        warnings: list[str] | None = None,
    ) -> int: ...

    def find_exact_contact(
        self,
        email: str | None = None,
        linkedin_url: str | None = None,
    ) -> dict[str, Any] | None: ...

    def create_partner(self, data: PartnerCreateData | dict[str, Any]) -> int: ...


class ImportItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    outcome: str
    reason: str | None = None
    partner_id: int | None = None


class ImportBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    dry_run: bool
    company_id: int | None = None
    results: list[ImportItemResult] = Field(default_factory=list)
    created: int = 0
    duplicates: int = 0
    failed: int = 0
    skipped: int = 0
    not_importable: int = 0
    unknown: int = 0
    warnings: list[str] = Field(default_factory=list)


def import_records(
    run: StoredRun | ProcessingRunLike,
    record_ids: Sequence[str] | None,
    dry_run: bool,
    odoo: ImportClient,
    *,
    prior_import_status: dict[str, str] | None = None,
) -> ImportBatchResult:
    by_id = {r.record_id: r for r in run.records}
    selected = list(record_ids) if record_ids is not None else [
        r.record_id for r in run.records if r.status == RecordStatus.ACCEPTED
    ]

    batch = ImportBatchResult(run_id=run.run_id, dry_run=dry_run)
    prior = prior_import_status or {}

    company_id: int | None = None
    if not dry_run:
        company_name = getattr(run, "company", None) or _first_company(run.records)
        domain = getattr(run, "domain", None) or _first_domain(run.records)
        if not company_name:
            raise ValueError("Company name is required for import")
        company_warnings: list[str] = []
        company_id = odoo.ensure_company(
            company_name,
            website=domain,
            company_data=_company_create_data(run.records),
            warnings=company_warnings,
        )
        batch.company_id = company_id
        batch.warnings.extend(company_warnings)

    for record_id in selected:
        record = by_id.get(record_id)
        if record is None:
            item = ImportItemResult(
                record_id=record_id,
                outcome="unknown",
                reason="unknown record_id",
            )
            batch.unknown += 1
            batch.results.append(item)
            continue

        previous = prior.get(record_id)
        if previous == "created":
            item = ImportItemResult(
                record_id=record_id,
                outcome="skipped",
                reason="already imported",
            )
            batch.skipped += 1
            batch.results.append(item)
            continue

        if not status_is_importable(record.status):
            item = ImportItemResult(
                record_id=record_id,
                outcome="not_importable",
                reason=f"status {record.status.value} is not importable",
            )
            batch.not_importable += 1
            batch.results.append(item)
            continue

        if not record.email:
            item = ImportItemResult(
                record_id=record_id,
                outcome="failed",
                reason="missing email",
            )
            batch.failed += 1
            batch.results.append(item)
            continue

        try:
            existing = odoo.find_exact_contact(
                email=record.email,
                linkedin_url=record.linkedin_url,
            )
            if existing is not None:
                item = ImportItemResult(
                    record_id=record_id,
                    outcome="duplicate",
                    reason=f"duplicate matches Odoo partner id={existing.get('id')}",
                    partner_id=int(existing["id"]) if existing.get("id") is not None else None,
                )
                batch.duplicates += 1
                batch.results.append(item)
                continue

            if dry_run:
                item = ImportItemResult(
                    record_id=record_id,
                    outcome="would_create",
                    reason="dry_run",
                )
                batch.created += 1
                batch.results.append(item)
                continue

            assert company_id is not None
            partner_id = odoo.create_partner(
                PartnerCreateData(
                    name=record.name or "Unknown",
                    email=record.email,
                    phone=record.phone,
                    job_title=record.title,
                    company_id=company_id,
                    linkedin_url=record.linkedin_url,
                    comment=build_contact_comment(record),
                )
            )
            item = ImportItemResult(
                record_id=record_id,
                outcome="created",
                partner_id=partner_id,
            )
            batch.created += 1
            batch.results.append(item)
        except Exception as exc:  # noqa: BLE001 — per-record isolation
            item = ImportItemResult(
                record_id=record_id,
                outcome="failed",
                reason=_safe_error_message(exc),
            )
            batch.failed += 1
            batch.results.append(item)

    return batch


class ProcessingRunLike(Protocol):
    run_id: str
    records: Sequence[ProcessedRecord]


def _first_company(records: Sequence[ProcessedRecord]) -> str | None:
    for record in records:
        if record.company:
            return record.company
    return None


def _first_domain(records: Sequence[ProcessedRecord]) -> str | None:
    for record in records:
        if record.domain:
            return record.domain
    return None


def _first_company_info(records: Sequence[ProcessedRecord]) -> CompanyInfo | None:
    for record in records:
        if record.company_info is not None:
            return record.company_info
    return None


def _company_create_data(
    records: Sequence[ProcessedRecord],
) -> CompanyCreateData | None:
    info = _first_company_info(records)
    if info is None:
        return None
    return CompanyCreateData(
        street=info.street,
        city=info.city,
        zip_code=info.postal_code,
        country=info.country,
        state=info.state,
        industry=info.industry,
        headcount=info.headcount,
        description=info.description,
    )


def _safe_error_message(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    lowered = text.lower()
    for secret_marker in ("api_key", "authorization", "bearer ", "password", "secret"):
        if secret_marker in lowered:
            return f"{type(exc).__name__}: redacted error"
    return text[:500]
