from __future__ import annotations

import re
import shlex

SAFE_EXECUTABLES = {
    "pytest",
    "ruff",
    "mypy",
    "pyright",
    "npm",
    "pnpm",
    "yarn",
    "node",
    "python",
    "python3",
    "go",
    "cargo",
}

SHELL_META = re.compile(r"[;&|`$<>\n\r]")


def parse_safe_command(command: str) -> list[str]:
    """Parse a single test/tool command without invoking a shell.

    This blocks shell composition and limits the executable surface. It is not a
    sandbox: repository test tools can still execute repository code.
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
    executable = argv[0].rsplit("/", 1)[-1]
    if executable not in SAFE_EXECUTABLES:
        raise ValueError(f"executable is not allowed: {executable}")

    if executable in {"python", "python3"}:
        if len(argv) < 3 or argv[1] != "-m" or argv[2] not in {"pytest", "unittest"}:
            raise ValueError("python commands are limited to -m pytest or -m unittest")

    return argv
