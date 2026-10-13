"""Adversarial Git config / hooks and model-edit trust boundary regressions."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import apply_edits, run


class GitControlBoundaryTests(unittest.TestCase):
    def test_model_cannot_edit_git_control_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            control = root / ".git"
            control.mkdir()
            config = control / "config"
            config.write_text("before", encoding="utf-8")
            for path in (".git/config", ".git/../.git/config"):
                with self.subTest(path=path):
                    allowed, reason = apply_edits(root, {
                        "edits": [{"path": path, "old": "before", "new": "modified"}]
                    })
                    self.assertFalse(allowed)
                    self.assertIn("Git control", reason)
                    self.assertEqual(config.read_text(), "before")

    @unittest.skipIf(os.name == "nt", "POSIX executable Git hook fixture")
    def test_host_git_commands_do_not_run_repository_configured_hooks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(
                ["git", "init", "-b", "main"], cwd=root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
            )
            hook = root / ".git" / "hooks" / "pre-commit"
            marker = root / "should-not-exist.marker"
            hook.write_text("#!/bin/sh\nprintf UNTRUSTED > '" + str(marker) + "'\n")
            hook.chmod(0o755)
            subprocess.run(
                ["git", "config", "core.hooksPath", ".git/hooks"],
                cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
            )
            user_config = root / "malicious-global.gitconfig"
            user_config.write_text('[core]\n\thooksPath = .git/hooks\n', encoding="utf-8")
            (root / "source.txt").write_text("safe change", encoding="utf-8")
            with patch.dict(os.environ, {
                "GIT_CONFIG_GLOBAL": str(user_config),
                "GIT_CONFIG_NOSYSTEM": "0",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.hooksPath",
                "GIT_CONFIG_VALUE_0": ".git/hooks",
            }):
                add_code, add_log = run(["git", "add", "--all"], root, 15)
                self.assertEqual(add_code, 0, add_log)
                code, log = run([
                    "git", "-c", "user.name=Sandbox Tester", "-c",
                    "user.email=git-test@example.test", "commit", "-m", "safe test commit"
                ], root, 15)
                self.assertEqual(code, 0, log)
            self.assertFalse(marker.exists(), "Untrusted repository hook was executed")
            log = subprocess.run(
                ["git", "show", "-s", "--format=%s", "HEAD"],
                cwd=root, capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(log, "safe test commit")


if __name__ == "__main__":
    unittest.main()
