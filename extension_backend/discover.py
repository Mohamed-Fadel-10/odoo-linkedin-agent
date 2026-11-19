from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from mcp_server.server import import_contacts_impl
from mcp_server.validation import validate_company, validate_domain
from pipeline.processor import RecordStatus
from pipeline.run_store import (
    DISCOVERY_STATUS_FAILED,
    DISCOVERY_STATUS_RUNNING,
    DISCOVERY_STATUS_SUCCEEDED,
    DiscoveryJob,
    RunStore,
    StoredRun,
)

from extension_backend.hermes_runner import run_hermes_discover
from extension_backend.settings import ExtensionBackendSettings
from extension_backend.url_parse import LinkedInCompanyUrl, parse_linkedin_company_url


logger = logging.getLogger("extension_backend.discover")

STAGE_HERMES = "hermes"
STAGE_PREVIEW = "preview"


def validate_run_for_discover(
    store: RunStore,
    *,
    run_id: str,
    company: str,
    domain: str,
    started_at: datetime,
) -> None:
    try:
        UUID(run_id)
    except ValueError as exc:
        raise ValueError("run_id must be a UUID") from exc

    stored = store.get_run(run_id)
    if stored is None:
        raise ValueError("unknown run_id from Hermes")
    if stored.company != company:
        raise ValueError("run company does not match request")
    if stored.domain != domain:
        raise ValueError("run domain does not match request")

    created = stored.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    started = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
    if created < started:
        raise ValueError("run_id predates this discover request")


def records_for_preview(
    stored: StoredRun,
    *,
    import_status_by_id: dict[str, str | None] | None = None,
    partner_id_by_id: dict[str, int | None] | None = None,
) -> list[dict[str, Any]]:
    import_map = import_status_by_id or {}
    partner_map = partner_id_by_id or {}
    rows: list[dict[str, Any]] = []
    for record in stored.records:
        import_status = import_map.get(record.record_id)
        already_imported = bool(import_status) and import_status != "not_attempted"
        selectable = record.status == RecordStatus.ACCEPTED and not already_imported
        rows.append(
            {
                "record_id": record.record_id,
                "name": record.name,
                "title": record.title,
                "email": record.email,
                "linkedin_url": record.linkedin_url,
                "phone": record.phone,
                "location": record.location,
                "department": record.department,
                "seniority": record.seniority,
                "email_confidence": record.email_confidence,
                "email_verification": record.email_verification,
                "twitter": getattr(record, "twitter", None),
                "sources_count": getattr(record, "sources_count", None),
                "status": record.status.value,
                "reason": record.reason,
                "import_status": import_status or "not_attempted",
                "partner_id": partner_map.get(record.record_id),
                "selectable": selectable,
            }
        )
    return rows


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _duration_ms_for_run(
    store: RunStore, run_id: str, stored: StoredRun
) -> int | None:
    del stored
    try:
        with store.connection() as conn:
            row = conn.execute(
                """
                SELECT created_at, updated_at
                FROM discovery_jobs
                WHERE run_id = %s
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    start = _aware(row["created_at"])
    end = _aware(row["updated_at"])
    ms = int((end - start).total_seconds() * 1000)
    return ms if ms >= 0 else None


def _age_minutes(updated_at: datetime) -> int:
    age = (datetime.now(timezone.utc) - _aware(updated_at)).total_seconds()
    return max(0, int(age // 60))


def resolve_stale_discovery_job(
    store: RunStore,
    job: DiscoveryJob,
    *,
    stale_after_seconds: int,
) -> DiscoveryJob:
    if job.status != DISCOVERY_STATUS_RUNNING:
        return job
    age = (datetime.now(timezone.utc) - _aware(job.updated_at)).total_seconds()
    if age <= stale_after_seconds:
        return job
    updated = store.update_discovery_job(
        job.job_id,
        status=DISCOVERY_STATUS_FAILED,
        error="discovery interrupted before completion",
        stage=None,
    )
    return updated or job


def discovery_job_payload(job: DiscoveryJob) -> dict[str, Any]:
    phase = {
        DISCOVERY_STATUS_RUNNING: "in_progress",
        DISCOVERY_STATUS_SUCCEEDED: "preview_ready",
        DISCOVERY_STATUS_FAILED: "failed",
    }.get(job.status, job.status)
    if job.status == DISCOVERY_STATUS_SUCCEEDED and job.run_id:
        phase = "preview_ready"
    return {
        "job_id": job.job_id,
        "slug": job.slug,
        "company": job.company,
        "domain": job.domain,
        "canonical_url": job.canonical_url,
        "status": job.status,
        "phase": phase,
        "stage": job.stage,
        "run_id": job.run_id,
        "error": job.error,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


def discover_status_payload(
    store: RunStore,
    *,
    slug: str,
    reuse_seconds: int,
    stale_after_seconds: int,
) -> dict[str, Any]:
    slug_clean = slug.strip().lower()
    latest = store.get_latest_discovery_job(slug=slug_clean)
    if latest is not None:
        latest = resolve_stale_discovery_job(
            store, latest, stale_after_seconds=stale_after_seconds
        )

    concurrent = (
        latest is not None and latest.status == DISCOVERY_STATUS_RUNNING
    )
    reusable = False
    age_minutes: int | None = None
    message: str | None = None

    if concurrent and latest is not None:
        message = "discovery already in progress for this company"
    elif (
        latest is not None
        and latest.status == DISCOVERY_STATUS_SUCCEEDED
        and latest.run_id
        and store.get_run(latest.run_id) is not None
    ):
        age_sec = (
            datetime.now(timezone.utc) - _aware(latest.updated_at)
        ).total_seconds()
        if age_sec < reuse_seconds and store.run_phase(latest.run_id) != "imported":
            reusable = True
            age_minutes = _age_minutes(latest.updated_at)
            message = f"Showing results from {age_minutes} min ago"

    return {
        "slug": slug_clean,
        "limited": False,
        "concurrent": concurrent,
        "reusable": reusable,
        "age_minutes": age_minutes,
        "retry_after_seconds": None,
        "message": message,
        "latest_job": discovery_job_payload(latest) if latest else None,
    }


def build_run_detail(store: RunStore, run_id: str) -> dict[str, Any]:
    stored = store.get_run(run_id)
    if stored is None:
        raise ValueError(f"unknown run_id: {run_id}")

    rows = store.get_record_rows(run_id)
    import_map = {
        str(row["record_id"]): row.get("import_status") for row in rows
    }
    partner_map = {
        str(row["record_id"]): row.get("partner_id") for row in rows
    }
    records = records_for_preview(
        stored,
        import_status_by_id=import_map,
        partner_id_by_id=partner_map,
    )
    selected_ids = [r["record_id"] for r in records if r["selectable"]]
    import_counts = store.import_counts(run_id)
    phase = store.run_phase(run_id)
    company_location = next(
        (r.location for r in stored.records if r.location),
        None,
    )
    duration_ms = _duration_ms_for_run(store, run_id, stored)
    return {
        "run_id": stored.run_id,
        "company": stored.company,
        "domain": stored.domain,
        "location": company_location,
        "created_at": _aware(stored.created_at).isoformat(),
        "duration_ms": duration_ms,
        "phase": phase,
        "counts": {
            "total": len(stored.records),
            "accepted": sum(
                1 for r in stored.records if r.status == RecordStatus.ACCEPTED
            ),
        },
        "processing_counts": {
            "Accepted": stored.counts.get("accepted", 0),
            "Duplicate": stored.counts.get("duplicates", 0),
            "Possible Duplicate": stored.counts.get("possible_duplicates", 0),
            "Skipped": stored.counts.get("skipped", 0),
            "Rejected": stored.counts.get("rejected", 0),
            "Total": stored.counts.get("total", 0),
        },
        "import_counts": {
            "Created": import_counts.get("created", 0),
            "Duplicate": import_counts.get("duplicate", 0),
            "Possible Duplicate": import_counts.get("possible_duplicate", 0),
            "Skipped": import_counts.get("skipped", 0)
            + import_counts.get("not_importable", 0),
            "Failed": import_counts.get("failed", 0),
            "Not attempted": import_counts.get("not_attempted", 0),
        },
        "selected_ids": selected_ids,
        "records": records,
    }


def list_run_summaries(
    store: RunStore, *, limit: int = 20, offset: int = 0
) -> list[dict[str, Any]]:
    rows = store.list_runs(limit=limit, offset=offset)
    summaries: list[dict[str, Any]] = []
    for row in rows:
        created = row["created_at"]
        if isinstance(created, datetime):
            created_s = _aware(created).isoformat()
        else:
            created_s = str(created)
        import_counts = row.get("import_counts") or {}
        summaries.append(
            {
                "run_id": row["run_id"],
                "company": row["company"],
                "domain": row["domain"],
                "created_at": created_s,
                "phase": row["phase"],
                "counts": row.get("counts") or {},
                "discovery_in_progress": bool(row.get("discovery_in_progress")),
                "import_counts": {
                    "Created": import_counts.get("created", 0),
                    "Duplicate": import_counts.get("duplicate", 0),
                    "Possible Duplicate": import_counts.get("possible_duplicate", 0),
                    "Skipped": import_counts.get("skipped", 0)
                    + import_counts.get("not_importable", 0),
                    "Failed": import_counts.get("failed", 0),
                    "Not attempted": import_counts.get("not_attempted", 0),
                },
            }
        )
    return summaries


def _preview_payload(
    *,
    store: RunStore,
    job: DiscoveryJob,
    run_id: str,
    company: str,
    domain: str,
    canonical_url: str,
    slug: str,
    preview: dict[str, Any],
    reused: bool,
    age_minutes: int | None,
    duration_ms: int,
) -> dict[str, Any]:
    stored = store.get_run(run_id)
    if stored is None:
        raise ValueError("unknown run_id after discover")
    return {
        "job_id": job.job_id,
        "slug": slug,
        "canonical_url": canonical_url,
        "company": company,
        "domain": domain,
        "run_id": run_id,
        "phase": "preview_ready",
        "status": DISCOVERY_STATUS_SUCCEEDED,
        "stage": None,
        "reused": reused,
        "age_minutes": age_minutes,
        "duration_ms": duration_ms,
        "selected_ids": preview.get("selected_ids", []),
        "confirmation_token": preview.get("confirmation_token"),
        "summary": preview.get("summary", {}),
        "planned": preview.get("planned", []),
        "records": records_for_preview(stored),
        "counts": {
            "total": len(stored.records),
            "accepted": sum(
                1 for r in stored.records if r.status == RecordStatus.ACCEPTED
            ),
        },
    }


def _try_reuse_discovery(
    *,
    store: RunStore,
    latest: DiscoveryJob,
    parsed: LinkedInCompanyUrl,
    company_clean: str,
    domain_clean: str,
    reuse_seconds: int,
) -> dict[str, Any] | None:
    if latest.status != DISCOVERY_STATUS_SUCCEEDED or not latest.run_id:
        return None
    if latest.domain != domain_clean:
        return None
    if store.get_run(latest.run_id) is None:
        return None
    age_sec = (datetime.now(timezone.utc) - _aware(latest.updated_at)).total_seconds()
    if age_sec >= reuse_seconds:
        return None
    if store.run_phase(latest.run_id) == "imported":
        return None

    preview = import_contacts_impl(latest.run_id, dry_run=True)
    if "error" in preview:
        raise ValueError(str(preview["error"]))

    age_minutes = _age_minutes(latest.updated_at)
    logger.info(
        "discovery_timing total_ms=0 reused=true age_min=%s slug=%s domain=%s",
        age_minutes,
        parsed.slug,
        domain_clean,
    )
    return _preview_payload(
        store=store,
        job=latest,
        run_id=latest.run_id,
        company=company_clean,
        domain=domain_clean,
        canonical_url=parsed.canonical_url,
        slug=parsed.slug,
        preview=preview,
        reused=True,
        age_minutes=age_minutes,
        duration_ms=0,
    )


def discover_preview(
    *,
    settings: ExtensionBackendSettings,
    store: RunStore,
    url: str,
    company: str,
    domain: str,
) -> dict[str, Any]:
    parsed: LinkedInCompanyUrl = parse_linkedin_company_url(url)
    company_clean = validate_company(company)
    domain_clean = validate_domain(domain)
    stale_after = settings.discover_timeout_seconds + 30

    latest = store.get_latest_discovery_job(slug=parsed.slug)
    if latest is not None:
        latest = resolve_stale_discovery_job(
            store, latest, stale_after_seconds=stale_after
        )

    if latest is not None and latest.status == DISCOVERY_STATUS_RUNNING:
        payload = discovery_job_payload(latest)
        payload["concurrent"] = True
        payload["reused"] = False
        return payload

    if latest is not None:
        reused = _try_reuse_discovery(
            store=store,
            latest=latest,
            parsed=parsed,
            company_clean=company_clean,
            domain_clean=domain_clean,
            reuse_seconds=settings.discovery_reuse_seconds,
        )
        if reused is not None:
            return reused

    job = store.create_discovery_job(
        slug=parsed.slug,
        company=company_clean,
        domain=domain_clean,
        canonical_url=parsed.canonical_url,
        stage=STAGE_HERMES,
    )
    started_at = datetime.now(timezone.utc)
    total_started = time.perf_counter()
    hermes_ms = 0
    preview_ms = 0
    try:
        store.update_discovery_job(job.job_id, stage=STAGE_HERMES)
        hermes_started = time.perf_counter()
        run_id = run_hermes_discover(
            company=company_clean,
            domain=domain_clean,
            hermes_bin=settings.hermes_bin,
            timeout_seconds=settings.discover_timeout_seconds,
            model=settings.hermes_model,
            provider=settings.hermes_provider,
            reasoning=settings.hermes_reasoning,
            toolsets=settings.hermes_toolsets,
            max_turns=settings.hermes_max_turns,
        )
        hermes_ms = int((time.perf_counter() - hermes_started) * 1000)

        validate_run_for_discover(
            store,
            run_id=run_id,
            company=company_clean,
            domain=domain_clean,
            started_at=started_at,
        )

        store.update_discovery_job(job.job_id, stage=STAGE_PREVIEW)
        preview_started = time.perf_counter()
        preview = import_contacts_impl(run_id, dry_run=True)
        preview_ms = int((time.perf_counter() - preview_started) * 1000)
        if "error" in preview:
            raise ValueError(str(preview["error"]))

        store.update_discovery_job(
            job.job_id,
            status=DISCOVERY_STATUS_SUCCEEDED,
            run_id=run_id,
            error=None,
            stage=None,
        )

        total_ms = int((time.perf_counter() - total_started) * 1000)
        logger.info(
            "discovery_timing total_ms=%s hermes_ms=%s preview_ms=%s "
            "slug=%s domain=%s reused=false",
            total_ms,
            hermes_ms,
            preview_ms,
            parsed.slug,
            domain_clean,
        )
        return _preview_payload(
            store=store,
            job=job,
            run_id=run_id,
            company=company_clean,
            domain=domain_clean,
            canonical_url=parsed.canonical_url,
            slug=parsed.slug,
            preview=preview,
            reused=False,
            age_minutes=None,
            duration_ms=total_ms,
        )
    except Exception as exc:
        store.update_discovery_job(
            job.job_id,
            status=DISCOVERY_STATUS_FAILED,
            error=_safe_job_error(exc),
            stage=None,
        )
        raise


def _safe_job_error(exc: Exception) -> str:
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
            return f"{type(exc).__name__}: discovery failed"
    return message[:500]
