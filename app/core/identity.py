"""Separate internal account-scoped identity from platform routing targets."""

from __future__ import annotations


def conversation_key(platform: str, account_id: str, conversation_id: str) -> str:
    if not platform or not account_id or not conversation_id:
        raise ValueError("platform, account_id and conversation_id are required")
    if ":" in platform or ":" in account_id:
        raise ValueError("platform and account_id must not contain ':'")
    return f"scope:{platform}:{account_id}:{conversation_id}"


def parse_conversation_key(value: str) -> tuple[str, str, str] | None:
    if not value.startswith("scope:"):
        return None
    parts = value.split(":", 3)
    if len(parts) != 4 or not all(parts[1:]):
        raise ValueError("invalid scoped conversation identifier")
    return parts[1], parts[2], parts[3]


def account_key_for(conversation_id: str) -> str | None:
    parts = parse_conversation_key(conversation_id)
    return f"{parts[0]}:{parts[1]}" if parts else None


def platform_conversation_id(value: str) -> str:
    parts = parse_conversation_key(value)
    return parts[2] if parts else value
