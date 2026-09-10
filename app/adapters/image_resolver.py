"""Resolve QQ image parts safely for an OpenAI-compatible vision request."""

from __future__ import annotations

import base64
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.platform import MessagePart


class ImageResolutionError(RuntimeError):
    """A remote image cannot safely be used by the model."""


class ImageResolver:
    def __init__(
        self,
        *,
        max_bytes: int,
        timeout_seconds: float,
        allow_remote_url: bool,
        client: Any | None = None,
    ) -> None:
        if max_bytes < 1 or timeout_seconds <= 0:
            raise ValueError("invalid image resolver limits")
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.allow_remote_url = allow_remote_url
        self._client = client

    async def resolve(self, part: MessagePart) -> MessagePart:
        if part.part_type != "image":
            return part
        if part.file_size is not None and part.file_size > self.max_bytes:
            raise ImageResolutionError("图片超过大小限制")
        url = self._safe_url(part.url)
        if self.allow_remote_url:
            return replace(part, resolved_url=url)
        mime, content = await self._download(url)
        data_url = self._temporary_data_url(mime, content)
        return replace(part, resolved_url=data_url)

    @staticmethod
    def unavailable(part: MessagePart) -> MessagePart:
        return replace(
            part,
            resolved_url=None,
            description="图片加载失败，模型未看到图片",
            available=False,
        )

    @staticmethod
    def _safe_url(value: str | None) -> str:
        if not value:
            raise ImageResolutionError("图片没有可访问的 URL")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ImageResolutionError("图片 URL 不受支持")
        if parsed.username or parsed.password:
            raise ImageResolutionError("图片 URL 不得包含认证信息")
        return value

    async def _download(self, url: str) -> tuple[str, bytes]:
        client = self._client or httpx.AsyncClient()
        owns_client = self._client is None
        try:
            async with client.stream(
                "GET",
                url,
                timeout=self.timeout_seconds,
                follow_redirects=True,
            ) as response:
                response.raise_for_status()
                mime = response.headers.get("content-type", "").split(";", 1)[0]
                if not mime.lower().startswith("image/"):
                    raise ImageResolutionError("远程内容不是图片")
                length = response.headers.get("content-length")
                if length and int(length) > self.max_bytes:
                    raise ImageResolutionError("图片超过大小限制")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise ImageResolutionError("图片超过大小限制")
                    chunks.append(chunk)
                if total == 0:
                    raise ImageResolutionError("下载到的图片为空")
                return mime, b"".join(chunks)
        except ImageResolutionError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            raise ImageResolutionError("图片下载失败") from exc
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def _temporary_data_url(mime: str, content: bytes) -> str:
        with tempfile.TemporaryDirectory(prefix="chatbot-qq-image-") as folder:
            path = Path(folder) / "image.bin"
            path.write_bytes(content)
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"
