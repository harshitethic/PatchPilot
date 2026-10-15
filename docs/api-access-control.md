# PatchPilot HTTP API access-control boundary

PatchPilot can clone repositories, modify code, execute isolated tests, and
delete local workspaces. CORS is NOT an authentication mechanism.

## Local-only default

Without PATCHPILOT_API_TOKEN, the HTTP middleware requires both an actual ASGI
loopback client address (127.0.0.0/8 or ::1), and a loopback Host header
(localhost, 127.0.0.1 or [::1], optionally with a port).

Every Origin header is compared against the explicit trusted browser-origin
list before a handler executes. Default origins include local development
servers on ports 5500 and 5173. Arbitrary origins and cross-site attempts to
delete workspaces are rejected even when the client appears to be local.

This blocks direct remote requests and common DNS-rebinding or drive-by
browser attacks. It does NOT block a malicious process already running under
the current local user. The API still has no multi-user identity system.

## Optional strong bearer token

Generate a random token on your own computer:

    python3 -c 'import secrets; print(secrets.token_urlsafe(32))'

Supply PATCHPILOT_API_TOKEN in the environment of the Uvicorn process, not in
tracked source, screenshots or public logs. For instance:

    export PATCHPILOT_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000

When configured, every API endpoint (including health and docs) requires:

    Authorization: Bearer <the generated token>

The local frontend has a password-style input for the token. The input
is not persisted in cookies, localStorage or a browser account. A correctly
configured token permits explicit remote HTTP clients, but does not add TLS.

If the configured token is shorter than 32, longer than 512, or contains
whitespace, every application request fails closed (503). Missing/invalid
or duplicate authorization headers are rejected (401) and are not logged.

## CORS, preflight and deployment

Browser origins must be trusted even when presenting a token. CORS
preflight OPTIONS requests are handled without executing route handlers;
the subsequent actual request still requires authorization. Approved
browser origins receive CORS headers on error responses, enabling the
frontend to display useful 401 errors.

For remote use, put PatchPilot behind a properly configured HTTPS reverse
proxy with independent access controls and rate limiting. Never forward
tokens over unencrypted networks. This is ONE shared secret, not roles,
revocation, tenant isolation, or a production multi-user architecture.

Repository Git operations and the AI backend still run under the service
account. Model-selected tests run in a separate restricted Docker container.
Those controls are not a substitute for independent host/workspace isolation.

## Reproduce HTTP security regressions

    PYTHONPATH=backend python3 -m unittest backend.tests.test_api_access -v
    PYTHONPATH=backend python3 -m unittest discover -s backend/tests -v

The tests use the real ASGI app and exercise loopback/remote IP addresses,
forged Host headers, browser-origin CSRF, CORS preflight, bearer tokens,
duplicate headers, deliberately weak tokens and workspace mutating routes.
These are targeted regressions, not a comprehensive security audit.