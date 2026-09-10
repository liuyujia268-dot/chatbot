"""Structured model context assembly helpers and safe debug output."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.core.session import Message
from app.memory.service import MemoryService
from app.memory.types import AccountProfile, ContactProfile, MemoryContext


PRIVATE_REDACTION = "[私密账号信息已隐藏]"


def redact_private_information(text: str, private_information: str | None) -> str:
    """Remove the configured private body and each non-empty line from text."""
    if not private_information:
        return text
    values = {
        private_information,
        *(line.strip() for line in private_information.splitlines() if line.strip()),
    }
    redacted = text
    for value in sorted(values, key=len, reverse=True):
        redacted = redacted.replace(value, PRIVATE_REDACTION)
    return redacted


@dataclass(frozen=True, slots=True)
class ModelContextSnapshot:
    conversation_id: str
    model_messages: tuple[Message, ...]
    recent_history: tuple[Message, ...]
    current_message: Message | None
    memory_context: MemoryContext | None


class MemoryContextProvider:
    def __init__(self, service: MemoryService) -> None:
        self._service = service

    def load(self, conversation_id: str, current_text: str) -> MemoryContext:
        return self._service.context_for(conversation_id, current_text)

    @staticmethod
    def system_messages(context: MemoryContext) -> tuple[Message, ...]:
        messages: list[Message] = []
        account = _format_account_profile(context.account_profile)
        if account:
            messages.append(Message("system", "【当前账号资料】\n" + account))
        messages.append(
            Message("system", "【当前联系人】\n" + context.conversation_id)
        )
        profile = _format_contact_profile(context.contact_profile)
        if profile:
            messages.append(Message("system", "【当前联系人画像】\n" + profile))
        if context.memories:
            lines = [
                (
                    f"- [memory_id={memory.id}; type={memory.memory_type}; "
                    f"source={memory.source_start_message_id}-"
                    f"{memory.source_end_message_id}] {memory.content}"
                )
                for memory in context.memories
            ]
            messages.append(
                Message("system", "【相关长期记忆】\n" + "\n".join(lines))
            )
        if context.summary:
            summary = context.summary
            messages.append(
                Message(
                    "system",
                    "【当前话题摘要】\n"
                    f"[summary_id={summary.id}; source="
                    f"{summary.start_message_id}-{summary.end_message_id}] "
                    f"{summary.content}",
                )
            )
        return tuple(messages)


def _format_contact_profile(profile: ContactProfile | None) -> str:
    if profile is None:
        return ""
    labels = {
        "preferred_name": "常用称呼",
        "relationship": "关系",
        "communication_style": "交流风格",
        "stable_preferences": "已确认稳定偏好",
        "notes": "备注",
    }
    lines = [
        f"- {labels[field]}：{getattr(profile, field)}"
        for field in labels
        if getattr(profile, field)
    ]
    return "\n".join(lines)


def _format_account_profile(profile: AccountProfile | None) -> str:
    if profile is None:
        return ""
    lines: list[str] = []
    if profile.public_name:
        lines.append(f"- 对外称呼：{profile.public_name}")
    if profile.background:
        lines.append(f"- 已配置基础背景：{profile.background}")
    if profile.public_information:
        lines.append(f"- 可公开信息：{profile.public_information}")
    if profile.private_information:
        lines.append(
            "- 已配置仅供程序保管的私密账号信息；"
            "模型不可访问其正文"
        )
    return "\n".join(lines)


class ContextDebugger:
    def __init__(
        self,
        *,
        enabled: bool = False,
        redacted_values: Sequence[str] = (),
        sink: Callable[[str], None] = print,
    ) -> None:
        self.enabled = enabled
        self._redacted_values = tuple(value for value in redacted_values if value)
        self._sink = sink

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def emit(self, snapshot: ModelContextSnapshot) -> None:
        if self.enabled:
            self._sink(self.render(snapshot))

    def render(self, snapshot: ModelContextSnapshot) -> str:
        context = snapshot.memory_context
        lines = [f"[DEBUG 上下文] conversation={snapshot.conversation_id}"]
        lines.append(f"最近聊天（{len(snapshot.recent_history)} 条）：")
        lines.extend(
            f"  - {message.role}: {message.content}"
            for message in snapshot.recent_history
        )
        if snapshot.current_message:
            lines.append(
                "当前消息："
                f"{snapshot.current_message.role}: {snapshot.current_message.content}"
            )
        if context and context.summary:
            summary = context.summary
            lines.append(
                f"摘要：#{summary.id} source="
                f"{summary.start_message_id}-{summary.end_message_id} "
                f"{summary.content}"
            )
        else:
            lines.append("摘要：无")
        memories = context.memories if context else ()
        lines.append(f"长期记忆（{len(memories)} 条）：")
        lines.extend(
            f"  - #{memory.id} {memory.memory_type}/{memory.memory_key}: "
            f"{memory.content} source={memory.source_start_message_id}-"
            f"{memory.source_end_message_id}"
            for memory in memories
        )
        profile = context.contact_profile if context else None
        profile_fields = [
            field
            for field in (
                "preferred_name",
                "relationship",
                "communication_style",
                "stable_preferences",
                "notes",
            )
            if profile and getattr(profile, field)
        ]
        lines.append(
            "联系人画像字段：" + (", ".join(profile_fields) or "无")
        )
        account = context.account_profile if context else None
        account_fields = [
            field
            for field in ("public_name", "background", "public_information")
            if account and getattr(account, field)
        ]
        if account and account.private_information:
            account_fields.append("private_information=<内容隐藏>")
        lines.append("当前账号字段：" + (", ".join(account_fields) or "无"))
        rendered = "\n".join(lines)
        private_information = (
            context.account_profile.private_information
            if context and context.account_profile
            else None
        )
        rendered = redact_private_information(rendered, private_information)
        for secret in self._redacted_values:
            rendered = rendered.replace(secret, "***")
        return rendered
