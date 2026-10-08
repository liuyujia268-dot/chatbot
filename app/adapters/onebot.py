"""Pure OneBot 11 message-event parsing for NapCat."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.platform import MessagePart, PlatformMessage


@dataclass(frozen=True, slots=True)
class ParsedQQEvent:
    message: PlatformMessage
    conversation_type: str
    peer_id: str
    group_id: str | None
    nickname: str | None
    reply_to_message_id: str | None
    mentions_self: bool


def parse_onebot_event(event: dict[str, Any]) -> ParsedQQEvent | None:
    post_type = str(event.get("post_type", ""))
    message_type = str(event.get("message_type", ""))
    if post_type not in {"message", "message_sent"}:
        return None
    if message_type not in {"private", "group"}:
        return None
    account_id = _identifier(event.get("self_id"))
    event_user_id = _identifier(event.get("user_id"))
    message_id = _identifier(event.get("message_id"))
    if not account_id or not event_user_id or not message_id:
        return None
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    declared_sender_id = _identifier(sender.get("user_id"))
    is_self = (
        post_type == "message_sent"
        or event_user_id == account_id
        or declared_sender_id == account_id
    )
    group_id = _identifier(event.get("group_id")) if message_type == "group" else None
    if message_type == "group" and not group_id:
        return None
    peer_id = event_user_id
    if message_type == "private" and is_self and peer_id == account_id:
        peer_id = (
            _identifier(event.get("target_id"))
            or _identifier(event.get("peer_id"))
        )
        if not peer_id or peer_id == account_id:
            # NapCat occasionally reports a sent private event without its
            # peer. Treating self_id as the peer creates a fake self-chat and
            # later makes send_private_msg target the logged-in account.
            return None
    sender_id = account_id if is_self else (declared_sender_id or event_user_id)
    conversation_id = (
        f"qq:group:{group_id}"
        if group_id is not None
        else f"qq:private:{peer_id}"
    )
    parts, reply_to, mentioned_user_ids = _message_parts(
        event.get("message"),
        account_id=account_id,
        message_id=message_id,
    )
    if not parts:
        return None
    text = "".join(part.text or "" for part in parts if part.part_type == "text")
    kinds = {part.part_type for part in parts}
    platform_message = PlatformMessage(
        platform="qq",
        account_id=account_id,
        conversation_id=conversation_id,
        sender_id=sender_id,
        message_id=message_id,
        timestamp=_timestamp(event.get("time")),
        message_type="mixed" if len(kinds) > 1 else next(iter(kinds)),
        text=text,
        is_self=is_self,
        parts=parts,
    )
    nickname = str(sender.get("card") or sender.get("nickname") or "").strip() or None
    return ParsedQQEvent(
        message=platform_message,
        conversation_type=message_type,
        peer_id=peer_id,
        group_id=group_id,
        nickname=nickname,
        reply_to_message_id=reply_to,
        mentions_self=account_id in mentioned_user_ids,
    )


def _identifier(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _timestamp(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return datetime.now(tz=UTC)


def _message_parts(
    value: Any,
    *,
    account_id: str,
    message_id: str,
) -> tuple[tuple[MessagePart, ...], str | None, tuple[str, ...]]:
    segments = value if isinstance(value, list) else [
        {"type": "text", "data": {"text": str(value or "")}}
    ]
    parts: list[MessagePart] = []
    reply_to: str | None = None
    mentioned_user_ids: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        kind = str(segment.get("type", ""))
        data = segment.get("data")
        if not isinstance(data, dict):
            continue
        if kind == "reply":
            reply_to = _identifier(data.get("id")) or reply_to
            continue
        if kind == "at":
            target = _identifier(data.get("qq"))
            if target:
                mentioned_user_ids.append(target)
                label = "当前账号" if target == account_id else target
                parts.append(MessagePart.text_part(f"[@{label}]"))
            continue
        if kind == "face":
            parts.append(MessagePart.text_part(_face_text(data)))
            continue
        if kind == "mface":
            label = _optional_text(data.get("summary"))
            parts.append(
                MessagePart.text_part(
                    f"[QQ商城表情：{label}]" if label else "[QQ商城表情]"
                )
            )
            continue
        if kind == "text":
            text = str(data.get("text", ""))
            if text.strip():
                parts.append(MessagePart.text_part(text))
            continue
        if kind != "image":
            continue
        file = _optional_text(data.get("file"))
        url = _optional_text(data.get("url"))
        if not (file or url):
            continue
        parts.append(
            MessagePart(
                part_type="image",
                file=file,
                url=url,
                summary=_optional_text(data.get("summary")),
                file_size=_optional_int(data.get("file_size")),
                sub_type=data.get("sub_type"),
                source_platform="qq",
                source_account_id=account_id,
                source_message_id=message_id,
                part_index=len(parts),
            )
        )
    return tuple(parts), reply_to, tuple(mentioned_user_ids)


def _optional_text(value: Any) -> str | None:
    result = str(value).strip() if value is not None else ""
    return result or None


def _optional_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _face_text(data: dict[str, Any]) -> str:
    label = next(
        (
            value
            for key in ("summary", "name", "text", "faceText")
            if (value := _optional_text(data.get(key)))
        ),
        None,
    )
    raw = data.get("raw")
    if label is None and isinstance(raw, dict):
        label = next(
            (
                value
                for key in ("summary", "name", "text", "faceText")
                if (value := _optional_text(raw.get(key)))
            ),
            None,
        )
    face_id = _identifier(data.get("id"))
    content = (
        f"[QQ表情：{label}]"
        if label
        else f"[QQ表情 id={face_id or 'unknown'}]"
    )
    chain_count = _optional_int(data.get("chainCount"))
    return f"{content}×{chain_count}" if chain_count and chain_count > 1 else content
