from __future__ import annotations

import re
import shlex

SAFE_DIRECT_EXECUTABLES = {
    "pytest",
    "ruff",
    "mypy",
    "pyright",
}
SAFE_PACKAGE_SCRIPTS = {"test", "lint", "typecheck", "check"}
SHELL_META = re.compile(r"[;&|`$<>\n\r]")


def _validate_package_manager(argv: list[str]) -> None:
    executable = argv[0]
    if len(argv) < 2:
        raise ValueError(f"{executable} requires an approved script command")

    command = argv[1]
    if command in SAFE_PACKAGE_SCRIPTS:
        return
    if command == "run" and len(argv) >= 3 and argv[2] in SAFE_PACKAGE_SCRIPTS:
        return

    raise ValueError(
        f"{executable} commands are limited to test/lint/typecheck/check scripts"
    )


def parse_safe_command(command: str) -> list[str]:
    """Parse a single test/tool command without invoking a shell.

    This blocks shell composition and obvious command-runner escape hatches. It
    is not a sandbox: approved test and lint tools can still execute repository
    code or project-defined scripts.
    """
    value = command.strip()
    if not value:
        raise ValueError("command is empty")
    if SHELL_META.search(value):
        raise ValueError("shell operators and redirection are not allowed")

    try:
        argv = shlex.split(value, posix=True)
    except ValueError as exc:
        raise ValueError(f"invalid command syntax: {exc}") from exc

    if not argv:
        raise ValueError("command is empty")

    executable = argv[0]
    if "/" in executable or "\\" in executable:
        raise ValueError("path-qualified executables are not allowed")

    if executable in SAFE_DIRECT_EXECUTABLES:
        return argv

    if executable in {"python", "python3"}:
        if len(argv) < 3 or argv[1] != "-m" or argv[2] not in {"pytest", "unittest"}:
            raise ValueError("python commands are limited to -m pytest or -m unittest")
        return argv

    if executable in {"npm", "pnpm", "yarn"}:
        _validate_package_manager(argv)
        return argv

    if executable == "go":
        if len(argv) < 2 or argv[1] != "test":
            raise ValueError("go commands are limited to go test")
        return argv

    if executable == "cargo":
        if len(argv) < 2 or argv[1] not in {"test", "check", "clippy"}:
            raise ValueError("cargo commands are limited to test/check/clippy")
        return argv

    raise ValueError(f"executable is not allowed: {executable}")
