from __future__ import annotations

import asyncio
from collections.abc import Sequence

from app.core.reply_engine import ReplyEngine
from app.core.session import Message, Session
from app.models.base import ChatModel
from app.models.mock import MockModel


class CapturingModel(ChatModel):
    def __init__(self, reply: str = "captured") -> None:
        self.reply = reply
        self.messages: tuple[Message, ...] = ()

    async def generate(self, messages: Sequence[Message]) -> str:
        self.messages = tuple(messages)
        return self.reply


def test_mock_model_returns_fixed_reply() -> None:
    async def scenario() -> None:
        session = Session("test")
        session.add_message("user", "任意消息")
        engine = ReplyEngine(MockModel())

        first = await engine.generate(session)
        session.add_message("user", "完全不同的消息")
        second = await engine.generate(session)

        assert first == MockModel.DEFAULT_REPLY
        assert second == MockModel.DEFAULT_REPLY

    asyncio.run(scenario())


def test_reply_engine_sends_system_prompt_and_only_recent_history() -> None:
    async def scenario() -> None:
        model = CapturingModel()
        session = Session("test")
        session.add_message("user", "old user")
        session.add_message("assistant", "old assistant")
        session.add_message("user", "recent user")
        session.add_message("assistant", "recent assistant")
        session.add_message("user", "current user")
        engine = ReplyEngine(
            model,
            system_prompt="system prompt",
            context_max_messages=3,
        )

        await engine.generate(session)

        assert [(item.role, item.content) for item in model.messages] == [
            ("system", "system prompt"),
            ("user", "recent user"),
            ("assistant", "recent assistant"),
            ("user", "current user"),
        ]
        assert sum(
            item.content == "current user" for item in model.messages
        ) == 1

    asyncio.run(scenario())


def test_context_limit_excludes_stored_history_without_losing_roles() -> None:
    model = CapturingModel()
    session = Session("test")
    for index in range(10):
        role = "user" if index % 2 == 0 else "assistant"
        session.add_message(role, f"message-{index}")
    engine = ReplyEngine(model, context_max_messages=4)

    messages = engine.build_model_messages(session)

    assert [(item.role, item.content) for item in messages] == [
        ("user", "message-6"),
        ("assistant", "message-7"),
        ("user", "message-8"),
        ("assistant", "message-9"),
    ]


def test_reply_engine_truncates_abnormally_long_output() -> None:
    async def scenario() -> None:
        model = CapturingModel(reply="x" * 50)
        session = Session("test")
        session.add_message("user", "hello")

        reply = await ReplyEngine(model, max_output_chars=10).generate(session)

        assert reply == "x" * 9 + "…"
        assert len(reply) == 10

    asyncio.run(scenario())
