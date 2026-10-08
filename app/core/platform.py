"""Platform-neutral inbound and outbound message values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal, Mapping

from app.core.identity import conversation_key


@dataclass(frozen=True, slots=True)
class MessagePart:
    part_type: Literal["text", "image"]
    text: str | None = None
    file: str | None = None
    url: str | None = None
    summary: str | None = None
    file_size: int | None = None
    sub_type: int | str | None = None
    resolved_url: str | None = None
    description: str | None = None
    source_platform: str | None = None
    source_account_id: str | None = None
    source_message_id: str | None = None
    part_index: int | None = None
    available: bool = True

    def __post_init__(self) -> None:
        if self.part_type == "text":
            if not self.text or not self.text.strip():
                raise ValueError("text part must not be empty")
        elif not (self.url or self.file):
            raise ValueError("image part requires url or file")
        if self.file_size is not None and self.file_size < 0:
            raise ValueError("image file_size must not be negative")

    @classmethod
    def text_part(cls, text: str) -> "MessagePart":
        return cls(part_type="text", text=text)

    @property
    def model_image_url(self) -> str | None:
        if not self.available:
            return None
        return self.resolved_url or self.url

    @property
    def fallback_text(self) -> str:
        if self.part_type == "text":
            return self.text or ""
        detail = self.description or self.summary
        return f"[图片：{detail}]" if detail else "[图片]"


@dataclass(frozen=True, slots=True)
class PlatformMessage:
    platform: str
    account_id: str
    conversation_id: str
    sender_id: str
    message_id: str
    timestamp: datetime
    message_type: str
    text: str
    is_self: bool = False
    parts: tuple[MessagePart, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "platform",
            "account_id",
            "conversation_id",
            "sender_id",
            "message_id",
            "message_type",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        normalized_parts = tuple(self.parts)
        if not normalized_parts and self.text.strip():
            normalized_parts = (MessagePart.text_part(self.text),)
        if not normalized_parts:
            raise ValueError("platform message must contain text or message parts")
        object.__setattr__(self, "parts", normalized_parts)
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must include timezone information")

    @property
    def deduplication_key(self) -> tuple[str, str, str]:
        return self.platform, self.account_id, self.message_id

    @property
    def session_id(self) -> str:
        return conversation_key(self.platform, self.account_id, self.conversation_id)

    @property
    def storage_text(self) -> str:
        return "\n".join(part.fallback_text for part in self.parts)

    @property
    def has_images(self) -> bool:
        return any(part.part_type == "image" for part in self.parts)


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    platform: str
    account_id: str
    conversation_id: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        return conversation_key(self.platform, self.account_id, self.conversation_id)

    def __post_init__(self) -> None:
        if not self.platform.strip() or not self.account_id.strip():
            raise ValueError("outbound platform and account_id must not be empty")
        if not self.conversation_id.strip() or not self.text.strip():
            raise ValueError("outbound conversation and text must not be empty")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
