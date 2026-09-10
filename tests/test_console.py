from __future__ import annotations

import asyncio

import pytest

from app.adapters.console import (
    ConsoleAdapter,
    parse_contact_message,
    parse_takeover_command,
)
from app.core.platform import OutboundMessage


class RecordingSender:
    def __init__(self) -> None:
        self.calls: list[OutboundMessage] = []

    async def send(self, message: OutboundMessage) -> None:
        self.calls.append(message)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("[张三] 你好", ("张三", "你好")),
        (" [李四]   在吗？ ", ("李四", "在吗？")),
    ],
)
def test_parse_contact_message(line: str, expected: tuple[str, str]) -> None:
    assert parse_contact_message(line) == expected


def test_parse_contact_message_rejects_invalid_format() -> None:
    with pytest.raises(ValueError):
        parse_contact_message("张三：你好")


def test_parse_takeover_command() -> None:
    assert parse_takeover_command("/takeover 张三 on") == ("张三", True)
    assert parse_takeover_command("/takeover 张三 OFF") == ("张三", False)
    assert parse_takeover_command("[张三] hello") is None


def test_qq_console_sends_operator_text_directly_to_primary_contact() -> None:
    async def scenario() -> None:
        sender = RecordingSender()
        console = ConsoleAdapter(
            dispatcher=None,  # type: ignore[arg-type]
            merger=None,  # type: ignore[arg-type]
            qq_mode=True,
            qq_sender=sender,
            qq_dry_run=False,
            qq_primary_contact_id="20002",
            qq_primary_contact_name="宇飞",
        )

        message = await console.send_qq_message("宇飞", "这是人工发送")

        assert message is not None
        assert message.conversation_id == "qq:private:20002"
        assert message.metadata["source_type"] == "human_self"
        assert sender.calls == [message]

    asyncio.run(scenario())


def test_qq_console_supports_numeric_private_and_prefixed_group_targets() -> None:
    async def scenario() -> None:
        sender = RecordingSender()
        console = ConsoleAdapter(
            dispatcher=None,  # type: ignore[arg-type]
            merger=None,  # type: ignore[arg-type]
            qq_mode=True,
            qq_sender=sender,
        )

        await console.send_qq_message("30003", "私聊")
        await console.send_qq_message("群:80001", "群消息")

        assert [item.conversation_id for item in sender.calls] == [
            "qq:private:30003",
            "qq:group:80001",
        ]

    asyncio.run(scenario())
