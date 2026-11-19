from __future__ import annotations

import hmac
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from extension_backend.settings import PAIRING_HEADER, ExtensionBackendSettings


def expected_extension_origin(extension_id: str) -> str:
    return f"chrome-extension://{extension_id}"


def _host_allowed(host: str | None) -> bool:
    if not host:
        return False
    hostname = host.split(":", 1)[0].strip().lower()
    return hostname in {"127.0.0.1", "localhost"}


def authorize_request(
    *,
    settings: ExtensionBackendSettings,
    origin: str | None,
    host: str | None,
    pairing_token: str | None,
) -> str | None:
    if not _host_allowed(host):
        return "forbidden host"
    provided = (pairing_token or "").strip()
    if not provided or not hmac.compare_digest(provided, settings.pairing_token):
        return "invalid pairing token"
    if not settings.extension_id:
        return "extension id is not configured"
    expected = expected_extension_origin(settings.extension_id)
    if origin is None or origin.strip() == "":
        return None
    if origin.strip() != expected:
        return "forbidden origin"
    return None


class ExtensionAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: object,
        *,
        settings_provider: Callable[[], ExtensionBackendSettings],
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._settings_provider = settings_provider

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)

        settings = self._settings_provider()
        origin = request.headers.get("origin")
        error = authorize_request(
            settings=settings,
            origin=origin,
            host=request.headers.get("host"),
            pairing_token=request.headers.get(PAIRING_HEADER),
        )
        if error:
            response = JSONResponse(status_code=403, content={"error": error})
            if settings.extension_id and origin == expected_extension_origin(
                settings.extension_id
            ):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Headers"] = (
                    f"Content-Type, {PAIRING_HEADER}"
                )
            return response
        return await call_next(request)
