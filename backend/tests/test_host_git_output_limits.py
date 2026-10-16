"""Real Git runner regressions: capped output, timeout, descendant cleanup."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import run


@unittest.skipUnless(os.name == "posix", "POSIX supervised Git required")
class HostGitRunnerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_large_real_git_diff_rejected_not_silently_truncated(self):
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, check=True)
        tracked = self.root / "source.txt"
        tracked.write_text("original\n")
        subprocess.run(["git", "add", "source.txt"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
             "commit", "-m", "Initial"],
            cwd=self.root, stdout=subprocess.DEVNULL, check=True,
        )
        tracked.write_text("X" * (5 * 1024 * 1024) + "\n")
        code, output = run(["git", "diff", "--no-ext-diff", "--binary"], self.root, 15)
        self.assertEqual(code, 122)
        self.assertIn("output exceeded", output.lower())
        self.assertLess(len(output), 200)

    def test_timeout_kills_fake_git_descendant_before_delayed_write(self):
        fake_dir = self.root / "bin"
        fake_dir.mkdir()
        marker = self.root / "orphan.txt"
        pid_file = self.root / "descendant.pid"
        child = (
            "import time,pathlib;time.sleep(3);"
            f"pathlib.Path({str(marker)!r}).write_text('ORPHAN')"
        )
        fake_git = fake_dir / "git"
        fake_git.write_text(
            f"#!{sys.executable}\n"
            "import subprocess,sys,time,pathlib\n"
            f"p=subprocess.Popen([sys.executable,'-c',{child!r}])\n"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid))\n"
            "time.sleep(30)\n"
        )
        fake_git.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(fake_dir) + os.pathsep + os.environ["PATH"]}):
            code, output = run(["git", "status", "--porcelain"], self.root, 1)
        self.assertEqual(code, 124)
        self.assertTrue(pid_file.exists(), "The fixture must actually spawn a descendant")
        self.assertNotIn("ORPHAN", output)
        time.sleep(3.3)
        self.assertFalse(marker.exists(), "Git timeout left an unsupervised descendant")

    def test_large_diff_preview_reports_omission_instead_of_appearing_empty(self):
        from app.main import build_patch
        with patch("app.main.run", return_value=(122, "Git output exceeded safe limit.")):
            warning = build_patch(self.root)
        self.assertIn("Diff omitted", warning)
        self.assertIn("local workspace", warning)

    def test_small_git_output_remains_complete(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        code, output = run(["git", "rev-parse", "--is-inside-work-tree"], self.root, 15)
        self.assertEqual(code, 0, output)
        self.assertEqual(output.strip(), "true")


if __name__ == "__main__":
    unittest.main()
