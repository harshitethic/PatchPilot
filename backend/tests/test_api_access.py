"""Real HTTP/ASGI regression tests for local access, bearer auth and CSRF."""
import os
import unittest
from unittest.mock import patch

import httpx

from app.access_control import (
    configured_api_token,
    is_local_host,
    is_loopback_client,
)
from app.main import app

TOKEN = "test-only-strong-PatchPilot-token-with-over-32-characters"


class AccessPureTests(unittest.TestCase):
    def test_localhost_host_validation_rejects_dns_rebinding_and_userinfo(self):
        for host in ("localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000"):
            with self.subTest(host=host):
                self.assertTrue(is_local_host(host))
        for host in (
            "localhost.evil.test", "127.0.0.1.evil.test", "evil.test",
            "localhost@evil.test", "localhost:bad", "localhost:99999",
            "localhost/x", "", None, " localhost",
        ):
            with self.subTest(host=host):
                self.assertFalse(is_local_host(host))

    def test_client_address_requires_actual_loopback_ip(self):
        for client in ("127.0.0.1", "::1", "127.15.16.17"):
            self.assertTrue(is_loopback_client(client))
        for client in ("192.0.2.5", "::ffff:192.0.2.5", "localhost", "", None):
            self.assertFalse(is_loopback_client(client))

    def test_token_misconfiguration_fails_closed(self):
        for value in ("short", "a" * 31, "a" * 513, "A" * 33 + "\n", "A" * 33 + " "):
            with self.subTest(value_length=len(value)), patch.dict(
                os.environ, {"PATCHPILOT_API_TOKEN": value}
            ), self.assertRaises(ValueError):
                configured_api_token()
        with patch.dict(os.environ, {"PATCHPILOT_API_TOKEN": TOKEN}):
            self.assertEqual(configured_api_token(), TOKEN)
        with patch.dict(os.environ, {"PATCHPILOT_API_TOKEN": ""}):
            self.assertIsNone(configured_api_token())


class AccessHttpTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, method="GET", path="/api/health", *,
                      client="127.0.0.1", host="localhost",
                      headers=None, json=None, env=None):
        settings = {"PATCHPILOT_API_TOKEN": ""}
        settings.update(env or {})
        transport = httpx.ASGITransport(app=app, client=(client, 12345))
        with patch.dict(os.environ, settings):
            async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}") as session:
                return await session.request(method, path, headers=headers, json=json)

    async def test_local_health_works_without_token(self):
        response = await self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "PatchPilot")

    async def test_remote_caller_denied_without_token_even_on_local_host_header(self):
        response = await self.request(client="198.51.100.8")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Traceback", response.text)

    async def test_malicious_host_denied_even_when_remote_ip_spoofs_loopback(self):
        response = await self.request(host="malicious.example")
        self.assertEqual(response.status_code, 403)

    async def test_cross_site_post_denied_before_destructive_handler(self):
        response = await self.request(
            "POST", "/api/workspaces/cleanup",
            headers={"Origin": "https://evil.example", "Content-Type": "application/json"},
            json={"dry_run": False},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("Untrusted browser origin", response.text)

    async def test_local_trusted_frontend_origin_can_call_health(self):
        response = await self.request(headers={"Origin": "http://localhost:5500"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://localhost:5500",
        )

    async def test_browser_can_read_auth_error_without_leaking_to_other_origins(self):
        response = await self.request(
            env={"PATCHPILOT_API_TOKEN": TOKEN},
            headers={"Origin": "http://localhost:5500"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://localhost:5500",
        )
        response = await self.request(
            env={"PATCHPILOT_API_TOKEN": TOKEN},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(response.headers.get("access-control-allow-origin"))

    async def test_token_protects_workspace_mutations_before_handler_validation(self):
        response = await self.request(
            "POST", "/api/workspaces/cleanup",
            json={"dry_run": False},
            env={"PATCHPILOT_API_TOKEN": TOKEN},
        )
        self.assertEqual(response.status_code, 401)
        response = await self.request(
            "POST", "/api/workspace",
            json={"workspace_id": "missing-workspace-id"},
            env={"PATCHPILOT_API_TOKEN": TOKEN},
            headers={"Authorization": "Bearer " + TOKEN},
        )
        self.assertEqual(response.status_code, 404)

    async def test_token_required_even_on_loopback_when_enabled(self):
        env = {"PATCHPILOT_API_TOKEN": TOKEN}
        response = await self.request(env=env)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")
        response = await self.request(env=env, headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status_code, 401)
        response = await self.request(env=env, headers={"Authorization": "Basic " + TOKEN})
        self.assertEqual(response.status_code, 401)
        response = await self.request(env=env, headers={"Authorization": "Bearer " + TOKEN})
        self.assertEqual(response.status_code, 200)

    async def test_remote_with_valid_token_allowed_but_origin_still_checked(self):
        headers = {"Authorization": "Bearer " + TOKEN}
        env = {"PATCHPILOT_API_TOKEN": TOKEN}
        ok = await self.request(client="198.51.100.5", host="secure.example", headers=headers, env=env)
        self.assertEqual(ok.status_code, 200)
        blocked = await self.request(
            client="198.51.100.5", host="secure.example",
            headers={**headers, "Origin": "https://evil.example"}, env=env,
        )
        self.assertEqual(blocked.status_code, 403)

    async def test_duplicate_host_headers_rejected_in_local_and_token_modes(self):
        for env in ({}, {"PATCHPILOT_API_TOKEN": TOKEN}):
            with self.subTest(token_enabled=bool(env)):
                headers = [("Host", "localhost"), ("Host", "malicious.example")]
                if env:
                    headers.append(("Authorization", "Bearer " + TOKEN))
                response = await self.request(headers=headers, env=env)
                self.assertEqual(response.status_code, 400)
                self.assertIn("Host header", response.text)

    async def test_duplicate_auth_headers_refused(self):
        token = TOKEN
        response = await self.request(
            env={"PATCHPILOT_API_TOKEN": token},
            headers=[("Authorization", "Bearer " + token), ("Authorization", "Bearer " + token)],
        )
        self.assertEqual(response.status_code, 401)

    async def test_weak_token_config_never_falls_back_to_local_no_auth(self):
        response = await self.request(env={"PATCHPILOT_API_TOKEN": "weak"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("weak", response.text)

    async def test_preflight_does_not_execute_handler(self):
        response = await self.request(
            "OPTIONS", "/api/run",
            headers={"Origin": "http://localhost:5500",
                     "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "Authorization, Content-Type"},
            env={"PATCHPILOT_API_TOKEN": TOKEN},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5500")
        response = await self.request(
            "OPTIONS", "/api/run",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        self.assertNotEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()