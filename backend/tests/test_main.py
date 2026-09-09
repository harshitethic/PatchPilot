import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.main import (
    RunRequest,
    apply_edits,
    github_headers,
    health,
    parse_json_object,
    safe_branch_name,
    safe_repo_name,
    validate_repo_url,
)


class UtilitySafetyTests(unittest.TestCase):
    def test_safe_repo_name_normalizes_git_url(self) -> None:
        self.assertEqual(
            safe_repo_name("https://github.com/example/project-name.git"),
            "project-name",
        )

    def test_safe_branch_name_normalizes_unsafe_input(self) -> None:
        self.assertEqual(
            safe_branch_name("  feat//unsafe branch  "),
            "feat/unsafe-branch",
        )

    def test_validate_repo_url_accepts_public_https_repository(self) -> None:
        self.assertEqual(
            validate_repo_url(" https://github.com/example/project.git "),
            "https://github.com/example/project.git",
        )

    def test_validate_repo_url_rejects_non_https_transports(self) -> None:
        for repo_url in (
            "/tmp/local-repo",
            "file:///tmp/local-repo",
            "ssh://git@example.com/project.git",
            "git@example.com:project.git",
            "http://github.com/example/project.git",
        ):
            with self.subTest(repo_url=repo_url):
                with self.assertRaises(HTTPException) as context:
                    validate_repo_url(repo_url)
                self.assertEqual(context.exception.status_code, 400)

    def test_validate_repo_url_rejects_embedded_credentials(self) -> None:
        with self.assertRaises(HTTPException) as context:
            validate_repo_url("https://token@example.com/project.git")
        self.assertEqual(context.exception.status_code, 400)

    def test_validate_repo_url_rejects_local_network_literals(self) -> None:
        for repo_url in (
            "https://localhost/project.git",
            "https://127.0.0.1/project.git",
            "https://10.0.0.5/project.git",
            "https://[::1]/project.git",
        ):
            with self.subTest(repo_url=repo_url):
                with self.assertRaises(HTTPException) as context:
                    validate_repo_url(repo_url)
                self.assertEqual(context.exception.status_code, 400)

    def test_validate_repo_url_rejects_query_and_fragment(self) -> None:
        for repo_url in (
            "https://github.com/example/project.git?token=secret",
            "https://github.com/example/project.git#branch",
        ):
            with self.subTest(repo_url=repo_url):
                with self.assertRaises(HTTPException) as context:
                    validate_repo_url(repo_url)
                self.assertEqual(context.exception.status_code, 400)

    def test_parse_json_object_accepts_fenced_json(self) -> None:
        payload = parse_json_object('```json\n{"summary":"ok","plan":[]}\n```')
        self.assertEqual(payload, {"summary": "ok", "plan": []})

    def test_apply_edits_updates_exact_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            target = repo / "src" / "example.txt"
            target.parent.mkdir(parents=True)
            target.write_text("before\n", encoding="utf-8")

            ok, message = apply_edits(
                repo,
                {
                    "edits": [
                        {
                            "path": "src/example.txt",
                            "old": "before",
                            "new": "after",
                        }
                    ]
                },
            )

            self.assertTrue(ok, message)
            self.assertEqual(target.read_text(encoding="utf-8"), "after\n")

    def test_apply_edits_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            ok, message = apply_edits(
                repo,
                {
                    "edits": [
                        {
                            "path": "../escape.txt",
                            "old": "before",
                            "new": "after",
                        }
                    ]
                },
            )

            self.assertFalse(ok)
            self.assertIn("escapes repository", message)

    def test_apply_edits_is_atomic_when_later_edit_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            first = repo / "first.txt"
            second = repo / "second.txt"
            first.write_text("alpha", encoding="utf-8")
            second.write_text("beta", encoding="utf-8")

            ok, _ = apply_edits(
                repo,
                {
                    "edits": [
                        {"path": "first.txt", "old": "alpha", "new": "changed"},
                        {"path": "second.txt", "old": "missing", "new": "changed"},
                    ]
                },
            )

            self.assertFalse(ok)
            self.assertEqual(first.read_text(encoding="utf-8"), "alpha")
            self.assertEqual(second.read_text(encoding="utf-8"), "beta")

    def test_github_headers_requires_token(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as context:
                github_headers()

        self.assertEqual(context.exception.status_code, 400)

    def test_run_request_rejects_invalid_iteration_count(self) -> None:
        with self.assertRaises(ValidationError):
            RunRequest(
                repo_url="https://example.test/repo.git",
                task="fix",
                max_iterations=0,
            )

    def test_health_contract(self) -> None:
        self.assertEqual(
            asyncio.run(health()),
            {"status": "ok", "service": "PatchPilot", "version": "0.4.0"},
        )


if __name__ == "__main__":
    unittest.main()
