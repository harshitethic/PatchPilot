# Contributing to PatchPilot

PatchPilot is a **local-first, single-user preview**, not a production multi-tenant coding-agent service. We welcome reproducible fixes with explicit limits.

## Getting started

Choose a small issue or submit a [reproducible bug report](.github/ISSUE_TEMPLATE/reproducible-bug.md). For security-sensitive changes, first read [security boundaries](docs/security-boundaries.md).

Backend CI runs Python 3.11 and 3.12. From the checkout root:

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m compileall app tests
python -m unittest discover -s tests -v
```

These are the [backend CI](.github/workflows/backend-ci.yml) test commands. Controlled offline tests do not establish live provider, Docker or arbitrary-repository correctness.

For changes involving Docker isolation, review the image and Docker daemon, then run the separately maintained [integration workflow](.github/workflows/docker-isolation.yml). From the root checkout:

```bash
docker pull python:3.12-slim-bookworm
python -m pip install -r backend/requirements.txt
PYTHONPATH=backend python -m unittest backend/tests/docker_smoke.py -v
```

Frontend changes should include browser reproduction steps and reference [browser verification](docs/browser-verification.md). The Docker test runner is not a complete host sandbox or independent audit.

## Pull request expectations

1. Link a relevant issue; describe the observed problem and exact reproduction steps.
2. Keep the change focused; add a failing-before/passing-after regression where possible.
3. Report exact test commands and results. Explicitly list any unrun live-browser, provider, Docker, OS or deployment verification.
4. Explain security boundary changes, migrations and backwards-compatibility effects.
5. Redact logs. Never commit `.env`, credentials, raw model prompts from private repositories or local workspace data.

AI assistance may be used if disclosed and personally reviewed. A green CI build does not constitute human maintainer approval.

## Safety and reporting

Model-supplied edits remain reviewable. Test commands are routed into a fail-closed Docker runner, but host Git operations and cloning are separate trust boundaries. Keep the HTTP service loopback-only; do not publicly deploy without a full security design. See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Feedback from independent users

Outside developers can help by following the [first-run instructions](README.md#-quick-start--macos--apple-silicon), reporting their OS and Python version, and sharing a redacted pass/fail trace from an actual trial. Fixture runs are not evidence of independent adoption.