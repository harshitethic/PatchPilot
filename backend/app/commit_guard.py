"""Fail-closed checks for accidental secrets before making local Git commits.

This is defense in depth against common mistakes, not a comprehensive secret
scanner. Worktree files are intentionally not deleted or rewritten on refusal.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Callable

MAX_STAGED_DIFF_CHARS = 2_000_000

# These are frequently private even when the repository lacks a .gitignore.
_BLOCKED_NAMES = frozenset({
    ".npmrc", ".pypirc", ".netrc", ".env", "credentials.json", "creds.json",
    "secrets.json", "accesser.json", "id_rsa", "id_ed25519",
    "id_ecdsa", "id_dsa", "auth_info_baileys",
})
_BLOCKED_EXTENSIONS = frozenset({".pem", ".p12", ".pfx", ".key", ".keystore", ".jks"})
_BLOCKED_COMPONENTS = frozenset({".aws", ".ssh", ".gnupg", "auth_info_baileys"})
_SAMPLE_ENV_NAMES = frozenset({".env.example", ".env.sample", ".env.template", ".env.defaults"})

# High-confidence signatures, not a generic detector that treats every
# variable named PASSWORD or API_KEY as proof of leaked credentials.
_SIGNATURES = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{25,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b[0-9]{8,12}:AA[A-Za-z0-9_-]{25,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
)


def is_sensitive_path(path: str) -> bool:
    parts = tuple(component.lower() for component in PurePosixPath(path).parts)
    if not parts:
        return False
    if any(part in _BLOCKED_COMPONENTS for part in parts):
        return True
    name = parts[-1]
    if name in _SAMPLE_ENV_NAMES or name.startswith(".env.example."):
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    if name in _BLOCKED_NAMES:
        return True
    return PurePosixPath(name).suffix in _BLOCKED_EXTENSIONS


def contains_secret_signature(content: str) -> bool:
    """Detect a limited set of recognizable credential patterns, not all secrets."""
    return any(signature.search(content) for signature in _SIGNATURES)


def staged_commit_risk(repo, git_run: Callable) -> str | None:
    """Return a generic reason without exposing secrets in any error message.

    Inspect what is *actually staged* in Git, rather than only the worktree.
    This function must run after git add and before git commit.
    """
    code, names = git_run(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"],
        repo, 30,
    )
    if code != 0:
        return "Cannot verify staged filenames"
    for path in names.split("\0"):
        if path and is_sensitive_path(path):
            return "Sensitive filename is staged"

    code, diff = git_run(
        ["git", "diff", "--cached", "--no-ext-diff", "--no-textconv", "--no-color", "--unified=0"],
        repo, 45,
    )
    if code != 0 or len(diff) > MAX_STAGED_DIFF_CHARS:
        return "Staged diff cannot be safely verified"

    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            if contains_secret_signature(line):
                return "Potential credential detected in staged additions"
    return None
