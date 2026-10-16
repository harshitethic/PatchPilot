"""Bounded Git subprocess output and POSIX process-group cleanup.

A trusted local backend may invoke Git to inspect untrusted repositories.
Never use subprocess.run(capture_output=True): a repository can induce
unbounded status/diff output or descendants that outlive a timed-out parent.
"""
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path

MAX_GIT_OUTPUT_BYTES = 4 * 1024 * 1024
READ_BYTES = 64 * 1024
OUTPUT_EXCEEDED = 122


def _terminate_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_git_process(
    argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int
) -> tuple[int, str]:
    """Capture up to 4 MiB; reject overflows instead of returning partial data.

    Git children inherit a dedicated POSIX process group. The group is
    force-terminated at completion or failure before this function returns.
    """
    if os.name != "posix":
        return 125, "Safe supervised Git execution requires POSIX."
    if timeout <= 0:
        return 125, "Invalid Git execution deadline."

    process = None
    output = bytearray()
    exit_code = None
    try:
        process = subprocess.Popen(
            argv, cwd=cwd, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, start_new_session=True,
        )
        assert process.stdout is not None
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selected:
            selected.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return 124, "Git command timed out."
                events = selected.select(min(remaining, 0.2))
                if not events:
                    continue
                chunk = os.read(process.stdout.fileno(), READ_BYTES)
                if not chunk:
                    break
                if len(output) + len(chunk) > MAX_GIT_OUTPUT_BYTES:
                    return OUTPUT_EXCEEDED, "Git output exceeded safe limit."
                output.extend(chunk)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 124, "Git command timed out."
        process.wait(timeout=remaining)
        exit_code = process.returncode
        return exit_code, output.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return 124, "Git command timed out."
    except (OSError, subprocess.SubprocessError):
        return 125, "Git execution failed safely."
    finally:
        if process is not None:
            try:
                _terminate_group(process)
            finally:
                if process.stdout is not None:
                    process.stdout.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
