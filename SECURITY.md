# Security Policy

## Supported version

Security fixes target the current `main` branch.

## Reporting a vulnerability

Do not disclose an exploitable issue or secret in a public GitHub issue. A useful report includes reproduction steps, affected code paths, impact, and a minimal proof of concept with sensitive data removed.

## High-risk areas

PatchPilot works with Git repositories, model-generated edits, shell commands, workspaces, and optional GitHub credentials. Security reports are particularly valuable for:

- command injection or unsafe command construction;
- path traversal or writes outside the isolated workspace;
- malicious repository content escaping repository boundaries;
- credential or environment-variable exposure;
- unsafe handling of GitHub tokens or imported issue content;
- server-side request forgery or unsafe repository URL handling;
- privilege escalation.

Treat repositories and task text as untrusted input. PatchPilot should not require unrestricted root access.

## Secrets

Keep provider keys and GitHub tokens in local environment files or a secret manager. Never commit them.
