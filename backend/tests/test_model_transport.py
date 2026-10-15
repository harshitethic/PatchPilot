"""Provider failure regressions, using real HTTPX streams without paid APIs."""
import asyncio
import gzip
import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app.main import ask_model
from app.model_transport import MAX_RESPONSE_BYTES


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.consumed = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    async def aclose(self):
        self.closed = True


class ModelTransportTests(unittest.IsolatedAsyncioTestCase):
    async def call_provider(self, handler, *, user="task", provider="ollama"):
        client_class = httpx.AsyncClient
        with patch("app.main.httpx.AsyncClient", side_effect=lambda **kw: client_class(
            **kw, transport=httpx.MockTransport(handler)
        )):
            return await ask_model(provider, "test-model", "system", user)

    async def test_chunked_oversized_body_stops_reading_and_closes(self):
        stream = TrackedStream([b" " * 65536] * 64)
        with self.assertRaises(HTTPException) as caught:
            await self.call_provider(lambda _: httpx.Response(200, stream=stream))
        self.assertEqual(caught.exception.status_code, 502)
        self.assertLess(stream.consumed, 64)
        self.assertTrue(stream.closed)

    async def test_invalid_json_is_sanitized(self):
        secret = "PRIVATE_PROVIDER_BODY"
        with self.assertRaises(HTTPException) as caught:
            await self.call_provider(lambda _: httpx.Response(
                200, stream=TrackedStream([secret.encode()])
            ))
        self.assertEqual(caught.exception.status_code, 502)
        self.assertNotIn(secret, caught.exception.detail)

    async def test_transport_error_is_sanitized(self):
        def broken(request):
            raise httpx.ConnectError("PRIVATE_PROVIDER_URL_AND_KEY", request=request)

        with self.assertRaises(HTTPException) as caught:
            await self.call_provider(broken)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertNotIn("PRIVATE", caught.exception.detail)

    async def test_supported_provider_formats_preserve_text_and_payload(self):
        for provider in ("ollama", "openai", "openrouter"):
            with self.subTest(provider=provider), patch.dict(os.environ, {
                "OPENAI_API_KEY": "test-key", "OPENROUTER_API_KEY": "test-key",
            }):
                text = "नमस्ते — valid model content"
                message = {"content": text}
                data = {"message": message} if provider == "ollama" else {"choices": [{"message": message}]}
                stream = TrackedStream([json.dumps(data).encode()])

                def respond(request):
                    self.assertEqual(request.headers["accept-encoding"], "identity")
                    payload = json.loads(request.content)
                    self.assertEqual(payload["messages"][1]["content"], "task")
                    self.assertEqual(payload["model"], "test-model")
                    return httpx.Response(200, stream=stream)

                self.assertEqual(await self.call_provider(respond, provider=provider), text)
                self.assertTrue(stream.closed)

    async def test_declared_oversized_body_refused_without_reading(self):
        stream = TrackedStream([b"PRIVATE"])
        with self.assertRaises(HTTPException) as caught:
            await self.call_provider(lambda _: httpx.Response(
                200, headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)}, stream=stream
            ))
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(stream.consumed, 0)
        self.assertTrue(stream.closed)

    async def test_small_declared_length_does_not_bypass_stream_limit(self):
        stream = TrackedStream([b" " * 65536] * 64)
        with self.assertRaises(HTTPException) as caught:
            await self.call_provider(lambda _: httpx.Response(
                200, headers={"Content-Length": "1"}, stream=stream
            ))
        self.assertEqual(caught.exception.status_code, 502)
        self.assertLess(stream.consumed, 64)
        self.assertTrue(stream.closed)

    async def test_response_at_exact_limit_is_accepted(self):
        data = b'{"message":{"content":"ok"}}'
        body = data + b" " * (MAX_RESPONSE_BYTES - len(data))
        stream = TrackedStream([body[i:i+65536] for i in range(0, len(body), 65536)])
        self.assertEqual(await self.call_provider(lambda _: httpx.Response(200, stream=stream)), "ok")
        self.assertTrue(stream.closed)

    async def test_compressed_body_rejected_before_decompression(self):
        stream = TrackedStream([gzip.compress(b" " * (MAX_RESPONSE_BYTES + 1))])
        with self.assertRaises(HTTPException) as caught:
            await self.call_provider(lambda _: httpx.Response(
                200, headers={"Content-Encoding": "gzip"}, stream=stream
            ))
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(stream.consumed, 0)
        self.assertTrue(stream.closed)

    async def test_redirect_and_error_bodies_are_not_read_or_retried(self):
        for status in (302, 401, 429, 500):
            with self.subTest(status=status):
                calls = []
                stream = TrackedStream([b"PRIVATE_PROVIDER_ERROR"])

                def respond(request):
                    calls.append(request)
                    return httpx.Response(status, headers={"Location": "https://other.invalid"}, stream=stream)

                with self.assertRaises(HTTPException) as caught:
                    await self.call_provider(respond)
                self.assertEqual(caught.exception.status_code, 502)
                self.assertNotIn("PRIVATE", caught.exception.detail)
                self.assertEqual(len(calls), 1)
                self.assertEqual(stream.consumed, 0)
                self.assertTrue(stream.closed)

    async def test_invalid_shapes_and_deep_json_are_sanitized(self):
        bodies = [b"[]", b"null", b"{}", b'{"message":null}',
                  b'{"message":{"content":[]}}', b'{"message":{"content":" "}}',
                  b"[" * 2000 + b"]" * 2000, b"\xff"]
        for body in bodies:
            with self.subTest(body=body[:40]), self.assertRaises(HTTPException) as caught:
                await self.call_provider(lambda _: httpx.Response(200, stream=TrackedStream([body])))
            self.assertEqual(caught.exception.status_code, 502)

    async def test_oversized_utf8_request_refused_before_network(self):
        calls = []
        with self.assertRaises(HTTPException) as caught:
            await self.call_provider(lambda req: calls.append(req), user="😀" * 300000)
        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(calls, [])

    async def test_idle_timeout_is_sanitized(self):
        def respond(request):
            raise httpx.ReadTimeout("PRIVATE_REQUEST", request=request)

        with self.assertRaises(HTTPException) as caught:
            await self.call_provider(respond)
        self.assertEqual(caught.exception.status_code, 504)
        self.assertNotIn("PRIVATE", caught.exception.detail)

    async def test_caller_cancellation_closes_stream_and_propagates(self):
        started = asyncio.Event()

        class WaitingStream(TrackedStream):
            async def __aiter__(self):
                started.set()
                await asyncio.Event().wait()
                yield b"unreachable"

        stream = WaitingStream([])
        task = asyncio.create_task(self.call_provider(lambda _: httpx.Response(200, stream=stream)))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stream.closed)


class LiveProviderTransportTests(unittest.IsolatedAsyncioTestCase):
    """Exercise real HTTPX/httpcore TCP I/O, not just a mock transport."""

    async def test_slow_drip_hits_total_deadline_and_disconnects(self):
        disconnected = asyncio.Event()
        writers = []
        tasks = []

        async def peer(reader, writer):
            tasks.append(asyncio.current_task())
            writers.append(writer)
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                length = int(next(line.split(b":", 1)[1] for line in headers.split(b"\r\n")
                                  if line.lower().startswith(b"content-length:")))
                await reader.readexactly(length)
                writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
                # Keep making progress well inside the per-read timeout.
                while True:
                    writer.write(b"1\r\n \r\n")
                    await writer.drain()
                    try:
                        if await asyncio.wait_for(reader.read(1), 0.01) == b"":
                            disconnected.set()
                            return
                    except TimeoutError:
                        pass
            except (ConnectionError, asyncio.IncompleteReadError):
                disconnected.set()
            finally:
                writer.close()

        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with patch.dict(os.environ, {"OLLAMA_BASE_URL": f"http://127.0.0.1:{port}"}, clear=True), patch(
                "app.model_transport.TOTAL_TIMEOUT_SECONDS", 0.3
            ):
                with self.assertRaises(HTTPException) as caught:
                    await asyncio.wait_for(ask_model("ollama", "test", "system", "task"), 3)
            self.assertEqual(caught.exception.status_code, 504)
            await asyncio.wait_for(disconnected.wait(), 2)
        finally:
            server.close()
            await server.wait_closed()
            for writer in writers:
                writer.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
