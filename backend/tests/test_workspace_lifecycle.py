"""Filesystem isolation, real Git status and explicit deletion checks."""
import asyncio
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.main import (
    WorkspaceCleanupRequest,
    cleanup_stale_workspaces,
    delete_workspace,
    list_workspace_summaries,
    workspace_dir,
    workspace_repo,
)


class WorkspaceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "workspaces"
        self.root.mkdir()
        self.patch = patch("app.main.WORKSPACES", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def create_repo(self, name, *, dirty=False):
        repo = self.root / name / "repo"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        (repo / "README.md").write_text("demo\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run([
            "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-qm", "initial"], cwd=repo, check=True)
        if dirty:
            (repo / "new.txt").write_text("uncommitted")
        return repo

    def test_inventory_lists_branch_and_changed_file_count(self):
        self.create_repo("clean-123")
        self.create_repo("dirty-123", dirty=True)
        self.assertEqual(
            {r["workspace_id"]: (r["branch"], r["changed_files"]) for r in list_workspace_summaries()},
            {"clean-123": ("main", 0), "dirty-123": ("main", 1)}
        )

    def test_invalid_id_and_symlink_are_never_followed(self):
        external = self.parent / "external"
        (external / "repo").mkdir(parents=True)
        try:
            (self.root / "link-123").symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks not supported")
        with self.assertRaises(HTTPException):
            workspace_dir("link-123")
        self.assertFalse(any(row["workspace_id"] == "link-123" for row in list_workspace_summaries()))
        with self.assertRaises(HTTPException):
            delete_workspace("link-123", force=True)
        self.assertTrue(external.exists())

    def test_repo_symlink_cannot_escape_workspace(self):
        external = self.parent / "external"
        external.mkdir()
        workdir = self.root / "safe-123"
        workdir.mkdir()
        try:
            (workdir / "repo").symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks not supported")
        with self.assertRaises(HTTPException):
            workspace_repo("safe-123")
        with self.assertRaises(HTTPException):
            delete_workspace("safe-123", force=True)
        self.assertTrue(external.exists())

    def test_delete_dirty_workspace_requires_explicit_force(self):
        repo = self.create_repo("dirty-123", dirty=True)
        with self.assertRaises(HTTPException) as exc:
            delete_workspace("dirty-123")
        self.assertEqual(exc.exception.status_code, 409)
        self.assertTrue(repo.exists())
        delete_workspace("dirty-123", force=True)
        self.assertFalse(repo.exists())

    def test_ignored_files_protect_workspace_from_bulk_cleanup(self):
        repo = self.create_repo("with-ignored")
        (repo / ".gitignore").write_text(".env\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
        subprocess.run([
            "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-qm", "ignore env"], cwd=repo, check=True)
        (repo / ".env").write_text("private local settings")
        now = datetime(2026, 10, 13, 8, tzinfo=timezone.utc)
        old = now.timestamp() - 48 * 3600
        os.utime(repo.parent, (old, old))
        report = cleanup_stale_workspaces(24, dry_run=False, now=now)
        self.assertIn("with-ignored", report["skipped_dirty"])
        self.assertNotIn("with-ignored", report["deleted"])
        self.assertTrue((repo / ".env").exists())

    def test_cleanup_is_dry_run_by_default_and_skips_dirty(self):
        old = self.create_repo("old-clean")
        dirty = self.create_repo("old-dirty", dirty=True)
        fresh = self.create_repo("new-clean")
        now = datetime(2026, 10, 13, 8, tzinfo=timezone.utc)
        for target in (old.parent, dirty.parent):
            timestamp = now.timestamp() - 48 * 3600
            os.utime(target, (timestamp, timestamp))
        stamp = now.timestamp() - 2 * 3600
        os.utime(fresh.parent, (stamp, stamp))
        report = cleanup_stale_workspaces(24, now=now)
        self.assertEqual(report["candidates"], ["old-clean"])
        self.assertEqual(report["deleted"], [])
        self.assertEqual(report["skipped_dirty"], ["old-dirty"])
        self.assertTrue(old.parent.exists())
        executed = cleanup_stale_workspaces(24, dry_run=False, now=now)
        self.assertEqual(executed["deleted"], ["old-clean"])
        self.assertFalse(old.parent.exists())
        self.assertTrue(dirty.parent.exists())
        self.assertTrue(fresh.parent.exists())

    def test_invalid_retention_hours_and_traversal(self):
        for invalid in [0, 721]:
            with self.assertRaises(ValidationError):
                WorkspaceCleanupRequest(max_age_hours=invalid)
            with self.assertRaises(ValueError):
                cleanup_stale_workspaces(invalid)
        with self.assertRaises(HTTPException):
            delete_workspace("../external", force=True)
        self.assertTrue(self.parent.exists())


if __name__ == "__main__":
    unittest.main()
