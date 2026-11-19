from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import UUID, uuid4

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from pipeline.processor import ProcessedRecord, ProcessingResult, RecordStatus


load_dotenv()

DDL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id UUID PRIMARY KEY,
        company TEXT NOT NULL,
        domain TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        counts JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_records (
        run_id UUID NOT NULL REFERENCES runs(run_id),
        record_id TEXT NOT NULL,
        status TEXT NOT NULL,
        reason TEXT,
        record JSONB NOT NULL,
        import_status TEXT,
        partner_id INTEGER,
        imported_at TIMESTAMPTZ,
        PRIMARY KEY (run_id, record_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_attempts (
        id BIGSERIAL PRIMARY KEY,
        run_id UUID NOT NULL,
        record_id TEXT NOT NULL,
        action TEXT NOT NULL,
        outcome TEXT NOT NULL,
        reason TEXT,
        timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS discovery_jobs (
        job_id UUID PRIMARY KEY,
        slug TEXT NOT NULL,
        company TEXT NOT NULL,
        domain TEXT NOT NULL,
        canonical_url TEXT NOT NULL,
        status TEXT NOT NULL,
        run_id UUID,
        error TEXT,
        stage TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    ALTER TABLE discovery_jobs ADD COLUMN IF NOT EXISTS stage TEXT
    """,
    """
    CREATE INDEX IF NOT EXISTS discovery_jobs_slug_updated_idx
    ON discovery_jobs (slug, updated_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS runs_created_at_idx
    ON runs (created_at DESC)
    """,
)

DISCOVERY_STATUS_RUNNING = "running"
DISCOVERY_STATUS_SUCCEEDED = "succeeded"
DISCOVERY_STATUS_FAILED = "failed"

_UNSET: object = object()


class StoredRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    company: str
    domain: str
    created_at: datetime
    counts: dict[str, int]
    records: list[ProcessedRecord] = Field(default_factory=list)


class DiscoveryJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    slug: str
    company: str
    domain: str
    canonical_url: str
    status: str
    run_id: str | None = None
    error: str | None = None
    stage: str | None = None
    created_at: datetime
    updated_at: datetime


class RunStore:
    def __init__(self, database_url: str | None = None) -> None:
        url = (database_url or os.getenv("DATABASE_URL", "")).strip()
        if not url:
            raise ValueError("DATABASE_URL is required")
        self.database_url = url

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[Any]]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            yield conn

    def ensure_schema(self) -> None:
        with self.connection() as conn:
            for statement in DDL_STATEMENTS:
                conn.execute(statement)
            conn.commit()

    def save_processing_result(
        self,
        result: ProcessingResult,
        *,
        company: str,
        domain: str,
    ) -> StoredRun:
        run_id = result.run_id.strip()
        try:
            UUID(run_id)
        except ValueError as exc:
            raise ValueError(f"run_id must be a UUID: {run_id!r}") from exc

        created_at = datetime.now(timezone.utc)
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO runs (run_id, company, domain, created_at, counts)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (run_id, company, domain, created_at, Jsonb(result.counts)),
            )
            for record in result.all_records:
                conn.execute(
                    """
                    INSERT INTO run_records (
                        run_id, record_id, status, reason, record,
                        import_status, partner_id, imported_at
                    )
                    VALUES (%s, %s, %s, %s, %s, NULL, NULL, NULL)
                    ON CONFLICT (run_id, record_id) DO NOTHING
                    """,
                    (
                        run_id,
                        record.record_id,
                        record.status.value,
                        record.reason,
                        Jsonb(record.model_dump(mode="json")),
                    ),
                )
            conn.commit()

        return StoredRun(
            run_id=run_id,
            company=company,
            domain=domain,
            created_at=created_at,
            counts=dict(result.counts),
            records=list(result.all_records),
        )

    def get_run(self, run_id: str) -> StoredRun | None:
        try:
            UUID(run_id)
        except ValueError:
            return None

        with self.connection() as conn:
            run_row = conn.execute(
                "SELECT run_id, company, domain, created_at, counts FROM runs WHERE run_id = %s",
                (run_id,),
            ).fetchone()
            if run_row is None:
                return None
            record_rows = conn.execute(
                """
                SELECT record, import_status, partner_id, imported_at
                FROM run_records
                WHERE run_id = %s
                ORDER BY record_id
                """,
                (run_id,),
            ).fetchall()

        records: list[ProcessedRecord] = []
        for row in record_rows:
            payload = row["record"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            record = ProcessedRecord.model_validate(payload)
            records.append(record)

        counts = run_row["counts"]
        if isinstance(counts, str):
            counts = json.loads(counts)

        return StoredRun(
            run_id=str(run_row["run_id"]),
            company=run_row["company"],
            domain=run_row["domain"],
            created_at=run_row["created_at"],
            counts=dict(counts),
            records=records,
        )

    def get_record_rows(self, run_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT record_id, status, reason, record, import_status, partner_id, imported_at
                FROM run_records
                WHERE run_id = %s
                ORDER BY record_id
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def lock_run(self, conn: psycopg.Connection[Any], run_id: str) -> dict[str, Any] | None:
        return conn.execute(
            "SELECT run_id, company, domain, counts FROM runs WHERE run_id = %s FOR UPDATE",
            (run_id,),
        ).fetchone()

    def get_import_state(
        self,
        conn: psycopg.Connection[Any],
        run_id: str,
        record_id: str,
    ) -> dict[str, Any] | None:
        return conn.execute(
            """
            SELECT record_id, status, reason, record, import_status, partner_id, imported_at
            FROM run_records
            WHERE run_id = %s AND record_id = %s
            """,
            (run_id, record_id),
        ).fetchone()

    def commit_import_outcome(
        self,
        conn: psycopg.Connection[Any],
        *,
        run_id: str,
        record_id: str,
        action: str,
        outcome: str,
        reason: str | None,
        partner_id: int | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        conn.execute(
            """
            UPDATE run_records
            SET import_status = %s,
                partner_id = COALESCE(%s, partner_id),
                imported_at = CASE
                    WHEN %s IN ('created', 'duplicate', 'failed', 'skipped', 'not_importable')
                    THEN COALESCE(imported_at, %s)
                    ELSE imported_at
                END
            WHERE run_id = %s AND record_id = %s
            """,
            (outcome, partner_id, outcome, now, run_id, record_id),
        )
        conn.execute(
            """
            INSERT INTO import_attempts (run_id, record_id, action, outcome, reason, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (run_id, record_id, action, outcome, reason, now),
        )

    def import_counts(self, run_id: str) -> dict[str, int]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT COALESCE(import_status, 'not_attempted') AS status, COUNT(*) AS n
                FROM run_records
                WHERE run_id = %s
                GROUP BY COALESCE(import_status, 'not_attempted')
                """,
                (run_id,),
            ).fetchall()
        counts = {
            "created": 0,
            "duplicate": 0,
            "possible_duplicate": 0,
            "skipped": 0,
            "failed": 0,
            "not_importable": 0,
            "not_attempted": 0,
        }
        for row in rows:
            key = str(row["status"])
            counts[key] = int(row["n"])
        return counts

    def list_runs(self, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT run_id, company, domain, created_at, counts
                FROM runs
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            ).fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                counts = row["counts"]
                if isinstance(counts, str):
                    counts = json.loads(counts)
                run_id = str(row["run_id"])
                results.append(
                    {
                        "run_id": run_id,
                        "company": row["company"],
                        "domain": row["domain"],
                        "created_at": row["created_at"],
                        "counts": dict(counts),
                        "phase": self.run_phase(run_id),
                        "import_counts": self.import_counts(run_id),
                        "discovery_in_progress": self._run_discovery_in_progress(
                            conn, run_id
                        ),
                    }
                )
        return results

    def run_phase(self, run_id: str) -> str:
        counts = self.import_counts(run_id)
        attempted = (
            counts.get("created", 0)
            + counts.get("duplicate", 0)
            + counts.get("possible_duplicate", 0)
            + counts.get("skipped", 0)
            + counts.get("failed", 0)
            + counts.get("not_importable", 0)
        )
        return "imported" if attempted > 0 else "preview_ready"

    def _discovery_job_from_row(self, row: dict[str, Any]) -> DiscoveryJob:
        return DiscoveryJob(
            job_id=str(row["job_id"]),
            slug=row["slug"],
            company=row["company"],
            domain=row["domain"],
            canonical_url=row["canonical_url"],
            status=row["status"],
            run_id=str(row["run_id"]) if row.get("run_id") is not None else None,
            error=row.get("error"),
            stage=row.get("stage"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_discovery_job(
        self,
        *,
        slug: str,
        company: str,
        domain: str,
        canonical_url: str,
        stage: str | None = None,
    ) -> DiscoveryJob:
        job_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO discovery_jobs (
                    job_id, slug, company, domain, canonical_url,
                    status, run_id, error, stage, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s)
                """,
                (
                    job_id,
                    slug.strip().lower(),
                    company,
                    domain,
                    canonical_url,
                    DISCOVERY_STATUS_RUNNING,
                    stage,
                    now,
                    now,
                ),
            )
            conn.commit()
        return DiscoveryJob(
            job_id=job_id,
            slug=slug.strip().lower(),
            company=company,
            domain=domain,
            canonical_url=canonical_url,
            status=DISCOVERY_STATUS_RUNNING,
            run_id=None,
            error=None,
            stage=stage,
            created_at=now,
            updated_at=now,
        )

    def update_discovery_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        run_id: str | None | object = _UNSET,
        error: str | None | object = _UNSET,
        stage: str | None | object = _UNSET,
    ) -> DiscoveryJob | None:
        try:
            UUID(job_id)
        except ValueError:
            return None
        now = datetime.now(timezone.utc)
        with self.connection() as conn:
            current = conn.execute(
                """
                SELECT status, run_id, error, stage
                FROM discovery_jobs
                WHERE job_id = %s
                """,
                (job_id,),
            ).fetchone()
            if current is None:
                return None
            new_status = status if status is not None else current["status"]
            new_run_id = current["run_id"] if run_id is _UNSET else run_id
            new_error = current["error"] if error is _UNSET else error
            new_stage = current.get("stage") if stage is _UNSET else stage
            conn.execute(
                """
                UPDATE discovery_jobs
                SET status = %s,
                    run_id = %s,
                    error = %s,
                    stage = %s,
                    updated_at = %s
                WHERE job_id = %s
                """,
                (new_status, new_run_id, new_error, new_stage, now, job_id),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT job_id, slug, company, domain, canonical_url, status,
                       run_id, error, stage, created_at, updated_at
                FROM discovery_jobs
                WHERE job_id = %s
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return self._discovery_job_from_row(dict(row))

    def get_discovery_job(self, job_id: str) -> DiscoveryJob | None:
        try:
            UUID(job_id)
        except ValueError:
            return None
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT job_id, slug, company, domain, canonical_url, status,
                       run_id, error, stage, created_at, updated_at
                FROM discovery_jobs
                WHERE job_id = %s
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return self._discovery_job_from_row(dict(row))

    def get_latest_discovery_job(self, *, slug: str | None = None) -> DiscoveryJob | None:
        with self.connection() as conn:
            if slug:
                row = conn.execute(
                    """
                    SELECT job_id, slug, company, domain, canonical_url, status,
                           run_id, error, stage, created_at, updated_at
                    FROM discovery_jobs
                    WHERE slug = %s
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (slug.strip().lower(),),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT job_id, slug, company, domain, canonical_url, status,
                           run_id, error, stage, created_at, updated_at
                    FROM discovery_jobs
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                ).fetchone()
        if row is None:
            return None
        return self._discovery_job_from_row(dict(row))

    def _run_discovery_in_progress(
        self, conn: psycopg.Connection[Any], run_id: str
    ) -> bool:
        row = conn.execute(
            """
            SELECT 1
            FROM discovery_jobs j
            WHERE j.status = %s
              AND (
                j.run_id = %s::uuid
                OR (
                  j.run_id IS NULL
                  AND EXISTS (
                    SELECT 1 FROM runs r
                    WHERE r.run_id = %s::uuid
                      AND r.company = j.company
                      AND r.domain = j.domain
                      AND r.created_at >= j.created_at
                  )
                )
              )
            LIMIT 1
            """,
            (DISCOVERY_STATUS_RUNNING, run_id, run_id),
        ).fetchone()
        return row is not None

    def delete_run(self, run_id: str) -> dict[str, str]:
        try:
            UUID(run_id)
        except ValueError as exc:
            raise ValueError("run_id must be a UUID") from exc

        with self.connection() as conn:
            run_row = conn.execute(
                "SELECT run_id, company FROM runs WHERE run_id = %s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if run_row is None:
                raise RunNotFoundError(f"unknown run_id: {run_id}")
            if self._run_discovery_in_progress(conn, run_id):
                raise RunInProgressError(
                    "cannot delete a run while discovery is in progress"
                )

            conn.execute("DELETE FROM run_records WHERE run_id = %s", (run_id,))
            conn.execute(
                "UPDATE discovery_jobs SET run_id = NULL WHERE run_id = %s",
                (run_id,),
            )
            conn.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
            conn.commit()
        return {"run_id": str(run_row["run_id"]), "company": str(run_row["company"])}

    def delete_all_runs_except_in_progress(self) -> int:
        deleted = 0
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT run_id FROM runs ORDER BY created_at DESC"
            ).fetchall()
            for row in rows:
                run_id = str(row["run_id"])
                if self._run_discovery_in_progress(conn, run_id):
                    continue
                conn.execute("DELETE FROM run_records WHERE run_id = %s", (run_id,))
                conn.execute(
                    "UPDATE discovery_jobs SET run_id = NULL WHERE run_id = %s",
                    (run_id,),
                )
                conn.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
                deleted += 1
            conn.commit()
        return deleted


def new_run_id() -> str:
    return str(uuid4())


def status_is_importable(status: RecordStatus | str) -> bool:
    value = status.value if isinstance(status, RecordStatus) else status
    return value == RecordStatus.ACCEPTED.value


class RunNotFoundError(LookupError):
    pass


class RunInProgressError(RuntimeError):
    pass
