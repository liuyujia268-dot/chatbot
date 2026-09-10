"""Minimal bidirectional WebSocket client for local NapCat OneBot traffic."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import ssl
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4


logger = logging.getLogger(__name__)
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class OneBotWebSocketEvents:
    def __init__(
        self,
        url: str,
        token: str | None = None,
        *,
        reconnect_seconds: float = 3.0,
    ) -> None:
        self.url = url
        self.token = token
        self.reconnect_seconds = reconnect_seconds
        self._writer: asyncio.StreamWriter | None = None
        self._send_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def __aiter__(self) -> AsyncIterator[dict]:
        while True:
            try:
                reader, writer = await self._connect()
                self._writer = writer
                try:
                    async for text in self._messages(reader, writer):
                        event = json.loads(text)
                        if isinstance(event, dict):
                            echo = str(event.get("echo", ""))
                            pending = self._pending.pop(echo, None)
                            if pending is not None and not pending.done():
                                pending.set_result(event)
                                continue
                            yield event
                finally:
                    if self._writer is writer:
                        self._writer = None
                    self._fail_pending(
                        ConnectionError("NapCat WebSocket disconnected")
                    )
                    writer.close()
                    await writer.wait_closed()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("onebot_websocket_disconnected url=%s", self.url)
                await asyncio.sleep(self.reconnect_seconds)

    def _fail_pending(self, error: Exception) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(error)

    async def call_action(
        self,
        action: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        writer = self._writer
        if writer is None:
            raise ConnectionError("NapCat WebSocket is not connected")
        echo = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        request = json.dumps(
            {"action": action, "params": params, "echo": echo},
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            async with self._send_lock:
                if self._writer is not writer:
                    raise ConnectionError("NapCat WebSocket reconnected")
                await self._send_frame(writer, 0x1, request)
            response = await asyncio.wait_for(future, timeout_seconds)
        finally:
            self._pending.pop(echo, None)
        try:
            retcode = int(response.get("retcode", -1))
        except (TypeError, ValueError):
            retcode = -1
        if response.get("status") != "ok" or retcode != 0:
            raise RuntimeError(
                f"NapCat action failed retcode={response.get('retcode')}"
            )
        data = response.get("data")
        return data if isinstance(data, dict) else {}

    async def _connect(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        parsed = urlsplit(self.url)
        host = parsed.hostname
        if not host or parsed.scheme not in {"ws", "wss"}:
            raise ValueError("invalid OneBot WebSocket URL")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        context = ssl.create_default_context() if parsed.scheme == "wss" else None
        reader, writer = await asyncio.open_connection(host, port, ssl=context)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        headers = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        if self.token:
            headers.append(f"Authorization: Bearer {self.token}")
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
        await writer.drain()
        response = await reader.readuntil(b"\r\n\r\n")
        self._validate_handshake(response, key)
        return reader, writer

    @staticmethod
    def _validate_handshake(response: bytes, key: str) -> None:
        lines = response.decode("latin-1").split("\r\n")
        if not lines or " 101 " not in lines[0]:
            raise ConnectionError("NapCat WebSocket handshake was rejected")
        headers = {
            name.strip().lower(): value.strip()
            for line in lines[1:]
            if ":" in line
            for name, value in [line.split(":", 1)]
        }
        expected = base64.b64encode(
            hashlib.sha1((key + _GUID).encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise ConnectionError("invalid WebSocket handshake response")

    async def _messages(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> AsyncIterator[str]:
        fragments = bytearray()
        while True:
            final, opcode, payload = await self._read_frame(reader)
            if opcode == 0x8:
                return
            if opcode == 0x9:
                await self._send_frame(writer, 0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1:
                fragments = bytearray(payload)
            elif opcode == 0x0 and fragments:
                fragments.extend(payload)
            else:
                continue
            if len(fragments) > 16 * 1024 * 1024:
                raise ConnectionError("OneBot WebSocket message is too large")
            if final:
                yield fragments.decode("utf-8")
                fragments.clear()

    @staticmethod
    async def _read_frame(
        reader: asyncio.StreamReader,
    ) -> tuple[bool, int, bytes]:
        header = await reader.readexactly(2)
        final = bool(header[0] & 0x80)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = int.from_bytes(await reader.readexactly(2), "big")
        elif length == 127:
            length = int.from_bytes(await reader.readexactly(8), "big")
        if length > 16 * 1024 * 1024:
            raise ConnectionError("OneBot WebSocket frame is too large")
        mask = await reader.readexactly(4) if masked else b""
        payload = await reader.readexactly(length)
        if mask:
            payload = bytes(
                value ^ mask[index % 4]
                for index, value in enumerate(payload)
            )
        return final, opcode, payload

    @staticmethod
    async def _send_frame(
        writer: asyncio.StreamWriter,
        opcode: int,
        payload: bytes,
    ) -> None:
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length < 65536:
            header = bytes((0x80 | opcode, 0x80 | 126)) + length.to_bytes(2, "big")
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + length.to_bytes(8, "big")
        masked = bytes(
            value ^ mask[index % 4]
            for index, value in enumerate(payload)
        )
        writer.write(header + mask + masked)
        await writer.drain()
