"""Security regressions for the fail-closed, resource-limited test runner."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.command_policy import parse_safe_command
from app.main import CommandRequest, execute
from app.sandbox import (
    MAX_OUTPUT_CHARS,
    build_container_command,
    docker_client_env,
    run_test_command,
    select_image,
)


class SandboxPolicyTests(unittest.TestCase):
    def test_no_docker_refuses_to_execute_untrusted_repository_code(self):
        with tempfile.TemporaryDirectory() as temp, patch("app.sandbox.shutil.which", return_value=None), patch(
            "app.sandbox.subprocess.Popen"
        ) as start:
            code, message = run_test_command(["python", "-m", "unittest"], Path(temp))
        self.assertEqual(code, 125)
        self.assertIn("Docker isolation is required", message)
        start.assert_not_called()

    def test_docker_policy_has_no_network_secrets_or_host_home_mount(self):
        with tempfile.TemporaryDirectory() as temp:
            command = build_container_command(
                ["python", "-m", "unittest"], Path(temp),
                name="patchpilot-" + "c" * 24,
            )
        as_text = " ".join(command)
        self.assertEqual(command[:2], ["docker", "run"])
        self.assertIn("--network none", as_text)
        self.assertIn("--cap-drop ALL", as_text)
        self.assertIn("--security-opt no-new-privileges", as_text)
        self.assertIn("--pids-limit 64", as_text)
        self.assertIn("--memory 512m", as_text)
        self.assertIn("--cpus 1.0", as_text)
        self.assertIn("--pull=never", as_text)
        self.assertIn("--read-only", command)
        self.assertIn("--user 65534:65534", as_text)
        self.assertIn("target=/workspace,readonly", as_text)
        self.assertNotIn("/var/run/docker.sock", as_text)
        self.assertNotIn("HOME=/Users/", as_text)

    def test_only_administrator_can_choose_container_image(self):
        self.assertEqual(select_image(["python", "-m", "unittest"]), "python:3.12-slim-bookworm")
        with patch.dict(os.environ, {"PATCHPILOT_IMAGE_PYTHON": "company/custom-python@sha256:" + "f" * 64}):
            self.assertIn("company/custom-python", select_image(["python", "-m", "unittest"]))
        for unsafe in ["../../host", "image name", "x,network=host", ""]:
            with self.subTest(value=unsafe), patch.dict(
                os.environ, {"PATCHPILOT_IMAGE_PYTHON": unsafe}
            ), self.assertRaises(ValueError):
                select_image(["python", "-m", "unittest"])

    def test_bad_or_symlinked_workspace_cannot_be_mounted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            actual = root / "actual"
            actual.mkdir()
            link = root / "linked"
            link.symlink_to(actual)
            with self.assertRaises(ValueError):
                build_container_command(["python", "-m", "unittest"], link)
            comma = root / "with,comma"
            comma.mkdir()
            with self.assertRaises(ValueError):
                build_container_command(["python", "-m", "unittest"], comma)
            with self.assertRaises(ValueError):
                build_container_command(["bash", "-c", "anything"], actual)
            with self.assertRaises(ValueError):
                build_container_command(["python", "\x00"], actual)

    def test_sensitive_environment_is_not_forwarded_to_docker_client(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "private-key", "GITHUB_TOKEN": "private-github",
            "AWS_SECRET_ACCESS_KEY": "private-aws",
            "DOCKER_HOST": "tcp://evil:2375",
            "DOCKER_CONFIG": "/private/config",
            "PATH": "/usr/bin",
        }):
            forwarded = docker_client_env()
            self.assertEqual(forwarded["PATH"], "/usr/bin")
            for key in ("OPENAI_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "DOCKER_HOST", "DOCKER_CONFIG"):
                self.assertNotIn(key, forwarded)

    def test_known_runner_command_policy_still_rejects_shell_injection(self):
        for unsafe in ("python -c 'print(1)'", "npm run build && curl evil", "bash -c whoami", "python -m os"):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                parse_safe_command(unsafe)

    def test_output_is_bounded_and_client_cleanup_runs(self):
        with tempfile.TemporaryDirectory() as temp, patch("app.sandbox.shutil.which", return_value="/fake/docker"), patch(
            "app.sandbox.build_container_command",
            return_value=[sys.executable, "-c", "print('x' * 65000)"],
        ), patch("app.sandbox.subprocess.run") as cleanup:
            code, output = run_test_command(["python", "-m", "unittest"], Path(temp), timeout=10)
        self.assertEqual(code, 0)
        self.assertLessEqual(len(output), MAX_OUTPUT_CHARS)
        self.assertTrue(output.rstrip("\n").endswith("x" * 100))
        self.assertEqual(cleanup.call_count, 1)
        self.assertEqual(cleanup.call_args.args[0][0:3], ["docker", "rm", "--force"])

    def test_timeout_attempts_force_removal(self):
        with tempfile.TemporaryDirectory() as temp, patch("app.sandbox.shutil.which", return_value="/fake/docker"), patch(
            "app.sandbox.build_container_command",
            return_value=[sys.executable, "-c", "import time;time.sleep(8)"],
        ), patch("app.sandbox.subprocess.run") as cleanup:
            code, output = run_test_command(["python", "-m", "unittest"], Path(temp), timeout=1)
        self.assertEqual(code, 124)
        self.assertIn("timed out", output)
        self.assertEqual(cleanup.call_count, 1)

    def test_execute_endpoint_uses_container_not_local_run(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.main.workspace_repo", return_value=Path(temp)
        ), patch(
            "app.main.run_test_command", return_value=(125, "Docker unavailable")
        ) as isolated, patch("app.main.run") as host_run:
            result = asyncio.run(
                execute(CommandRequest(workspace_id="safe-id", command="python -m unittest"))
            )
            self.assertEqual(result["returncode"], 125)
            self.assertEqual(result["execution_backend"], "docker")
            isolated.assert_called_once()
            host_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
