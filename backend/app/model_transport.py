"""Bound model-provider transfers without trusting upstream lengths or errors."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from fastapi import HTTPException

MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TOTAL_TIMEOUT_SECONDS = 240


def encode_request(payload: dict[str, Any]) -> bytes:
    # Check incrementally rather than constructing an unbounded serialized copy.
    body = bytearray()
    for piece in json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).iterencode(payload):
        encoded = piece.encode("utf-8")
        if len(body) + len(encoded) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "Model request exceeds the 1 MiB transfer limit")
        body.extend(encoded)
    return bytes(body)


async def request_model_json(
    url: str, headers: dict[str, str], payload: dict[str, Any]
) -> dict[str, Any]:
    body = encode_request(payload)
    # Request identity encoding and consume raw bytes: checking a decompressed
    # stream *after* HTTPX expands it would not bound decompression allocation.
    request_headers = {**headers, "Accept-Encoding": "identity"}
    try:
        # HTTPX timeouts bound idle I/O, not total duration. A peer sending a
        # small chunk before every read timeout must still hit this deadline.
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(240, connect=10), follow_redirects=False,
            ) as client:
                async with client.stream("POST", url, headers=request_headers, content=body) as response:
                    if not 200 <= response.status_code < 300:
                        raise HTTPException(502, f"Model provider request failed (HTTP {response.status_code})")
                    if response.headers.get("content-encoding", "identity").lower().strip() != "identity":
                        raise HTTPException(502, "Model provider returned unsupported content encoding")
                    declared = response.headers.get("content-length")
                    if declared is not None:
                        try:
                            length = int(declared)
                        except ValueError:
                            raise HTTPException(502, "Model provider returned invalid content length") from None
                        if length < 0:
                            raise HTTPException(502, "Model provider returned invalid content length")
                        if length > MAX_RESPONSE_BYTES:
                            raise HTTPException(502, "Model response exceeds the 2 MiB transfer limit")
                    received = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(received) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise HTTPException(502, "Model response exceeds the 2 MiB transfer limit")
                        received.extend(chunk)
        try:
            data = json.loads(received)
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPException(502, "Model provider returned invalid JSON") from None
        if not isinstance(data, dict):
            raise HTTPException(502, "Model provider returned an invalid response object")
        return data
    except (TimeoutError, httpx.TimeoutException):
        raise HTTPException(504, "Model provider request timed out") from None
    except httpx.HTTPError:
        # URLs, response bodies and exception text may contain private data.
        raise HTTPException(502, "Model provider connection failed") from None


def model_content(data: dict[str, Any], provider: str) -> str:
    try:
        if provider == "ollama":
            content = data["message"]["content"]
        else:
            content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError):
        raise HTTPException(502, "Model provider returned missing or invalid text content") from None
    return content
