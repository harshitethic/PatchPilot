# Browser result-state verification

The UI previously kept a successful run's plan, touched files, green test status
and Git diff while another request ran. If that request failed, the summary said
"Run failed" but the previous result remained visible. That could make a user
associate a patch or passing tests with the wrong request.

Starting a request now clears all previous result fields. Errors also clear
partially rendered fields, and `finally` restores the submit button. Input fields
stay available for correction; this does not delete any backend workspace.

## Reproduce

```bash
python -m venv .browser-venv
.browser-venv/bin/python -m pip install playwright==1.64.0
.browser-venv/bin/python -m playwright install chromium
.browser-venv/bin/python backend/browser_tests/run.py --output browser-evidence
```

On Linux, browser system libraries may also be required; Playwright's
`install --with-deps chromium` installs them. An existing Chromium can be selected
with `--browser /path/to/chromium`. Video recording requires Playwright's FFmpeg
bundle (`python -m playwright install ffmpeg`). Windows users can use the venv's
`Scripts/python.exe` instead.

Five scenarios run the repository's actual HTML and JavaScript in a fresh
Chromium context against a temporary loopback HTTP fixture:

1. A successful response renders its plan and filenames as literal text, even
   when they contain HTML-like strings.
2. A delayed next request immediately clears old fields and disables submit.
3. An HTTP 502 response clears old results, displays the error and restores submit.
4. A disconnected HTTP response does the same without uncaught JavaScript errors.
5. A malformed success response that throws during rendering clears partial results.

The regression fails against the previous UI: its earlier plan remains visible
during the delayed request. No latency threshold is used. The fixture server and
browser are closed on success or failure.

## Evidence and boundaries

[Browser UI CI](https://github.com/harshitethic/PatchPilot/actions/workflows/browser-ui.yml)
records the actual browser viewport and retains it with a final screenshot for
14 days. Run the same command to regenerate these artifacts after expiry.
The model input explicitly says "HTTP fixture — no real model".

Only the frontend API URL is redirected to the fixture's ephemeral same-origin
HTTP server. There is no intercepted successful response or fabricated DOM.
The response data is controlled test input: green test status in this recording
is **not evidence that repository tests actually ran**. This is a browser state
regression, not an end-to-end Git clone, AI inference, authentication/CORS or
Docker demonstration. Backend/provider integration and live Docker isolation
remain separate test suites. A fully integrated screen recording remains open.
