# Security boundaries and local trust model

PatchPilot is a local developer tool. Repository-defined test scripts are
executable code, even when the commands are restricted to an allowlist.
The command allowlist is NOT a sandbox. Do not point PatchPilot at untrusted
repositories or expose its API directly to the public Internet.

## Implemented boundaries

- Git cloning requires GitHub HTTPS owner/repository URLs. Local files,
  alternate hosts, credential-bearing URLs and extra path segments are refused.
- Repository indexing and context construction skip symlinks and external files.
- Agent edits reject path escapes and duplicate edits to a single file.
- Workspace lookups reject malformed identifiers.
- Test commands are parsed without shell execution, but their code may run.

## Work still required before production deployment

Provide isolated containers or microVMs, bounded filesystem resources,
network egress restrictions, authentication, rate limits, workspace expiry,
secret redaction, and prompt-injection defenses. A public GitHub repository
can itself contain sensitive or malicious content, so operate under an
unprivileged local OS account without production credentials.

These tests cover specific regressions, not complete isolation.
