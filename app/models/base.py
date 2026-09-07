"""Unified model interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.core.session import Message


class ChatModel(ABC):
    @abstractmethod
    async def generate(self, messages: Sequence[Message]) -> str:
        """Generate one assistant response from conversation messages."""

