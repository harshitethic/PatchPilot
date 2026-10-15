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

A subsequent review reproduced a bypass in the repair loop: its raw Git diff
could include adjacent credentials from a file excluded by the source filter,
and failed-test output could print credentials directly into the next prompt.
Four regression scenarios fail on commit `d05bd36` and pass with the repair
guardrails below; a fifth verifies that safe diagnostics still support a repair.

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
- Repair prompts use the same filtered current source context and no longer
  include raw Git diffs. Diffs can expose deleted or adjacent private lines
  even when the current file is excluded from source context.
- Failed-test diagnostics are checked for recognizable credential patterns
  **before** taking their last 12,000 characters. A match omits the entire
  diagnostic from the prompt; the model still receives the test return code.
  Safe diagnostics remain available. The existing bounded private local log
  retains its diagnostic tail with mode `0600` on creation.

## Limitations and consent

**This is not a complete Data Loss Prevention (DLP) system.**
Encrypted files, arbitrary token formats, base64-encoded secrets,
unrecognized credential names, model outputs, user-provided task text,
GitHub issue content, and already-committed Git history may still contain
private information. Passwords in ordinary prose can also evade pattern
detection. Never submit a proprietary or private repository to an
external model without checking the selected provider and your
organization's data-handling policies.

Unrecognized credentials printed by tests can still reach the model. These
guards do not prevent a model from proposing edits to sensitive paths. The
full patch remains available to the local API caller for review and is not
redacted; do not publish it without checking its contents.

Safe operation remains local-first with explicit provider selection;
a local Ollama model does not automatically imply full host isolation
or that every imported file is harmless. The commit guard separately
prevents common secrets from entering **new local Git commits**.

## Reproduce without a provider or live credentials

```bash
PYTHONPATH=backend python -m unittest backend.tests.test_model_context_secrets -v
PYTHONPATH=backend python -m unittest discover -s backend/tests -p 'test_repair_context_privacy.py' -v
PYTHONPATH=backend python -m unittest discover -s backend/tests -v
```

Tests use isolated directories, mock external HTTP responses, and
synthetic credentials. They verify that sensitive file content is
excluded, safe code remains available, and external-provider error
bodies aren't returned through PatchPilot's API.
The repair tests exercise the ASGI run endpoint and real temporary Git
repositories, while replacing the model and isolated test runner with
deterministic fixtures. They are not a live-provider or Docker isolation test.
