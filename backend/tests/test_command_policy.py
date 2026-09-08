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

    def test_allows_approved_package_script(self) -> None:
        self.assertEqual(
            parse_safe_command("npm run lint"),
            ["npm", "run", "lint"],
        )
        self.assertEqual(
            parse_safe_command("pnpm test -- --runInBand"),
            ["pnpm", "test", "--", "--runInBand"],
        )

    def test_allows_go_and_cargo_validation_commands(self) -> None:
        self.assertEqual(parse_safe_command("go test ./..."), ["go", "test", "./..."])
        self.assertEqual(parse_safe_command("cargo clippy --all-targets"), ["cargo", "clippy", "--all-targets"])

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

    def test_rejects_direct_node_execution(self) -> None:
        with self.assertRaisesRegex(ValueError, "not allowed"):
            parse_safe_command("node -e 'console.log(1)'")

    def test_rejects_package_manager_exec_escape_hatches(self) -> None:
        for command in (
            "npm exec node -- -e console.log(1)",
            "pnpm exec node -e console.log(1)",
            "yarn exec node -e console.log(1)",
        ):
            with self.subTest(command=command):
                with self.assertRaisesRegex(ValueError, "limited"):
                    parse_safe_command(command)

    def test_rejects_go_run_and_cargo_run(self) -> None:
        with self.assertRaisesRegex(ValueError, "go test"):
            parse_safe_command("go run ./cmd/tool")
        with self.assertRaisesRegex(ValueError, "test/check/clippy"):
            parse_safe_command("cargo run --bin tool")


if __name__ == "__main__":
    unittest.main()
