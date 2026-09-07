"""Reserved OpenAI model adapter; intentionally not connected yet."""

from __future__ import annotations

from collections.abc import Sequence
from app.core.session import Message
from app.models.base import ChatModel


class OpenAIChatModel(ChatModel):
    async def generate(self, messages: Sequence[Message]) -> str:
        del messages
        raise NotImplementedError("Real AI API integration is intentionally disabled")
