import unittest

from app.command_policy import parse_safe_command


class CommandPolicyTests(unittest.TestCase):
    def test_allows_plain_pytest_command(self) -> None:
        self.assertEqual(
            parse_safe_command("pytest -q tests/test_api.py"),
            ["pytest", "-q", "tests/test_api.py"],
        )

    def test_allows_python_module_test_runner(self) -> None:
        self.assertEqual(
            parse_safe_command("python -m unittest discover -s tests"),
            ["python", "-m", "unittest", "discover", "-s", "tests"],
        )

    def test_rejects_shell_chaining(self) -> None:
        with self.assertRaisesRegex(ValueError, "shell operators"):
            parse_safe_command("pytest -q; rm -rf .git")

    def test_rejects_command_substitution(self) -> None:
        with self.assertRaisesRegex(ValueError, "shell operators"):
            parse_safe_command("pytest $(cat /etc/passwd)")

    def test_rejects_unlisted_executable(self) -> None:
        with self.assertRaisesRegex(ValueError, "not allowed"):
            parse_safe_command("git status")

    def test_rejects_absolute_path_to_allowlisted_executable(self) -> None:
        with self.assertRaisesRegex(ValueError, "path-qualified"):
            parse_safe_command("/tmp/pytest -q")

    def test_rejects_relative_path_to_allowlisted_executable(self) -> None:
        with self.assertRaisesRegex(ValueError, "path-qualified"):
            parse_safe_command("./pytest -q")

    def test_rejects_windows_path_to_allowlisted_executable(self) -> None:
        with self.assertRaisesRegex(ValueError, "path-qualified"):
            parse_safe_command(r"tools\\pytest -q")

    def test_restricts_direct_python_scripts(self) -> None:
        with self.assertRaisesRegex(ValueError, "limited"):
            parse_safe_command("python dangerous.py")


if __name__ == "__main__":
    unittest.main()
