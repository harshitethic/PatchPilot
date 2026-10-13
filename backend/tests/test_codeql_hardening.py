"""Regression checks for fixed URL hosts and secret-free test subprocesses."""
import asyncio
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app.main import (
    get_issue,
    github_issue_url,
    import_issue,
    IssueRequest,
    run,
    subprocess_environment,
)


class SecurityBoundaryTests(unittest.TestCase):
    def test_github_issue_url_host_never_depends_on_repo_input(self):
        url = github_issue_url("owner/repo-name", 42)
        self.assertEqual(url.scheme, "https")
        self.assertEqual(url.host, "api.github.com")
        self.assertEqual(url.path, "/repos/owner/repo-name/issues/42")

    def test_rejects_traversal_credentials_unicode_or_long_repos(self):
        inputs = (
            "../foo", "foo/..", "foo/repo/extra", "foo", "owner/repo?x=1",
            "owner/with space", "ábc/repo", "x" * 161 + "/repo",
            "evil@api.github.com/repo",
        )
        for repo in inputs:
            with self.subTest(repo=repo), self.assertRaises(HTTPException):
                github_issue_url(repo, 1)
        for number in (0, -1, 2**33):
            with self.assertRaises(HTTPException):
                github_issue_url("good/repo", number)

    def test_api_get_and_import_issue_are_fixed_host(self):
        requests = []

        def respond(request):
            requests.append(request.url)
            return httpx.Response(
                200, json={"number": 42, "title": "Fix parser", "body": "Details",
                            "user": {"login": "author"}, "labels": []},
            )

        async_client_class = httpx.AsyncClient
        with patch(
            "app.main.httpx.AsyncClient",
            side_effect=lambda **kwargs: async_client_class(
                transport=httpx.MockTransport(respond)
            ),
        ), patch(
            "app.main.github_headers", return_value={}
        ):
            fetched = asyncio.run(get_issue("owner/repo", 42))
            imported = asyncio.run(import_issue(IssueRequest(repo="owner/repo", issue_number=42)))
        self.assertEqual(fetched["title"], "Fix parser")
        self.assertIn("Fix parser", imported["task"])
        self.assertEqual([r.host for r in requests], ["api.github.com", "api.github.com"])
        self.assertEqual([r.path for r in requests], ["/repos/owner/repo/issues/42"] * 2)

    def test_test_commands_do_not_inherit_api_keys_or_auth_sockets(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "SECRET_TEST_KEY",
            "GITHUB_TOKEN": "SECRET_TEST_TOKEN",
            "AWS_SECRET_ACCESS_KEY": "SECRET_TEST_AWS",
            "SSH_AUTH_SOCK": "/private/key.sock",
            "PATH": "/usr/bin",
        }):
            clean = subprocess_environment()
            self.assertEqual(clean.get("PATH"), "/usr/bin")
            self.assertEqual(clean.get("CI"), "1")
            for name in ("OPENAI_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK"):
                self.assertNotIn(name, clean)
            with patch("app.main.subprocess.run") as called:
                called.return_value = subprocess.CompletedProcess(["pytest"], 0, "ok")
                rc, output = run(["pytest", "-q"], Path("."), 5)
                self.assertEqual((rc, output), (0, "ok"))
                self.assertNotIn("GITHUB_TOKEN", called.call_args.kwargs["env"])
                self.assertFalse(called.call_args.kwargs["shell"])

    def test_generic_run_rejects_unknown_executable(self):
        with patch("app.main.subprocess.run") as called:
            result = run(["bash", "-c", "echo unsafe"], Path("."))
            self.assertEqual(result[0], 126)
            called.assert_not_called()


if __name__ == "__main__":
    unittest.main()
