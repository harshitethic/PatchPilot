"""Real Chromium UI regressions against a controlled loopback HTTP fixture.

This verifies frontend state, not model quality, Git cloning or Docker execution.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
RESULT = {
    "summary": "Fixture result: changed answer",
    "plan": ["<img src=x onerror=alert(1)>"],
    "touched_files": ["<script>fixture</script>.py"],
    "test_command": "python -m unittest",
    "tests_passed": True,
    "test_output": "FIXTURE_TEST_OUTPUT",
    "diff": "+answer = 42",
    "workspace_id": "fixture-workspace",
    "branch": "fixture/answer",
    "history": [{"action": "test"}],
}


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if urlsplit(self.path).path != "/":
            self.send_error(404)
            return
        # Only the endpoint changes: both production HTML and its script execute
        # unmodified otherwise. No browser request interception or DOM fabrication.
        page = (ROOT / "frontend/index.html").read_text().replace(
            "const API='http://127.0.0.1:8000';", "const API='';"
        )
        body = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/run":
            self.send_error(404)
            return
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(data)
        scenario = data["task"]
        if scenario == "network-failure":
            self.close_connection = True
            return
        if scenario == "delayed-success":
            self.server.started.set()
            if not self.server.release.wait(timeout=10):
                self.send_error(504)
                return
        failed = scenario == "provider-failure"
        payload = {"detail": "Fixture provider unavailable"} if failed else RESULT
        if scenario == "malformed-success":
            payload = {key: value for key, value in RESULT.items() if key != "history"}
        body = json.dumps(payload).encode()
        self.send_response(502 if failed else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def assert_no_previous_result(page):
    for selector, old in (
        ("#plan", "<img"), ("#files", "<script>"), ("#tests", "Tests passed"),
        ("#testOutput", "FIXTURE_TEST_OUTPUT"), ("#diff", "+answer = 42"),
    ):
        expect(page.locator(selector)).not_to_contain_text(old)


def exercise(browser, server, output):
    options = {"viewport": {"width": 1280, "height": 1000}}
    if output:
        options.update(record_video_dir=str(output), record_video_size=options["viewport"])
    context = browser.new_context(**options)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(f"http://127.0.0.1:{server.server_port}/")
        page.locator("#repo").fill("https://github.com/example/browser-fixture")
        page.locator("#model").fill("HTTP fixture — no real model")
        page.locator("#task").fill("success")
        page.locator("#run").click()
        expect(page.locator("#summary")).to_have_text(RESULT["summary"])
        expect(page.locator("#tests")).to_contain_text("Tests passed")
        expect(page.locator("#diff")).to_have_text(RESULT["diff"])
        expect(page.locator("#plan img")).to_have_count(0)
        expect(page.locator("#files script")).to_have_count(0)
        expect(page.locator("#plan")).to_contain_text(RESULT["plan"][0])
        expect(page.locator("#files")).to_contain_text(RESULT["touched_files"][0])
        print("PASS successful response renders literal untrusted text", flush=True)

        page.locator("#task").fill("delayed-success")
        page.locator("#run").click()
        expect(page.locator("#run")).to_be_disabled()
        expect(page.locator("#summary")).to_have_text("Working…")
        assert_no_previous_result(page)
        assert server.started.wait(timeout=5), "Fixture did not receive delayed request"
        assert len(server.requests) == 2
        server.release.set()
        expect(page.locator("#diff")).to_have_text(RESULT["diff"])
        print("PASS in-flight request clears old results and disables submit", flush=True)

        page.locator("#task").fill("provider-failure")
        page.locator("#run").click()
        expect(page.locator("#summary")).to_have_text("Run failed")
        expect(page.locator("#status")).to_contain_text("Fixture provider unavailable")
        expect(page.locator("#run")).to_be_enabled()
        assert_no_previous_result(page)
        print("PASS HTTP error clears previous results and restores submit", flush=True)

        page.locator("#task").fill("success")
        page.locator("#run").click()
        expect(page.locator("#diff")).to_have_text(RESULT["diff"])
        page.locator("#task").fill("network-failure")
        page.locator("#run").click()
        expect(page.locator("#summary")).to_have_text("Run failed")
        expect(page.locator("#status")).to_contain_text("Error:")
        expect(page.locator("#run")).to_be_enabled()
        assert_no_previous_result(page)
        page.locator("#task").fill("malformed-success")
        page.locator("#run").click()
        expect(page.locator("#summary")).to_have_text("Run failed")
        expect(page.locator("#run")).to_be_enabled()
        assert_no_previous_result(page)
        print("PASS partially rendered malformed response is cleared", flush=True)
        assert not errors, errors
        print("PASS connection failure clears previous results without JavaScript errors", flush=True)
        if output:
            page.screenshot(path=str(output / "failure-state.png"), full_page=True)
    finally:
        server.release.set()
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Optional locally installed Chromium executable")
    parser.add_argument("--output", type=Path, help="Save actual browser video and final screenshot")
    args = parser.parse_args()
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    server.requests = []
    server.release = threading.Event()
    server.started = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.browser)
            try:
                exercise(browser, server, args.output)
            finally:
                browser.close()
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
