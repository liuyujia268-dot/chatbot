"""Asynchronous client for OpenAI-compatible Chat Completions providers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

import httpx

from app.core.session import Message
from app.models.base import (
    ChatModel,
    EmptyModelResponseError,
    InvalidModelResponseError,
    ModelAuthenticationError,
    ModelConnectionError,
    ModelNotFoundError,
    ModelNetworkConfigurationError,
    ModelServiceError,
    ModelTimeoutError,
)


logger = logging.getLogger(__name__)


class AsyncHTTPClient(Protocol):
    async def post(self, url: str, **kwargs: Any) -> Any: ...


class OpenAICompatibleModel(ChatModel):
    """One provider implementation shared by Ollama, Packy, and similar APIs."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout_seconds: float,
        max_output_tokens: int,
        temperature: float | None = None,
        proxy_mode: str = "system",
        proxy_url: str | None = None,
        client: AsyncHTTPClient | None = None,
    ) -> None:
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._proxy_mode = proxy_mode
        self._proxy_url = proxy_url
        self._client = client

    async def generate(self, messages: Sequence[Message]) -> str:
        payload = {
            "model": self._model_name,
            "messages": [
                {
                    "role": message.role,
                    "content": self._serialize_content(message),
                }
                for message in messages
            ],
            "max_tokens": self._max_output_tokens,
            "stream": False,
        }
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._post(payload, headers)
        except asyncio.CancelledError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            logger.warning(
                "model_timeout provider=openai_compatible model=%s timeout=%s",
                self._model_name,
                self._timeout_seconds,
            )
            raise ModelTimeoutError("模型请求超时，请稍后重试。") from None
        except (ImportError, ValueError, OSError) as exc:
            # Client construction can fail before post() (SOCKS/CA/proxy URL).
            # Never log the raw exception: proxy credentials may be inside it.
            logger.warning(
                "model_network_configuration_failed proxy_mode=%s error_type=%s",
                self._proxy_mode, type(exc).__name__,
            )
            raise ModelNetworkConfigurationError(
                "模型网络配置失败；请在设置检查模型网络模式和代理地址。"
                "SOCKS 组件缺失时请使用完整新版发行包；可直连时选择 direct 后重连。"
            ) from None
        except httpx.ConnectError as exc:
            logger.warning(
                "model_connection_failed provider=openai_compatible model=%s endpoint=%s error_type=%s",
                self._model_name,
                self._endpoint,
                type(exc).__name__,
            )
            raise ModelConnectionError(
                "无法连接模型服务，请确认服务已启动且地址正确。"
            ) from None
        except httpx.RequestError as exc:
            logger.warning(
                "model_request_failed provider=openai_compatible model=%s endpoint=%s error_type=%s",
                self._model_name,
                self._endpoint,
                type(exc).__name__,
            )
            raise ModelConnectionError("模型服务请求失败，请检查网络和服务地址。") from None

        self._raise_for_status(response)
        return self._parse_content(response)

    async def _post(self, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        if self._client is not None:
            return await self._client.post(
                self._endpoint,
                json=payload,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        if self._proxy_mode not in {"system", "direct", "custom"}:
            raise ValueError("Unknown model proxy mode")
        options: dict[str, Any] = {"trust_env": self._proxy_mode == "system"}
        if self._proxy_mode == "custom":
            if not self._proxy_url:
                raise ValueError("Custom model proxy is missing")
            options["proxy"] = self._proxy_url
        async with httpx.AsyncClient(**options) as client:
            return await client.post(
                self._endpoint,
                json=payload,
                headers=headers,
                timeout=self._timeout_seconds,
            )

    def _raise_for_status(self, response: Any) -> None:
        try:
            status_code = int(response.status_code)
        except (AttributeError, TypeError, ValueError) as exc:
            raise InvalidModelResponseError("模型服务返回了无法识别的响应。") from exc
        if status_code < 400:
            return

        detail = self._safe_error_detail(response)
        logger.error(
            "model_http_error provider=openai_compatible model=%s status=%s detail=%s",
            self._model_name,
            status_code,
            detail,
        )
        if status_code in {401, 403}:
            raise ModelAuthenticationError("模型服务认证失败，请检查 API Key。")
        if status_code == 404:
            raise ModelNotFoundError("模型不存在，或 MODEL_BASE_URL 配置不正确。")
        raise ModelServiceError(f"模型服务返回异常（HTTP {status_code}）。")

    def _safe_error_detail(self, response: Any) -> str:
        detail = ""
        try:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                detail = str(error.get("message", ""))
            elif error is not None:
                detail = str(error)
        except (ValueError, TypeError, AttributeError):
            detail = str(getattr(response, "text", ""))
        secrets = [self._api_key, self._proxy_url]
        if self._proxy_url:
            try:
                proxy = urlsplit(self._proxy_url)
                for credential in (proxy.username, proxy.password):
                    if credential:
                        secrets.extend((credential, unquote(credential)))
            except ValueError:
                pass
        for secret in secrets:
            if secret:
                detail = detail.replace(secret, "***")
        return detail[:500]

    @staticmethod
    def _parse_content(response: Any) -> str:
        try:
            body = response.json()
        except (ValueError, TypeError, AttributeError) as exc:
            raise InvalidModelResponseError("模型服务返回的内容不是有效 JSON。") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise InvalidModelResponseError("模型服务返回格式不符合预期。") from exc

        if isinstance(content, list):
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        if not isinstance(content, str):
            raise InvalidModelResponseError("模型回复不是文本内容。")
        content = content.strip()
        if not content:
            raise EmptyModelResponseError("模型返回了空回复，请重试。")
        return content

    @staticmethod
    def _serialize_content(message: Message) -> Any:
        if isinstance(message.content, str):
            return message.content
        content: list[dict[str, Any]] = []
        for part in message.content:
            if part.part_type == "text":
                content.append({"type": "text", "text": part.text or ""})
                continue
            image_url = part.model_image_url
            if image_url:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    }
                )
            else:
                content.append({"type": "text", "text": part.fallback_text})
        return content
