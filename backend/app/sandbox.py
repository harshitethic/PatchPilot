"""Fail-closed execution of repository test commands.

The backend itself is a trusted local process; repository test code is not.
Tests run only inside disposable Docker containers with strict resource limits.
This does not make Git cloning, Docker daemon access, or all kernel attack
surfaces completely safe; consult docs/security-boundaries.md.
"""
from __future__ import annotations

import os
import re
import secrets
import selectors
import shutil
import subprocess
import time
from pathlib import Path

# Trusted administrators may install private, prebuilt offline images by setting
# the relevant env var. Repository files and model output cannot set these.
DEFAULT_IMAGES = {
    "python": "python:3.12-slim-bookworm",
    "node": "node:22-bookworm-slim",
    "go": "golang:1.23-bookworm",
    "rust": "rust:1.87-bookworm",
}
IMAGE_ENV = {
    "python": "PATCHPILOT_IMAGE_PYTHON",
    "node": "PATCHPILOT_IMAGE_NODE",
    "go": "PATCHPILOT_IMAGE_GO",
    "rust": "PATCHPILOT_IMAGE_RUST",
}
IMAGE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,250}\Z")
EXECUTION_TO_PROFILE = {
    "pytest": "python", "python": "python", "python3": "python",
    "ruff": "python", "mypy": "python", "pyright": "node",
    "npm": "node", "pnpm": "node", "yarn": "node",
    "go": "go", "cargo": "rust",
}
MAX_OUTPUT_CHARS = 12000


def select_image(argv: list[str]) -> str:
    """Select an administrator-approved image, never one from repo contents."""
    if not argv or argv[0] not in EXECUTION_TO_PROFILE:
        raise ValueError("Unapproved test executable")
    profile = EXECUTION_TO_PROFILE[argv[0]]
    image = os.getenv(IMAGE_ENV[profile], DEFAULT_IMAGES[profile])
    if not IMAGE_PATTERN.fullmatch(image) or ".." in image:
        raise ValueError("Invalid configured test image")
    return image


def build_container_command(
    argv: list[str], repo: Path, *, name: str | None = None
) -> list[str]:
    """Create a test-only Docker invocation without a shell or host credentials."""
    if not argv or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv):
        raise ValueError("Invalid test command argument")
    image = select_image(argv)
    resolved = repo.resolve(strict=True)
    if not resolved.is_dir() or repo.is_symlink():
        raise ValueError("Repository is not a real directory")
    # Docker's --mount syntax is comma-delimited. Refuse paths it cannot quote
    # securely instead of allowing mount-option interpretation.
    if any(c in str(resolved) for c in (",", "\n", "\r", "=")):
        raise ValueError("Unsupported workspace path for a safe bind mount")
    container_name = name or "patchpilot-" + secrets.token_hex(12)
    if not re.fullmatch(r"patchpilot-[a-f0-9]{24}", container_name):
        raise ValueError("Invalid container name")
    return [
        "docker", "run", "--rm", "--init", "--pull=never",
        "--name", container_name,
        "--network", "none",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "64",
        "--memory", "512m", "--cpus", "1.0",
        "--read-only", "--user", "65534:65534",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "--workdir", "/workspace",
        "--mount", f"type=bind,source={resolved},target=/workspace,readonly",
        "--env", "HOME=/tmp",
        "--env", "CI=1",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--env", "PYTEST_ADDOPTS=-p no:cacheprovider",
        "--env", "NPM_CONFIG_CACHE=/tmp/npm-cache",
        image, *argv,
    ]


def docker_client_env() -> dict[str, str]:
    """Never pass model/API tokens or redirect to arbitrary Docker daemons."""
    allowed = ("PATH", "HOME", "USER", "TMPDIR", "LANG", "LC_ALL")
    return {name: os.environ[name] for name in allowed if name in os.environ}


def run_test_command(argv: list[str], repo: Path, timeout: int = 180) -> tuple[int, str]:
    """Stream and cap untrusted output; never fall back to host execution.

    Exit 125 = Docker unavailable, 124 = timeout, 126 = policy denial.
    """
    if not shutil.which("docker"):
        return 125, "Test skipped: Docker isolation is required and unavailable."
    name = "patchpilot-" + secrets.token_hex(12)
    try:
        cmd = build_container_command(argv, repo, name=name)
    except (OSError, ValueError) as exc:
        return 126, "Test blocked by container security policy: " + str(exc)

    process: subprocess.Popen[bytes] | None = None
    expired = False
    chunks = b""
    try:
        process = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, shell=False, env=docker_client_env(),
        )
        assert process.stdout is not None
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            while True:
                seconds = deadline - time.monotonic()
                if seconds <= 0:
                    expired = True
                    break
                events = ready.select(min(seconds, 0.5))
                if not events:
                    if process.poll() is not None:
                        # Wait briefly for any final buffered stdout or EOF.
                        continue
                    continue
                data = os.read(process.stdout.fileno(), 65536)
                if not data:
                    break
                chunks = (chunks + data)[-MAX_OUTPUT_CHARS:]
        if expired:
            process.kill()
        process.wait(timeout=5)
        if expired:
            return 124, "Container test timed out and was terminated."
        return process.returncode or 0, chunks.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return 125, "Docker test runner is unavailable; no host fallback was attempted."
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process is not None and process.stdout is not None:
            process.stdout.close()
        # Always clean by a high-entropy exact container name, including
        # timeout, client failures and successful completion.
        if process is not None:
            try:
                subprocess.run(
                    ["docker", "rm", "--force", name],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=15, check=False, shell=False,
                    env=docker_client_env(),
                )
            except (OSError, subprocess.SubprocessError):
                pass
