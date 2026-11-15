"""Generated patch branches must obey Git's ref format, not only a character allowlist."""
import subprocess
import unittest

from app.main import safe_branch_name


class SafeBranchNameTests(unittest.TestCase):
    def test_common_branch_names_keep_expected_shape(self):
        self.assertEqual(safe_branch_name("  feat//unsafe branch  "), "feat/unsafe-branch")
        self.assertEqual(safe_branch_name("@@@"), "patchpilot/task")
        self.assertEqual(safe_branch_name("fix/normalize"), "fix/normalize")

    def test_generated_refs_are_accepted_by_git(self):
        bad_inputs = [
            "release.lock", "feature/a..b", "feat/.hidden", "fix/foo.lock",
            "nested/another.lock", "double...dots", "test/..", "abc/.lock",
            "fix/after@{abc}", ".dot/starts", "ends/.hidden",
        ]
        for value in bad_inputs:
            with self.subTest(value=value):
                branch = safe_branch_name(value)
                self.assertLessEqual(len(branch), 80)
                result = subprocess.run(
                    ["git", "check-ref-format", "--branch", branch],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, f"{value!r} -> {branch!r}: {result.stderr}")

    def test_truncation_cannot_leave_invalid_git_ref_suffix(self):
        name = "x" * 76 + ".lock" + "more"
        branch = safe_branch_name(name)
        self.assertEqual(subprocess.run(
            ["git", "check-ref-format", "--branch", branch], check=False,
            capture_output=True,
        ).returncode, 0)


if __name__ == "__main__":
    unittest.main()
