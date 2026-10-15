"""Repair prompts must honor the same disclosure boundary as initial context."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.main import app


class RepairContextPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, *, output="AssertionError: expected 43", private_path=None, private_value=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repair-fixture" / "repo"
            repo.mkdir(parents=True)
            (repo / "service.py").write_text("answer = 41\n")
            if private_path:
                (repo / private_path).write_text(f"PRIVATE = '{private_value}'\nMODE = 'old'\n")

            def git(*args):
                return subprocess.run(["git", *args], cwd=repo, check=True,
                                      capture_output=True, text=True).stdout

            git("init", "-b", "main")
            git("add", ".")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-m", "fixture")
            edits = [{"path": "service.py", "old": "41", "new": "42"}]
            if private_path:
                # A guessed edit can change a file excluded from source context.
                # Its raw Git diff must not reintroduce adjacent private lines.
                edits.append({"path": private_path, "old": "MODE = 'old'", "new": "MODE = 'new'"})
            replies = [
                {"summary": "Fix answer", "plan": [], "touched_files": ["service.py"],
                 "test_command": "python -m unittest"},
                {"edits": edits},
                {"edits": [{"path": "service.py", "old": "42", "new": "43"}]},
            ]
            prompts = []

            async def model(provider, model, system, user):
                prompts.append(user)
                return json.dumps(replies[len(prompts) - 1])

            with patch.dict(os.environ, {}, clear=True), patch("app.main.WORKSPACES", root), patch(
                "app.main.clone_repo", return_value=("repair-fixture", repo)
            ), patch("app.main.ask_model", side_effect=model), patch(
                "app.main.run_test_command", side_effect=[(1, output), (0, "Passed")]
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
                    base_url="http://localhost",
                ) as client:
                    response = await client.post("/api/run", json={
                        "repo_url": "https://github.com/example/fixture", "task": "Set answer to 43",
                        "max_iterations": 2, "branch_name": "fix/answer",
                    })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["tests_passed"])
            self.assertEqual(len(prompts), 3)
            self.assertEqual((repo / "service.py").read_text(), "answer = 43\n")
            self.assertIn(output[-12000:], (repo.parent / "test-output.log").read_text())
            self.assertEqual((repo.parent / "test-output.log").stat().st_mode & 0o777, 0o600)
            return prompts

    async def test_recognizable_credentials_in_test_output_are_not_sent_to_repair_model(self):
        secret = "ghp_" + "Q" * 36
        prompts = await self.exercise(output=f"Failed with token {secret}")
        self.assertNotIn(secret, "\n".join(prompts))

    async def test_secret_detection_runs_before_diagnostic_tail_truncation(self):
        secret = "sk-proj-" + "Z" * 40
        prompts = await self.exercise(output=secret + "x" * 11980)
        self.assertNotIn("Z" * 20, prompts[2])

    async def test_raw_diff_cannot_reintroduce_excluded_sensitive_file(self):
        secret = "PRIVATE_ENV_VALUE_NOT_A_KNOWN_TOKEN_PATTERN"
        prompts = await self.exercise(private_path=".env.production", private_value=secret)
        self.assertNotIn(secret, "\n".join(prompts))

    async def test_raw_diff_cannot_reintroduce_token_from_omitted_source_file(self):
        secret = "ghp_" + "S" * 36
        prompts = await self.exercise(private_path="unsafe.py", private_value=secret)
        self.assertNotIn(secret, "\n".join(prompts))

    async def test_safe_diagnostics_and_filtered_current_source_still_support_repairs(self):
        prompts = await self.exercise()
        self.assertIn("AssertionError: expected 43", prompts[2])
        self.assertIn("answer = 42", prompts[2])
