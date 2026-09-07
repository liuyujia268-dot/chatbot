"""Model provider selection."""

from __future__ import annotations

from app.config import ConfigurationError, Settings
from app.models.base import ChatModel
from app.models.mock import MockModel
from app.models.openai import AsyncHTTPClient, OpenAICompatibleModel


def build_model(
    settings: Settings,
    *,
    client: AsyncHTTPClient | None = None,
) -> ChatModel:
    if settings.model_provider == "mock":
        return MockModel()
    if settings.model_provider == "openai_compatible":
        if not (
            settings.model_base_url
            and settings.model_api_key
            and settings.model_name
        ):
            raise ConfigurationError("OpenAI-compatible 模型配置不完整")
        return OpenAICompatibleModel(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key,
            model_name=settings.model_name,
            timeout_seconds=settings.model_timeout_seconds,
            max_output_tokens=settings.model_max_tokens,
            temperature=settings.model_temperature,
            client=client,
        )
    raise ConfigurationError(f"不支持的 MODEL_PROVIDER：{settings.model_provider}")
