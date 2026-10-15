# Safety checks before PatchPilot creates local Git commits

**Status: v0.4 local API, opt-in explicit commit action.**
PatchPilot is a local developer tool; this is an additional check, not an
independently audited secret-detection product.

## Prior bug

The commit endpoint ran Git add --all and committed every staged file with
no credential review. In a repository without an effective gitignore, that
could commit a new .env file. A source file containing a recognizable GitHub
credential could also enter the local Git history.

A regression test using a real temporary Git repository reproduces both
outcomes on the previous implementation. The tests now check that the
commit is refused and that HEAD does not advance.

## Current workflow

1. Validate the workspace and commit request as before.
2. Stage the changes using Git add.
3. Inspect the exact staged Git paths and added diff lines.
4. **Fail closed** on sensitive filenames and recognizable high-confidence
   credential formats. Unknown or oversized staged diff output also blocks
   the commit instead of being treated as safe.
5. Only after these checks pass does Git create a commit.

Sensitive paths include .env, .env.local/production, .npmrc, .ssh,
private-key formats, and well-known secret-storage file names. Documented
sample templates such as .env.example are allowed unless their *contents*
contain a recognized credential signature.

Recognized signatures include common GitHub tokens, OpenAI-style keys,
AWS access key IDs, Telegram bot tokens, and private-key PEM headers. The
matching token and Git diff are **never** included in the API error response.

## Important limits

- **Not a comprehensive secret scanner.** Arbitrary passwords, custom key
  formats, credentials split across lines, encoded payloads, unknown binary
  formats, and secrets already committed in Git history may evade detection.
  Use provider-specific credential scanning and rotate leaked keys.
- Refusal leaves the developer's files and Git staging area intact. Review
  and unstage offending changes manually; PatchPilot does not delete files,
  reset user changes, rewrite Git history or silently suppress a secret.
- A legitimate large diff may be refused until reviewed or split into
  smaller commits. That is safer than certifying uninspected content.
- The check guards only the **explicit commit endpoint**. It is not a
  general guarantee that model-generated files, uploaded files, prompts,
  private workspaces or shell commands are free of secrets.
- Backend Git operations still execute on the host. Container isolation
  for tests, bearer access controls, and local service deployment
  boundaries are described separately in
  [security boundaries](security-boundaries.md).

## Reproduction

Run from the project checkout on Python 3.11+ with backend requirements:

```bash
PYTHONPATH=backend python -m unittest backend.tests.test_secret_commit -v
PYTHONPATH=backend python -m unittest discover -s backend/tests -v
```

Tests use **synthetic fixture strings only**, never real credentials. They
verify an authenticated loopback HTTP request returns a generic 409 on an
unsafe commit, the branch HEAD remains unchanged, and harmless commits
still work.
