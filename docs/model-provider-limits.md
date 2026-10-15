# Bounded model-provider transport

## Problem reproduced

After the source-context privacy fix in PR #29, `ask_model` still used a
buffered HTTP POST. A provider could return an arbitrarily large body before
JSON parsing. HTTPX's per-operation timeout was not an overall deadline: a
server could keep a request alive by sending occasional bytes. Invalid JSON
and network exceptions also escaped without a stable API error contract.

Three regressions failed against that implementation: a 4 MiB chunked body,
malformed JSON, and a connection error. They now exercise the actual HTTPX
streaming interface and verify early termination and safe errors.

## Policy

All Ollama, OpenAI-compatible and OpenRouter calls use the same transport.
Limits are maintained in `backend/app/model_transport.py`, not controlled by
the repository, task text or model output.

| Boundary | Enforcement | API result |
| --- | --- | --- |
| Outbound JSON | At most 1 MiB of serialized UTF-8, checked before opening the client | 413 |
| Inbound body | At most 2 MiB, counted while streaming, including when Content-Length is missing or dishonest | 502 |
| Declared oversized body | Reject headers without reading the body | 502 |
| Compressed body | Request identity encoding; reject other encodings before raw-body reads or decompression | 502 |
| Total network operation | 240 seconds across connection, upload, headers and body; independent of continued stream progress | 504 |
| Connection | 10-second connect timeout within the overall deadline | 504 |
| Redirect/non-2xx | Do not follow redirects, read error bodies, or retry | 502 |
| Invalid JSON/envelope/content | Reject malformed, deeply nested, non-object, missing or non-text results | 502 |
| Transport failure | Generic message without exception text, URL, headers or provider body | 502 |
| Caller cancellation | Propagate cancellation and close the response/client contexts | Cancelled task |

An exact-limit response is accepted. The response is parsed only after a
bounded transfer. No automatic retry is added: repeating generation can
duplicate charges or produce a different edit. The user can inspect the
workspace and explicitly start a new run.

Rejecting compression is deliberate. Limiting the length of already-decoded
chunks does not stop a decompressor allocating a large expanded chunk first.
Providers or proxies that insist on compressed responses must be configured
to honor `Accept-Encoding: identity`.

## Reproduce offline

From the repository root, with Python 3.11+ and backend dependencies installed:

```bash
PYTHONPATH=backend python -m unittest backend.tests.test_model_transport -v
PYTHONPATH=backend python -m unittest backend.tests.test_agent_provider_integration -v
PYTHONPATH=backend python -m unittest discover -s backend/tests -v
```

The provider tests cover compatible response formats, exact and excessive
sizes, dishonest length headers, compressed data, redirects, errors, Unicode
request accounting, timeouts and cancellation. A real loopback TCP server
drips HTTP chunks until the shortened test deadline expires and observes
the client disconnect. It requires no external model service or API key.

The agent integration tests exercise **ASGI request → real Git branch →
loopback HTTP provider → source edit → diff → explicit local commit**. Git
cloning alone is replaced with a temporary seeded repository. A malformed
coder response returns 502 and leaves the tracked file unchanged. The
success fixture deliberately requests no test command and verifies that the
API does not falsely report tests as passed. Docker execution has its own
separate live integration workflow.

## Limits of these limits

- This bounds individual model transfers, not process-wide memory, model
  token generation, provider billing, concurrent requests or disk usage.
  JSON objects and transport buffers add overhead beyond the byte caps.
- It is not an inbound ASGI request-body cap. The caller's task already exists
  in application memory before the outbound serialization check.
- Existing provider URLs and proxy settings remain administrator-controlled;
  this is not a new network egress or SSRF sandbox.
- An aborted request does not guarantee the remote provider stopped generation
  or billing. Client task cancellation is tested; browser disconnection is
  not guaranteed to cancel an ASGI handler automatically.
- A provider failure can leave the newly cloned workspace/branch available
  for inspection. Existing user work is not deleted or rolled back. Earlier
  edits remain if a later repair call fails.
- Git cloning quotas, dedicated worker identities, per-user authorization,
  audit events, and independent deployment review remain open in
  [issue #23](https://github.com/harshitethic/PatchPilot/issues/23).
