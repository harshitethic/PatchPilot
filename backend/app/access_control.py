"""Fail-closed HTTP boundary for a powerful local code-editing API.

This module deliberately does NOT trust CORS to authenticate requests.
Local mode (no token configured) is limited to direct loopback callers and
loopback Host headers. Token mode allows explicit remote use through a
separately configured HTTPS reverse proxy; it is not multi-user authorization.
"""
from __future__ import annotations

import hmac
import ipaddress
import os
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


def configured_api_token() -> str | None:
    value = os.environ.get("PATCHPILOT_API_TOKEN", "")
    if not value:
        return None
    if not 32 <= len(value) <= 512 or any(ch.isspace() for ch in value):
        raise ValueError("PATCHPILOT_API_TOKEN must be a strong 32-512 character token")
    return value


def is_loopback_client(address: str | None) -> bool:
    if not address:
        return False
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def is_local_host(host: str | None) -> bool:
    if not host or "@" in host or any(c.isspace() for c in host):
        return False
    try:
        parsed = urlsplit("//" + host)
        return (
            parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and (parsed.port is None or 1 <= parsed.port <= 65535)
            and not parsed.path and not parsed.query and not parsed.fragment
            and parsed.username is None and parsed.password is None
        )
    except ValueError:
        return False


class ApiAccessMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, allowed_origins):
        super().__init__(app)
        self.allowed_origins = allowed_origins

    async def dispatch(self, request: Request, call_next):
        # CORS preflight must be handled by the CORS layer. Preflights cannot
        # execute API handlers; actual requests still require access checks.
        if request.method == "OPTIONS" and "access-control-request-method" in request.headers:
            return await call_next(request)

        # Duplicate Host headers are ambiguous across proxies and ASGI servers.
        # Do not let a different hop authorize a different authority.
        if len(request.headers.getlist("host")) != 1:
            return JSONResponse({"detail": "Exactly one Host header is required"}, status_code=400)

        origin = request.headers.get("origin")
        if origin is not None and origin not in self.allowed_origins():
            return JSONResponse({"detail": "Untrusted browser origin"}, status_code=403)

        try:
            token = configured_api_token()
        except ValueError:
            # An accidentally weak configured secret must NEVER silently
            # revert to unauthenticated loopback mode.
            return JSONResponse({"detail": "API authentication is misconfigured"}, status_code=503)

        if token is not None:
            authorization = request.headers.getlist("authorization")
            if (
                len(authorization) != 1
                or not authorization[0].startswith("Bearer ")
                or not hmac.compare_digest(authorization[0][7:], token)
            ):
                return JSONResponse(
                    {"detail": "Valid API bearer token required"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
        elif not (
            is_loopback_client(request.client.host if request.client else None)
            and is_local_host(request.headers.get("host"))
        ):
            return JSONResponse(
                {"detail": "Local-only mode: a bearer token is required for remote access"},
                status_code=403,
            )

        return await call_next(request)