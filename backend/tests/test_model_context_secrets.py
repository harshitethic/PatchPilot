"""Context sent to LLM providers should never contain obvious local secrets."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app.main import ask_model, list_files, read_repo_context


class ModelContextSecretTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()

    def test_private_environment_and_credentials_excluded_from_context(self):
        private = self.repo / ".env.production"
        private.write_text("API_KEY=PRIVATE_PROVIDER_KEY_MUST_NOT_LEAK\n")
        creds = self.repo / "config" / "credentials.json"
        creds.parent.mkdir()
        creds.write_text('{"access_token":"CREDENTIAL_FILE_MUST_NOT_LEAK"}')
        safe = self.repo / "service.py"
        safe.write_text("def healthy():\n    return True\n")
        files = list_files(self.repo)
        context = read_repo_context(self.repo, files)
        self.assertEqual(files, ["service.py"])
        self.assertIn("def healthy()", context)
        self.assertNotIn("PRIVATE_PROVIDER_KEY", context)
        self.assertNotIn("CREDENTIAL_FILE", context)

    def test_secret_signature_in_normal_source_file_does_not_reach_model(self):
        secret = "ghp_" + "Q" * 36
        (self.repo / "unsafe.py").write_text('API_KEY = "' + secret + '"')
        (self.repo / "safe.py").write_text("answer = 42")
        files = list_files(self.repo)
        self.assertIn("unsafe.py", files)
        context = read_repo_context(self.repo, files)
        self.assertNotIn(secret, context)
        self.assertNotIn("unsafe.py", context)
        self.assertIn("answer = 42", context)

    def test_explicit_sensitive_context_path_cannot_bypass_file_list(self):
        (self.repo / ".npmrc").write_text("private-npm-registry-auth")
        leaked = read_repo_context(self.repo, [".npmrc"])
        self.assertNotIn("private-npm-registry-auth", leaked)

    def test_oversized_repository_file_not_loaded_into_model_memory(self):
        (self.repo / "huge.py").write_text(
            "a" * 300_000 + "NEVER_INCLUDED_PRIVATE_FILE_SUFFIX"
        )
        (self.repo / "small.py").write_text("small = True")
        context = read_repo_context(self.repo, list_files(self.repo))
        self.assertNotIn("huge.py", context)
        self.assertNotIn("NEVER_INCLUDED", context)
        self.assertIn("small = True", context)

    def test_provider_error_body_never_leaks_to_api(self):
        secret = "EXTERNAL_PROVIDER_RESPONSE_HAS_PRIVATE_DATA"
        client_class = httpx.AsyncClient
        transport = httpx.MockTransport(lambda _: httpx.Response(429, text=secret))

        async def request():
            return await ask_model("openai", "gpt-4o-mini", "system", "user")

        with patch("app.main.httpx.AsyncClient", side_effect=lambda **kw: client_class(
            **kw, transport=transport
        )), patch.dict(
            os.environ, {"OPENAI_API_KEY": "synthetic-test-key"}
        ):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(request())
        self.assertEqual(caught.exception.status_code, 502)
        self.assertNotIn(secret, caught.exception.detail)
        self.assertIn("429", caught.exception.detail)

    def test_safe_env_template_is_allowed(self):
        (self.repo / ".env.example").write_text("OPENAI_API_KEY=replace_me\n")
        self.assertIn(".env.example", list_files(self.repo))


if __name__ == "__main__":
    unittest.main()
