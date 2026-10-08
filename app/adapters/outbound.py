"""Outbound sending boundary with a safe dry-run wrapper."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from app.core.platform import OutboundMessage


logger = logging.getLogger(__name__)

_SENTENCE_ENDINGS = frozenset("。！？!?")
_SENTENCE_CLOSERS = frozenset("”’」』】）》）\"'")


def split_outbound_text(text: str) -> tuple[str, ...]:
    """Split chat prose at sentence endings and explicit line breaks."""
    source = text.strip()
    if not source:
        return ()

    parts: list[str] = []
    current: list[str] = []
    boundary_pending = False

    def flush() -> None:
        value = "".join(current).strip()
        if value:
            parts.append(value)
        current.clear()

    for character in source:
        if character in "\r\n":
            flush()
            boundary_pending = False
            continue
        if boundary_pending:
            if character in _SENTENCE_ENDINGS or character in _SENTENCE_CLOSERS:
                current.append(character)
                continue
            if character.isspace():
                flush()
                boundary_pending = False
                continue
            flush()
            boundary_pending = False
        current.append(character)
        if character in _SENTENCE_ENDINGS:
            boundary_pending = True

    flush()
    return tuple(parts)


class MessageSender(Protocol):
    async def send(self, message: OutboundMessage) -> str | None: ...


class OneBotActionClient(Protocol):
    async def call_action(
        self,
        action: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any] | list[Any]: ...


class ConsoleMessageSender:
    def __init__(self, sink=print) -> None:
        self._sink = sink

    async def send(self, message: OutboundMessage) -> None:
        count = int(message.metadata.get("message_count", 1))
        suffix = f"（合并 {count} 条消息）" if count > 1 else ""
        self._sink(f"[{message.conversation_id}] 机器人{suffix}: {message.text}")


class DryRunMessageSender:
    """Record outbound messages and never invoke the wrapped sender."""

    def __init__(self, wrapped: MessageSender) -> None:
        self._wrapped = wrapped
        self.records: list[OutboundMessage] = []
        self.dry_run = True

    async def send(self, message: OutboundMessage) -> None:
        self.records.append(message)
        logger.info(
            "dry_run_outbound platform=%s account=%s conversation=%s chars=%s",
            message.platform,
            message.account_id,
            message.conversation_id,
            len(message.text),
        )


class NapCatMessageSender:
    """Send text and explicitly attached local images through OneBot."""

    def __init__(
        self,
        client: OneBotActionClient,
        *,
        on_sent: Callable[[str, str], None] | None = None,
    ) -> None:
        self._client = client
        self._on_sent = on_sent

    async def send(self, message: OutboundMessage) -> str | None:
        prefix, separator, target_id = message.conversation_id.rpartition(":")
        if not separator or not target_id:
            raise ValueError("invalid QQ conversation_id")
        payload = [{"type": "text", "data": {"text": message.text}}]
        image_paths = message.metadata.get("local_image_paths", ())
        if isinstance(image_paths, (str, Path)):
            image_paths = (image_paths,)
        if not isinstance(image_paths, (tuple, list)):
            raise ValueError("local_image_paths must be a sequence")
        for raw_path in image_paths:
            image_path = Path(raw_path).resolve()
            if image_path.suffix.lower() not in {
                ".gif",
                ".jpeg",
                ".jpg",
                ".png",
                ".webp",
            }:
                raise ValueError("unsupported outbound image type")
            if not image_path.is_file():
                raise ValueError("outbound image does not exist")
            payload.append(
                {
                    "type": "image",
                    "data": {"file": image_path.as_uri()},
                }
            )
        if prefix == "qq:private":
            action = "send_private_msg"
            params = {"user_id": target_id, "message": payload}
        elif prefix == "qq:group":
            action = "send_group_msg"
            params = {"group_id": target_id, "message": payload}
        else:
            raise ValueError("unsupported QQ conversation_id")
        data = await self._client.call_action(action, params)
        message_id = str(data.get("message_id", "")).strip() if isinstance(data, dict) else ""
        if message_id and self._on_sent is not None:
            self._on_sent(message.account_id, message_id)
        logger.info(
            "qq_message_sent conversation=%s chars=%s",
            message.conversation_id,
            len(message.text),
        )
        return message_id or None
