# Security boundaries and threat model

PatchPilot processes **untrusted repository data** but remains a **local-only
development service**. A GitHub repository can contain hostile code, symlinks,
Git configuration, tests and prompt-injection content. The LLM itself is not a
trusted security principal.

## Trust boundaries

| Component | Trust assumption | Enforcement |
|---|---|---|
| User-managed backend | Trusted local developer | Backend binds to loopback, explicit CORS origins; **no public authentication** |
| Git source URLs | Untrusted | HTTPS Github owner/repo validation and no SSH or arbitrary hosts |
| Repository text | Untrusted | Symlink containment, bounded context, plan/edit validation |
| Model-produced test commands | Untrusted | Strict parser; **Docker test runner only** |
| Test container | Potentially hostile | No network, non-root, read-only source/root, resource caps, no host credentials/socket |
| Host Git plumbing | Trusted backend, untrusted repository contents | Static Git executable, bounded Git commands, system/global config disabled, hooks/fsmonitor disabled, terminal prompt disabled |
| Container image | Trusted by administrator | Static profile-based images, optional admin-configured OCI image, offline pull policy |

## Git hook and configuration boundaries

The backend still performs Git clone, status, staging and local commits on the
host. Unlike third-party test runners, these operations require host-side Git
plumbing for the local developer workflow. Commands are passed as structured
argv with no shell; the backend ignores inherited GIT_ variables, system/global
Git configuration, repository-specified hooks and fsmonitor helpers. Credential
helpers and file/ext transport protocols are disabled for these operations.

The agent is also forbidden from applying source-code patches to paths under
the repository's internal .git control directory. An adversarial regression
test creates an executable pre-commit hook and malicious Git configuration,
executes the allowed commit flow and verifies that the hook never runs.

This is defense in depth, **not a replacement for daemon isolation,
authentication, URL validation, resource quotas or independent audit**. The
CodeQL command-line finding remains open for further review.

## Live verification

The [Docker isolation workflow](../.github/workflows/docker-isolation.yml)
uses a real container on Linux to check:

- A host-only private file and Docker socket are not mounted inside the test
- A test cannot connect to an external TCP endpoint with `--network=none`
- Writes to the repository are denied; writes to bounded `/tmp` succeed
- The test runs as a non-root UID and sees none of the host's API keys

[Unit regressions](../backend/tests/test_sandbox.py) check policy flag
construction, image selection, symlink/path injection, environment filtering,
bounded output, timeouts, force cleanup and the API execution dispatch.

## Fail-closed behavior and trade-offs

Docker CLI and a preloaded, administrator-approved image must be present. An
unavailable runner returns status **125** with an explanation; policy violations
return **126**; timeouts return **124**, forcibly removing the named container.
PatchPilot does not try running the command directly on the host. The agent
stops retrying on these runner failures, instead of treating them as errors
that a model can solve by changing repository files.

The repository bind mount is **read-only**. This intentionally limits test
suites that write fixtures into their working tree. For packages not already
installed in the approved runner image, prepare a trusted image before running
tests; never mount host package-manager credentials to install dependencies
during the test. Runtime network access is disabled.

This runner is not a microVM or a formal isolation guarantee. Docker's kernel
attack surface and host daemon privileges remain. Avoid sharing the Docker
socket, rootful containers, host network or host home directory.

## Remaining deployment work

- Explicit auth, audit logs, quotas and rate limits for any multi-user service
- Dedicated minimal-permission worker and isolated per-user workspace storage
- MicroVM isolation or independently reviewed container runtime profiles
- Resource and disk quotas for Git cloning and source indexing, not just tests
- Threat model and signed, vulnerability-scanned runner image lifecycle
- Robust prompt-injection handling and secret scrubbing of indexed source
- End-to-end integration in a real configured installation, beyond CI smoke

See [security issue #23](https://github.com/harshitethic/PatchPilot/issues/23)
for deployment acceptance and remaining isolation caveats. **Do not dismiss
a CodeQL finding merely because the runner's CLI appears locked down.**


## HTTP access control (October 2026)

The API defaults to actual loopback-client and Host checks, plus validation
of browser Origin headers. Setting PATCHPILOT_API_TOKEN requires a strong
bearer token on all routes with constant-time comparison. Weak configured
secrets fail closed, rather than reverting to an unauthenticated server.
The local frontend can send a token from a password input without storing it
in localStorage. See [the HTTP access model](api-access-control.md).

This is shared-secret local developer access, not an independent multi-user
deployment security audit. Authenticate and privilege-separate repository
workers and introduce proper resource quotas before public hosting.