"""Real-Git regression: refusing sensitive tracked and newly staged content."""
from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import HTTPException
from unittest.mock import patch

from app.main import app, commit_workspace


class CommitSecretProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("original\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run([
            "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
            "commit", "-qm", "initial",
        ], cwd=self.repo, check=True)
        self.base = self.head()

    def head(self):
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()

    def changed(self):
        return subprocess.check_output(["git", "status", "--porcelain"], cwd=self.repo, text=True)

    def attempt_commit(self):
        return commit_workspace(
            self.repo, "fix: reviewed code change", "Test Contributor", "test@example.test"
        )

    def test_real_http_commit_refuses_sensitive_file_without_echoing_it(self):
        secret_fixture = "ghp_" + "B" * 37
        (self.repo / "src.py").write_text(
            "API_KEY = '" + secret_fixture + "'\n", encoding="utf-8"
        )

        async def request():
            transport = httpx.ASGITransport(
                app=app, client=("127.0.0.1", 54321)
            )
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                return await client.post("/api/commit", json={
                    "workspace_id": "safe-workspace-id",
                    "message": "unsafe commit",
                    "author_name": "Tester",
                    "author_email": "test@example.test",
                })

        with patch("app.main.workspace_repo", return_value=self.repo), patch.dict(
            "os.environ", {"PATCHPILOT_API_TOKEN": ""}
        ):
            response = asyncio.run(request())
        self.assertEqual(response.status_code, 409)
        self.assertIn("Commit refused", response.json()["detail"])
        self.assertNotIn(secret_fixture, response.text)
        self.assertEqual(self.head(), self.base)
        self.assertTrue((self.repo / "src.py").exists())

    def test_new_unignored_env_file_never_enters_commit(self):
        (self.repo / ".env").write_text("OPENAI_API_KEY=fixture-only-secret\n", encoding="utf-8")
        (self.repo / "README.md").write_text("reviewed change\n", encoding="utf-8")
        with self.assertRaises(HTTPException) as caught:
            self.attempt_commit()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.head(), self.base)
        self.assertTrue((self.repo / ".env").exists())
        self.assertIn("README.md", self.changed())

    def test_added_known_credential_signature_in_normal_file_is_blocked(self):
        (self.repo / "settings.py").write_text(
            'GITHUB_TOKEN = "ghp_' + "A" * 36 + '"\n', encoding="utf-8"
        )
        with self.assertRaises(HTTPException) as caught:
            self.attempt_commit()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.head(), self.base)
        self.assertNotIn("ghp_", str(caught.exception.detail))

    def test_sensitive_file_variants_are_refused_without_deleting_file(self):
        for path in (
            ".env.local", ".env.production", "config/secrets.json",
            "auth/accesser.json", "keys/private.pem",
            "config/.npmrc", "home/.ssh/id_ed25519",
        ):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as folder:
                parent = Path(folder) / "repo"
                parent.mkdir()
                subprocess.run(["git", "init", "-q", "-b", "main"], cwd=parent, check=True)
                (parent / "README.md").write_text("original\n")
                subprocess.run(["git", "add", "README.md"], cwd=parent, check=True)
                subprocess.run([
                    "git", "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.test",
                    "commit", "-qm", "initial",
                ], cwd=parent, check=True)
                location = parent / path
                location.parent.mkdir(parents=True, exist_ok=True)
                location.write_text("fake fixture material")
                base = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=parent, text=True
                ).strip()
                with self.assertRaises(HTTPException) as caught:
                    commit_workspace(parent, "do not commit secret", "Tester", "test@example.test")
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(
                    subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=parent, text=True
                    ).strip(), base,
                )
                self.assertTrue(location.is_file())

    def test_example_env_without_secrets_can_be_committed(self):
        (self.repo / ".env.example").write_text(
            "OPENAI_API_KEY=replace_with_your_own_key\n",
            encoding="utf-8",
        )
        result = self.attempt_commit()
        self.assertNotEqual(result["commit_sha"], self.base)

    def test_private_key_header_is_refused_without_echoing_payload(self):
        (self.repo / "config.py").write_text(
            'SECRET = "-----BEGIN PRIVATE KEY-----\n' + "TEST_FIXTURE_ONLY" + '\n"\n',
            encoding="utf-8",
        )
        with self.assertRaises(HTTPException) as caught:
            self.attempt_commit()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertNotIn("PRIVATE KEY", caught.exception.detail)
        self.assertEqual(self.head(), self.base)

    def test_reviewed_nonsecret_changes_still_commit(self):
        (self.repo / "README.md").write_text("valid update\n", encoding="utf-8")
        result = self.attempt_commit()
        self.assertEqual(result["message"], "fix: reviewed code change")
        self.assertNotEqual(self.head(), self.base)
        self.assertEqual(result["commit_sha"], self.head())


if __name__ == "__main__":
    unittest.main()