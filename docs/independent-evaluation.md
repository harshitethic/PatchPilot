# Independent engineering review: PatchPilot

**Status:** local developer-tool preview. Not audited multi-tenant infrastructure and not a guarantee of safe execution of arbitrary repositories. This guide is for testers using disposable code and credentials.

## Start with the boundaries, not the demo

- [Trust boundaries](security-boundaries.md): Git/host operations, untrusted repository text, model provider and isolated test container.
- [Docker isolation CI](https://github.com/harshitethic/PatchPilot/actions/workflows/docker-isolation.yml): actual Linux boundary checks.
- [HTTP access control](api-access-control.md): loopback-only assumptions and optional bearer token.
- [Private model context](model-context-privacy.md) and [bounded provider transfers](model-provider-limits.md): documented gaps, not complete exfiltration prevention.

## Reproduce a narrow test safely

1. Follow the [README local quick start](../README.md#-quick-start--macos--apple-silicon). Keep FastAPI on **127.0.0.1**. Use a disposable public or synthetic repository; never supply private data or production credentials.
2. Read the [browser verification scenario](browser-verification.md). It uses a controlled fixture; **do not** present fixture responses as real model intelligence.
3. Give the agent a deliberately simple failing test and inspect plan, isolated workspace, diff, exit status, and repair behavior. Confirm nothing is committed to the original checkout without review.
4. Repeat a controlled test with **Docker unavailable**. Model-selected test execution must fail **closed** without substituting host shell execution. Never loosen isolation just to get a demo to pass.

## Focused review requests

- Identify a demonstrable path from untrusted repository text to an unbounded host operation.
- Inspect bounded outputs, process cleanup, symlink handling, Git hooks/config isolation and token/origin controls.
- Try malformed provider replies, oversized context and cancellation; check what is returned over HTTP versus stored locally.
- Find and document two usability failures a new independent tester experiences during first setup.

Submit reproducible observations in [issues](https://github.com/harshitethic/PatchPilot/issues). Security-sensitive vulnerabilities should follow [SECURITY.md](../SECURITY.md), not public exploit details. Include OS, Docker version, commit SHA, sanitized steps, expected vs actual and the scope of testing. Distinguish an actual external review from author-generated test fixtures.

## Release gate (to verify; not a passing claim)

- [ ] Full backend and actual Docker isolation CI green
- [ ] Browser UI regression flow repeated on release artifact
- [ ] Clean install, including failure behavior when Docker/model missing
- [ ] License, release notes and known platform restrictions reviewed
- [ ] All external validation reported with reproducible issue links, not invented user counts
