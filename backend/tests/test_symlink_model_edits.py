"""Regression tests for model-generated edits through repository symlinks."""
import tempfile
import unittest
from pathlib import Path

from app.main import apply_edits


class SymlinkEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)

    def test_symlink_to_git_config_cannot_bypass_control_path_guard(self):
        git = self.repo / ".git"
        git.mkdir()
        config = git / "config"
        config.write_text("protected", encoding="utf-8")
        (self.repo / "safe-looking.txt").symlink_to(".git/config")

        ok, message = apply_edits(self.repo, {
            "edits": [{"path": "safe-looking.txt", "old": "protected", "new": "compromised"}]
        })

        self.assertFalse(ok, message)
        self.assertIn("symlink", message)
        self.assertEqual(config.read_text(encoding="utf-8"), "protected")

    def test_symlinked_parent_directory_is_rejected(self):
        actual = self.repo / "real"
        actual.mkdir()
        target = actual / "source.py"
        target.write_text("safe", encoding="utf-8")
        (self.repo / "alias").symlink_to(actual, target_is_directory=True)

        ok, message = apply_edits(self.repo, {
            "edits": [{"path": "alias/source.py", "old": "safe", "new": "unsafe"}]
        })

        self.assertFalse(ok, message)
        self.assertIn("symlink", message)
        self.assertEqual(target.read_text(encoding="utf-8"), "safe")

    def test_in_repo_symlink_rejected_even_if_target_is_normal_source(self):
        target = self.repo / "source.py"
        target.write_text("safe", encoding="utf-8")
        (self.repo / "alias.py").symlink_to("source.py")

        ok, message = apply_edits(self.repo, {
            "edits": [{"path": "alias.py", "old": "safe", "new": "unsafe"}]
        })

        self.assertFalse(ok, message)
        self.assertEqual(target.read_text(encoding="utf-8"), "safe")

    def test_rejected_second_edit_preserves_previously_staged_content(self):
        source = self.repo / "source.py"
        source.write_text("before", encoding="utf-8")
        (self.repo / "alias.py").symlink_to("source.py")

        ok, message = apply_edits(self.repo, {"edits": [
            {"path": "source.py", "old": "before", "new": "after"},
            {"path": "alias.py", "old": "before", "new": "unsafe"},
        ]})

        self.assertFalse(ok, message)
        self.assertEqual(source.read_text(encoding="utf-8"), "before")

    def test_plain_file_edit_still_works(self):
        source = self.repo / "source.py"
        source.write_text("before", encoding="utf-8")
        ok, message = apply_edits(self.repo, {
            "edits": [{"path": "source.py", "old": "before", "new": "after"}]
        })
        self.assertTrue(ok, message)
        self.assertEqual(source.read_text(encoding="utf-8"), "after")


if __name__ == "__main__":
    unittest.main()
