"""Minimal bidirectional WebSocket client for local NapCat OneBot traffic."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import ssl
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
from app.core.send_guard import check_submission


logger = logging.getLogger(__name__)
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class OneBotAuthenticationError(ConnectionError):
    """WebSocket authentication is distinct from WebUI login."""


class OneBotWebSocketEvents:
    def __init__(
        self,
        url: str,
        token: str | None = None,
        *,
        reconnect_seconds: float = 3.0,
        connect_timeout_seconds: float = 10.0,
        ping_interval_seconds: float = 30.0,
        ping_timeout_seconds: float = 10.0,
        status_callback: Callable[[str, str | None], None] | None = None,
    ) -> None:
        if min(
            reconnect_seconds,
            connect_timeout_seconds,
            ping_interval_seconds,
            ping_timeout_seconds,
        ) <= 0:
            raise ValueError("WebSocket timeout values must be positive")
        self.url = url
        self.token = token
        self.reconnect_seconds = reconnect_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        self.ping_interval_seconds = ping_interval_seconds
        self.ping_timeout_seconds = ping_timeout_seconds
        self._status_callback = status_callback
        self._writer: asyncio.StreamWriter | None = None
        self._send_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._last_disconnect_error: str | None = None
        self._disconnect_repeat_count = 0

    @property
    def connected(self) -> bool:
        return self._writer is not None

    async def __aiter__(self) -> AsyncIterator[dict]:
        while True:
            if not getattr(self, '_enabled', True):
                await self._wait_reconnect()
                continue
            try:
                revision = getattr(self, '_connection_revision', 0)
                self._notify_status("connecting")
                reader, writer = await self._connect()
                if revision != getattr(self, '_connection_revision', 0):
                    await self._close_writer(writer)
                    continue  # a handshake started with the previous account's config
                self._writer = writer
                self._last_disconnect_error = None
                self._disconnect_repeat_count = 0
                self._notify_status("connected")
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
                    self._notify_status("disconnected")
                    self._fail_pending(
                        ConnectionError("NapCat WebSocket disconnected")
                    )
                    await self._close_writer(writer)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if revision != getattr(self, '_connection_revision', 0):
                    continue
                detail = ('OneBot 鉴权失败：检查当前账号的 WebSocket Token / QQ_WS_TOKEN，与 WebUI Token 不同'
                          if isinstance(exc, OneBotAuthenticationError)
                          else 'OneBot 端口未监听：请在当前账号的 NapCat 网络配置启用 WebSocket 服务端'
                          if isinstance(exc, ConnectionRefusedError)
                          else 'OneBot 连接超时：检查当前账号的 WebSocket 地址、端口和服务状态'
                          if isinstance(exc, TimeoutError)
                          else 'OneBot 连接失败：检查 QQ_WS_URL、当前账号的 NapCat OneBot 服务及网络')
                self._notify_status("error", detail)
                self._log_disconnect(exc)
            # A normal close frame is also a disconnection. Do not tight-loop
            # handshakes while NapCat is logging out or restarting its account.
            await self._wait_reconnect()

    async def _wait_reconnect(self) -> None:
        changed = getattr(self, '_configuration_changed', None)
        if changed is None:
            self._configuration_changed = changed = asyncio.Event()
        try:
            await asyncio.wait_for(changed.wait(), timeout=self.reconnect_seconds)
        except TimeoutError:
            pass
        changed.clear()

    def configure_connection(self, url: str, token: str | None, *, enabled: bool = True,
                             force: bool = False) -> None:
        """Update only this client's in-memory endpoint; never write NapCat/.env."""
        if (not force and self.url == url and self.token == token
                and getattr(self, '_enabled', True) == enabled):
            return
        self.url, self.token, self._enabled = url, token, enabled
        self._connection_revision = getattr(self, '_connection_revision', 0) + 1
        self._notify_status('disconnected', None)
        writer, self._writer = self._writer, None
        self._fail_pending(ConnectionError('OneBot connection configuration changed'))
        if writer is not None:
            writer.close()
        if not hasattr(self, '_configuration_changed'):
            self._configuration_changed = asyncio.Event()
        self._configuration_changed.set()

    def _log_disconnect(self, error: Exception) -> None:
        signature = type(error).__name__
        if signature == self._last_disconnect_error:
            self._disconnect_repeat_count += 1
        else:
            self._last_disconnect_error = signature
            self._disconnect_repeat_count = 1
        if self._disconnect_repeat_count == 1 or (
            self._disconnect_repeat_count % 20 == 0
        ):
            logger.warning(
                "onebot_websocket_disconnected error=%s repeated=%s",
                signature,
                self._disconnect_repeat_count,
            )

    def _notify_status(self, status: str, detail: str | None = None) -> None:
        if self._status_callback is None:
            return
        try:
            self._status_callback(status, detail)
        except Exception:
            logger.exception("onebot_status_callback_failed status=%s", status)

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
    ) -> dict[str, Any] | list[Any]:
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
                check_submission()
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
        return data if isinstance(data, (dict, list)) else {}

    async def _connect(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        parsed = urlsplit(self.url)
        token = self.token  # stable credentials for this handshake
        host = parsed.hostname
        if not host or parsed.scheme not in {"ws", "wss"}:
            raise ValueError("invalid OneBot WebSocket URL")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        context = ssl.create_default_context() if parsed.scheme == "wss" else None
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=context),
            timeout=self.connect_timeout_seconds,
        )
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
        if token:
            headers.append(f"Authorization: Bearer {token}")
        try:
            writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
            await asyncio.wait_for(
                writer.drain(),
                timeout=self.connect_timeout_seconds,
            )
            response = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"),
                timeout=self.connect_timeout_seconds,
            )
            self._validate_handshake(response, key)
        except BaseException:
            await self._close_writer(writer)
            raise
        return reader, writer

    @staticmethod
    def _validate_handshake(response: bytes, key: str) -> None:
        lines = response.decode("latin-1").split("\r\n")
        if lines and any(f' {code} ' in lines[0] for code in ('401', '403')):
            raise OneBotAuthenticationError('OneBot 鉴权失败，请检查 QQ_WS_TOKEN（不是 QQ_WEBUI_TOKEN）')
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
            try:
                final, opcode, payload = await asyncio.wait_for(
                    self._read_frame(reader),
                    timeout=self.ping_interval_seconds,
                )
            except TimeoutError:
                ping_payload = os.urandom(4)
                async with self._send_lock:
                    await self._send_frame(writer, 0x9, ping_payload)
                try:
                    final, opcode, payload = await asyncio.wait_for(
                        self._read_frame(reader),
                        timeout=self.ping_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise ConnectionError(
                        "NapCat WebSocket keepalive timed out"
                    ) from exc
            if opcode == 0x8:
                return
            if opcode == 0x9:
                async with self._send_lock:
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
    async def _close_writer(writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=3.0)
        except (OSError, TimeoutError):
            pass

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
