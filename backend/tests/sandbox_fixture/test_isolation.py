"""Executed INSIDE Docker on GitHub's Linux runner, not on the host."""
import os
import socket
import unittest
from pathlib import Path


class IsolationSmoke(unittest.TestCase):
    def test_host_secret_outside_workspace_is_not_mounted(self):
        self.assertFalse(Path("/patchpilot-host-secret.txt").exists())
        self.assertFalse(Path("/host-home/.ssh/id_rsa").exists())
        self.assertFalse(Path("/var/run/docker.sock").exists())

    def test_container_has_no_external_network(self):
        with self.assertRaises(OSError):
            socket.create_connection(("1.1.1.1", 80), timeout=1)

    def test_workspace_is_read_only_and_tmp_is_writable(self):
        with self.assertRaises(OSError):
            Path("/workspace/.sandbox-write-probe").write_text("not allowed")
        file = Path("/tmp/sandbox-fixture-probe")
        file.write_text("allowed")
        self.assertEqual(file.read_text(), "allowed")
        file.unlink()

    def test_non_root_and_no_sensitive_environment(self):
        self.assertNotEqual(os.geteuid(), 0)
        for key in ("OPENAI_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY"):
            self.assertNotIn(key, os.environ)


if __name__ == "__main__":
    unittest.main()
