from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

from app.core.dispatcher import ContinuousMessageMerger, MessageDispatcher
from app.core.memory import InMemoryStore
from app.core.reply_engine import ReplyEngine
from app.core.session import Message, SessionManager
from app.models.base import ChatModel
from app.models.mock import MockModel
from app.storage.database import Database


class RecordingModel(ChatModel):
    def __init__(self) -> None:
        self.calls: list[tuple[Message, ...]] = []

    async def generate(self, messages: Sequence[Message]) -> str:
        self.calls.append(tuple(messages))
        return "recorded"


def make_dispatcher(
    path: Path, model: ChatModel | None = None
) -> tuple[MessageDispatcher, SessionManager]:
    database = Database(path)
    database.initialize()
    sessions = SessionManager()
    dispatcher = MessageDispatcher(
        sessions=sessions,
        engine=ReplyEngine(model or MockModel()),
        memory=InMemoryStore(),
        database=database,
    )
    return dispatcher, sessions


def test_contacts_keep_independent_contexts(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = RecordingModel()
        dispatcher, _ = make_dispatcher(tmp_path / "chat.db", model)

        await dispatcher.dispatch("张三", "张三的第一条")
        await dispatcher.dispatch("李四", "李四的第一条")
        await dispatcher.dispatch("张三", "张三的第二条")

        zhang_user_messages = [
            message.content for message in model.calls[2] if message.role == "user"
        ]
        assert zhang_user_messages == ["张三的第一条", "张三的第二条"]
        assert all("李四" not in content for content in zhang_user_messages)

    asyncio.run(scenario())


def test_messages_and_replies_are_saved_to_sqlite(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "chat.db"
        dispatcher, _ = make_dispatcher(database_path)

        reply = await dispatcher.dispatch("张三", "你好")

        messages = Database(database_path).list_messages("张三")
        assert reply == MockModel.DEFAULT_REPLY
        assert [(item.role, item.content) for item in messages] == [
            ("user", "你好"),
            ("assistant", MockModel.DEFAULT_REPLY),
        ]

    asyncio.run(scenario())


def test_continuous_messages_are_merged_per_contact(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, _ = make_dispatcher(tmp_path / "chat.db")
        merger = ContinuousMessageMerger(dispatcher, merge_window_seconds=0.02)

        first = asyncio.create_task(merger.submit("张三", "第一条"))
        await asyncio.sleep(0.005)
        second = asyncio.create_task(merger.submit("张三", "第二条"))
        first_result, second_result = await asyncio.gather(first, second)

        assert first_result is None
        assert second_result is not None
        assert second_result.message_count == 2
        assert second_result.merged_content == "第一条\n第二条"
        stored = Database(tmp_path / "chat.db").list_messages("张三")
        assert [(item.role, item.content) for item in stored] == [
            ("user", "第一条"),
            ("user", "第二条"),
            ("assistant", MockModel.DEFAULT_REPLY),
        ]

    asyncio.run(scenario())


def test_human_takeover_disables_only_that_sessions_reply(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "chat.db"
        dispatcher, sessions = make_dispatcher(database_path)
        dispatcher.set_human_takeover("张三", True)

        zhang_reply = await dispatcher.dispatch("张三", "人工处理中")
        li_reply = await dispatcher.dispatch("李四", "仍然自动回复")

        assert zhang_reply is None
        assert li_reply == MockModel.DEFAULT_REPLY
        assert sessions.get_or_create("张三").human_takeover is True
        assert sessions.get_or_create("李四").human_takeover is False
        assert [item.role for item in Database(database_path).list_messages("张三")] == ["user"]

        dispatcher.set_human_takeover("张三", False)
        assert await dispatcher.dispatch("张三", "恢复") == MockModel.DEFAULT_REPLY

    asyncio.run(scenario())
