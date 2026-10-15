# Keep sensitive repository files out of model prompts

**Verified on synthetic fixtures, not an audit of every secret format.**

PatchPilot's planner and repair loop can call a local Ollama model or an
external OpenAI/OpenRouter-compatible provider. Source code placed into
its model context is therefore a data disclosure boundary, not simply a
convenience for summarization.

## Reproduced previous behavior

The original repository traversal included unignored, committed
`.env.production` and `config/credentials.json` files. The context
builder would read their complete text into the prompt. A separate
normal `unsafe.py` containing a recognizable GitHub-style token
was also included. These outcomes were reproduced with synthetic
fixture strings and regression tests that **failed on the prior code**.

## Guardrails

- Sensitive paths such as `.env*` (excluding harmless template names),
  `.npmrc`, private key files and well-known credential/session stores
  are excluded from both file inventory and repository context.
- Recognizable high-confidence GitHub, OpenAI, AWS, Telegram and
  PEM private-key patterns cause the entire ordinary source file to be
  skipped rather than redacting only the matched line.
- Even if a caller bypasses the file-inventory helper and directly
  supplies a sensitive path, the context builder checks it again.
- Repository context reads are bounded: source files larger than
  256 KB are excluded and individual files contribute at most 7,000
  text characters (with an explicit truncation marker).
- Error responses from model providers no longer relay the provider's
  raw error body, which may quote submitted prompts or credentials.

## Limitations and consent

**This is not a complete Data Loss Prevention (DLP) system.**
Encrypted files, arbitrary token formats, base64-encoded secrets,
unrecognized credential names, model outputs, user-provided task text,
GitHub issue content, and already-committed Git history may still contain
private information. Passwords in ordinary prose can also evade pattern
detection. Never submit a proprietary or private repository to an
external model without checking the selected provider and your
organization's data-handling policies.

Safe operation remains local-first with explicit provider selection;
a local Ollama model does not automatically imply full host isolation
or that every imported file is harmless. The commit guard separately
prevents common secrets from entering **new local Git commits**.

## Reproduce without a provider or live credentials

```bash
PYTHONPATH=backend python -m unittest backend.tests.test_model_context_secrets -v
PYTHONPATH=backend python -m unittest discover -s backend/tests -v
```

Tests use isolated directories, mock external HTTP responses, and
synthetic credentials. They verify that sensitive file content is
excluded, safe code remains available, and external-provider error
bodies aren't returned through PatchPilot's API.
