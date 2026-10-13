import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from app.main import (apply_edits, clone_repo, list_files, read_repo_context,
                      validate_repository_url, workspace_repo)


class RepositoryBoundaryTests(unittest.TestCase):
    def test_accepts_public_github_https_url(self):
        self.assertEqual(validate_repository_url("https://github.com/pallets/flask"),
                         "https://github.com/pallets/flask.git")
        self.assertEqual(validate_repository_url(" https://github.com/pallets/flask.git/ "),
                         "https://github.com/pallets/flask.git")

    def test_rejects_non_github_network_file_and_git_urls(self):
        urls = ["file:///etc/passwd", "ssh://github.com/owner/repo",
                "https://github.com.evil.tld/owner/repo",
                "https://github.com:444/owner/repo",
                "https://user:pass@github.com/owner/repo",
                "https://github.com/owner/repo/tree/main",
                "https://github.com/../repo",
                "https://github.com/owner/repo?q=secret",
                "https://github.com/owner/repo#fragment",
                "git@github.com:owner/repo.git"]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_repository_url(url)

    def test_bad_clone_url_rejected_before_running_git(self):
        with patch("app.main.run") as mock_run:
            with self.assertRaises(HTTPException) as exc:
                clone_repo("file:///etc/passwd")
            self.assertEqual(exc.exception.status_code, 400)
            mock_run.assert_not_called()

    def test_symlink_outside_repository_not_indexed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "README.md").write_text("normal documentation")
            secret = root / "secret.py"
            secret.write_text("PRIVATE_HOST_CONTENT")
            try:
                (repo / "innocent.py").symlink_to(secret)
            except (NotImplementedError, OSError):
                self.skipTest("Symlinks unavailable")
            self.assertEqual(list_files(repo), ["README.md"])
            self.assertNotIn("PRIVATE_HOST_CONTENT", read_repo_context(repo, ["innocent.py"]))

    def test_context_rejects_parent_traversal_and_nested_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (root / "private.txt").write_text("HOST_MARKER")
            outside = root / "outside"
            outside.mkdir()
            (outside / "hidden.py").write_text("HOST_MARKER")
            try:
                (repo / "linked").symlink_to(outside, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("Symlinks unavailable")
            data = read_repo_context(repo, ["../private.txt", "linked/hidden.py"])
            self.assertNotIn("HOST_MARKER", data)

    def test_sequential_edits_to_same_file_preserve_both_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            target = repo / "demo.py"
            target.write_text("alpha beta gamma")
            edits = {"edits": [
                {"path": "demo.py", "old": "alpha", "new": "one"},
                {"path": "demo.py", "old": "gamma", "new": "three"},
            ]}
            ok, result = apply_edits(repo, edits)
            self.assertTrue(ok, result)
            self.assertEqual(result, "Applied 2 edit(s)")
            self.assertEqual(target.read_text(), "one beta three")

    def test_later_invalid_same_file_edit_leaves_original_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            target = repo / "demo.py"
            target.write_text("alpha beta")
            edits = {"edits": [
                {"path": "demo.py", "old": "alpha", "new": "one"},
                {"path": "demo.py", "old": "alpha", "new": "two"},
            ]}
            ok, reason = apply_edits(repo, edits)
            self.assertFalse(ok)
            self.assertIn("exactly one match", reason)
            self.assertEqual(target.read_text(), "alpha beta")

    def test_workspace_id_rejects_dot_segments(self):
        with self.assertRaises(HTTPException) as exc:
            workspace_repo("valid/../bad")
        self.assertEqual(exc.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
