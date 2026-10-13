"""Live Docker isolation smoke test. Run in CI on an Ubuntu Docker runner."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.sandbox import run_test_command


class DockerIsolationSmoke(unittest.TestCase):
    def test_real_container_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            (workspace / "tests").mkdir(parents=True)
            fixture = Path(__file__).parent / "sandbox_fixture" / "test_isolation.py"
            (workspace / "tests" / "test_isolation.py").write_bytes(fixture.read_bytes())
            # A real host secret sibling. A container with only the workspace
            # mounted must not be able to read it.
            (Path(root) / "patchpilot-host-secret.txt").write_text("DO_NOT_EXPOSE")
            # Folder readable by nobody in the runner.
            workspace.chmod(0o755)
            (workspace / "tests").chmod(0o755)
            code, output = run_test_command(
                ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
                workspace, timeout=45,
            )
            self.assertEqual(code, 0, output)
            self.assertIn("Ran 4 tests", output)
            self.assertNotIn("DO_NOT_EXPOSE", output)


if __name__ == "__main__":
    unittest.main()
