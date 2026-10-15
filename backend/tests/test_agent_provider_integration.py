"""ASGI agent workflow + real Git + real loopback provider, no paid services."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.main import app


class AgentProviderIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, *, invalid_coder=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "test-workspace" / "repo"
            repo.mkdir(parents=True)
            source = repo / "service.py"
            source.write_text("answer = 41\n")

            def git(*args):
                return subprocess.run(
                    ["git", *args], cwd=repo, check=True, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                ).stdout

            git("init", "-b", "main")
            git("add", "service.py")
            git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "fixture")
            requests = []
            errors = []
            tasks = []
            plan = {"summary": "Fix answer", "plan": ["Update value"],
                    "touched_files": ["service.py"], "test_command": None}
            edits = {"edits": [{"path": "service.py", "old": "41", "new": "42"}]}
            replies = [json.dumps({"message": {"content": json.dumps(plan)}}).encode(),
                       b"PRIVATE_INVALID_JSON" if invalid_coder else
                       json.dumps({"message": {"content": json.dumps(edits)}}).encode()]

            async def provider(reader, writer):
                tasks.append(asyncio.current_task())
                try:
                    headers = await reader.readuntil(b"\r\n\r\n")
                    length = int(next(line.split(b":", 1)[1] for line in headers.split(b"\r\n")
                                      if line.lower().startswith(b"content-length:")))
                    requests.append(json.loads(await reader.readexactly(length)))
                    reply = replies[len(requests) - 1]
                    writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
                    for offset in range(0, len(reply), 13):
                        part = reply[offset:offset + 13]
                        writer.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                        await writer.drain()
                    writer.write(b"0\r\n\r\n")
                    await writer.drain()
                except Exception as exc:
                    errors.append(type(exc).__name__)
                finally:
                    writer.close()
                    await writer.wait_closed()

            server = await asyncio.start_server(provider, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            try:
                with patch.dict(os.environ, {"OLLAMA_BASE_URL": f"http://127.0.0.1:{port}"}, clear=True), patch(
                    "app.main.clone_repo", return_value=("test-workspace", repo)
                ), patch("app.main.WORKSPACES", root):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
                        base_url="http://localhost",
                    ) as client:
                        response = await client.post("/api/run", json={
                            "repo_url": "https://github.com/example/fixture",
                            "task": "Set answer to 42", "model": "fixture", "branch_name": "fix/answer",
                        })
                        if invalid_coder:
                            self.assertEqual(response.status_code, 502)
                            self.assertNotIn("PRIVATE", response.text)
                            self.assertEqual(source.read_text(), "answer = 41\n")
                            self.assertEqual(git("diff"), "")
                        else:
                            self.assertEqual(response.status_code, 200, response.text)
                            result = response.json()
                            self.assertEqual(result["branch"], "fix/answer")
                            self.assertIn("+answer = 42", result["diff"])
                            self.assertFalse(result["tests_passed"])
                            self.assertIsNone(result["test_execution_backend"])
                            committed = await client.post("/api/commit", json={
                                "workspace_id": "test-workspace", "message": "fix: answer",
                            })
                            self.assertEqual(committed.status_code, 200, committed.text)
                            self.assertEqual(committed.json()["commit_sha"], git("rev-parse", "HEAD").strip())
                            self.assertEqual(git("status", "--porcelain"), "")
                            self.assertEqual(source.read_text(), "answer = 42\n")
                self.assertEqual(len(requests), 2)
                self.assertIn("answer = 41", requests[0]["messages"][1]["content"])
                self.assertEqual(errors, [])
            finally:
                server.close()
                await server.wait_closed()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def test_agent_edits_and_commits_with_real_chunked_provider(self):
        await self.exercise()

    async def test_invalid_coder_response_returns_502_without_source_changes(self):
        await self.exercise(invalid_coder=True)
