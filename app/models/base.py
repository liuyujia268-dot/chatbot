"""Unified model interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.core.session import Message


class ModelError(RuntimeError):
    """Base class for safe, user-facing model failures."""


class ModelConnectionError(ModelError):
    """The configured model service could not be reached."""


class ModelTimeoutError(ModelError):
    """The model call exceeded its configured timeout."""


class ModelAuthenticationError(ModelError):
    """The model service rejected the configured credentials."""


class ModelNotFoundError(ModelError):
    """The configured model or endpoint was not found."""


class ModelServiceError(ModelError):
    """The model service returned an unsuccessful status."""


class EmptyModelResponseError(ModelError):
    """The model returned no usable text."""


class InvalidModelResponseError(ModelError):
    """The model response did not match the expected schema."""


class ChatModel(ABC):
    @abstractmethod
    async def generate(self, messages: Sequence[Message]) -> str:
        """Generate one assistant response from conversation messages."""
