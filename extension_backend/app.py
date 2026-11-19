from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from extension_backend.discover import (
    build_run_detail,
    discover_preview,
    discover_status_payload,
    discovery_job_payload,
    list_run_summaries,
    resolve_stale_discovery_job,
)
from extension_backend.security import ExtensionAuthMiddleware, expected_extension_origin
from extension_backend.settings import (
    PAIRING_HEADER,
    ExtensionBackendSettings,
    load_settings,
)
from mcp_server.server import get_report_impl, import_contacts_impl
from mcp_server.validation import validate_domain, validate_run_id, validate_selected_ids
from pipeline.run_store import (
    DISCOVERY_STATUS_RUNNING,
    RunInProgressError,
    RunNotFoundError,
    RunStore,
)


logger = logging.getLogger("extension_backend")

_settings: ExtensionBackendSettings | None = None


def configure_extension_backend(
    *,
    settings: ExtensionBackendSettings | None = None,
) -> None:
    global _settings
    _settings = settings


def get_settings() -> ExtensionBackendSettings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def get_store() -> RunStore:
    return RunStore()


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


def _error(
    status_code: int,
    message: str,
    *,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"error": message}
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body)


class DiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    company: str
    domain: str


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    selected_ids: list[str] = Field(min_length=1)
    confirmation_token: str | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="odoo-linkedin-agent extension backend", version="0.1.0")

    def _settings_provider() -> ExtensionBackendSettings:
        return get_settings()

    settings = _settings_provider()
    origins = (
        [expected_extension_origin(settings.extension_id)]
        if settings.extension_id
        else []
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", PAIRING_HEADER],
    )
    app.add_middleware(ExtensionAuthMiddleware, settings_provider=_settings_provider)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/runs")
    def list_runs(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        try:
            runs = list_run_summaries(get_store(), limit=limit, offset=offset)
            return {"runs": runs, "limit": limit, "offset": offset}
        except Exception:  # noqa: BLE001
            logger.error("list_runs failed: %s", "Exception")
            return _error(500, "list runs failed")

    @app.delete("/runs")
    def clear_runs() -> Any:
        try:
            deleted = get_store().delete_all_runs_except_in_progress()
            return {"deleted": deleted}
        except Exception:  # noqa: BLE001
            logger.error("clear_runs failed: %s", "Exception")
            return _error(500, "clear runs failed")

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> Any:
        try:
            clean = validate_run_id(run_id)
            return build_run_detail(get_store(), clean)
        except ValueError as exc:
            return _error(400, _safe_public_message(exc))
        except Exception:  # noqa: BLE001
            logger.error("get_run failed: %s", "Exception")
            return _error(500, "get run failed")

    @app.delete("/runs/{run_id}")
    def delete_run(run_id: str) -> Any:
        try:
            clean = validate_run_id(run_id)
            result = get_store().delete_run(clean)
            return {"deleted": True, **result}
        except ValueError as exc:
            return _error(400, _safe_public_message(exc))
        except RunNotFoundError as exc:
            return _error(404, _safe_public_message(exc))
        except RunInProgressError as exc:
            return _error(409, _safe_public_message(exc))
        except Exception:  # noqa: BLE001
            logger.error("delete_run failed: %s", "Exception")
            return _error(500, "delete run failed")

    @app.get("/discovery-jobs/latest")
    def latest_discovery_job(
        slug: str | None = Query(default=None),
    ) -> Any:
        try:
            settings_local = get_settings()
            store = get_store()
            job = store.get_latest_discovery_job(slug=slug)
            if job is None:
                return {"job": None}
            job = resolve_stale_discovery_job(
                store,
                job,
                stale_after_seconds=settings_local.discover_timeout_seconds + 30,
            )
            payload = discovery_job_payload(job)
            if job.run_id:
                try:
                    detail = build_run_detail(store, job.run_id)
                    payload["phase"] = detail["phase"]
                except ValueError:
                    pass
            return {"job": payload}
        except Exception:  # noqa: BLE001
            logger.error("latest_discovery_job failed: %s", "Exception")
            return _error(500, "latest discovery job failed")

    @app.get("/discovery-jobs/{job_id}")
    def get_discovery_job(job_id: str) -> Any:
        try:
            settings_local = get_settings()
            store = get_store()
            job = store.get_discovery_job(job_id)
            if job is None:
                return _error(400, "unknown job_id")
            job = resolve_stale_discovery_job(
                store,
                job,
                stale_after_seconds=settings_local.discover_timeout_seconds + 30,
            )
            payload = discovery_job_payload(job)
            if job.run_id:
                try:
                    detail = build_run_detail(store, job.run_id)
                    payload["phase"] = detail["phase"]
                except ValueError:
                    pass
            return payload
        except Exception:  # noqa: BLE001
            logger.error("get_discovery_job failed: %s", "Exception")
            return _error(500, "get discovery job failed")

    @app.get("/discover-status")
    def discover_status(slug: str = Query(min_length=1)) -> Any:
        try:
            settings_local = get_settings()
            return discover_status_payload(
                get_store(),
                slug=slug,
                reuse_seconds=settings_local.discovery_reuse_seconds,
                stale_after_seconds=settings_local.discover_timeout_seconds + 30,
            )
        except Exception:  # noqa: BLE001
            logger.error("discover_status failed: %s", "Exception")
            return _error(500, "discover status failed")

    @app.post("/discover")
    def discover(body: DiscoverRequest) -> Any:
        try:
            validate_domain(body.domain)
            result = discover_preview(
                settings=get_settings(),
                store=get_store(),
                url=body.url,
                company=body.company,
                domain=body.domain,
            )
            if (
                isinstance(result, dict)
                and result.get("concurrent")
                and result.get("status") == DISCOVERY_STATUS_RUNNING
            ):
                return result
            return result
        except ValueError as exc:
            return _error(400, _safe_public_message(exc))
        except Exception:  # noqa: BLE001
            logger.error("discover failed: %s", "Exception")
            return _error(500, "discover failed")

    @app.post("/approve")
    def approve(body: ApproveRequest) -> Any:
        try:
            run_id = validate_run_id(body.run_id)
            selected = validate_selected_ids(body.selected_ids, required=True)
            assert selected is not None
            preview = import_contacts_impl(run_id, dry_run=True, selected_ids=selected)
            if isinstance(preview, dict) and preview.get("error"):
                raise ValueError(str(preview["error"]))
            token = str(preview.get("confirmation_token") or "").strip()
            if not token:
                raise ValueError("confirmation_token missing from dry-run preview")
            bound_ids = preview.get("selected_ids") or selected

            result = import_contacts_impl(
                run_id,
                dry_run=False,
                selected_ids=bound_ids,
                confirmation_token=token,
            )
            if isinstance(result, dict) and result.get("error"):
                raise ValueError(str(result["error"]))
            return result
        except ValueError as exc:
            return _error(400, _safe_public_message(exc))
        except Exception:  # noqa: BLE001
            logger.error("approve failed: %s", "Exception")
            return _error(500, "approve failed")

    @app.get("/report/{run_id}")
    def report(run_id: str) -> Any:
        try:
            clean = validate_run_id(run_id)
            result = get_report_impl(clean)
            if isinstance(result, dict) and result.get("error"):
                raise ValueError(str(result["error"]))
            return result
        except ValueError as exc:
            return _error(400, _safe_public_message(exc))
        except Exception:  # noqa: BLE001
            logger.error("report failed: %s", "Exception")
            return _error(500, "report failed")

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, _exc: Exception) -> JSONResponse:
        logger.error("unhandled: %s", "Exception")
        return _error(500, "request failed")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = load_settings()
    if not settings.extension_id:
        raise SystemExit(
            "EXTENSION_ID is missing in .env. Load the unpacked extension in Chrome, "
            "copy its ID from chrome://extensions, set EXTENSION_ID, then restart."
        )
    configure_extension_backend(settings=settings)
    try:
        get_store().ensure_schema()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Failed to ensure database schema: {exc}") from exc
    application = create_app()
    uvicorn.run(
        application,
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
