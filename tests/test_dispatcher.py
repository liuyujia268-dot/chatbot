from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.core.dispatcher import ContinuousMessageMerger, MessageDispatcher
from app.core.memory import InMemoryStore
from app.core.reply_engine import ReplyEngine
from app.core.session import Message, SessionManager
from app.models.base import ChatModel, ModelConnectionError
from app.models.mock import MockModel
from app.storage.database import Database


class RecordingModel(ChatModel):
    def __init__(self) -> None:
        self.calls: list[tuple[Message, ...]] = []

    async def generate(self, messages: Sequence[Message]) -> str:
        self.calls.append(tuple(messages))
        return "recorded"


class SlowThenFastModel(ChatModel):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False
        self.call_count = 0

    async def generate(self, messages: Sequence[Message]) -> str:
        del messages
        self.call_count += 1
        if self.call_count == 1:
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return "fresh reply"


class CancellationIgnoringModel(ChatModel):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.call_count = 0

    async def generate(self, messages: Sequence[Message]) -> str:
        del messages
        self.call_count += 1
        if self.call_count == 1:
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return "stale reply"
        return "fresh reply"


class FailingModel(ChatModel):
    async def generate(self, messages: Sequence[Message]) -> str:
        del messages
        raise ModelConnectionError("service unavailable")


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
        model = RecordingModel()
        dispatcher, _ = make_dispatcher(tmp_path / "chat.db", model)
        merger = ContinuousMessageMerger(dispatcher, merge_window_seconds=0.02)

        first = asyncio.create_task(merger.submit("张三", "第一条"))
        await asyncio.sleep(0.005)
        second = asyncio.create_task(merger.submit("张三", "第二条"))
        first_result, second_result = await asyncio.gather(first, second)

        assert first_result is None
        assert second_result is not None
        assert second_result.message_count == 2
        assert second_result.merged_content == (
            "【连续消息 1/2】\n第一条\n"
            "【连续消息 2/2】\n第二条"
        )
        assert [
            item.content
            for item in model.calls[0]
            if item.role == "user"
        ] == ["第一条", "第二条"]
        stored = Database(tmp_path / "chat.db").list_messages("张三")
        assert [(item.role, item.content) for item in stored] == [
            ("user", "第一条"),
            ("user", "第二条"),
            ("assistant", "recorded"),
        ]

    asyncio.run(scenario())


def test_debounce_batches_are_isolated_between_contacts(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = RecordingModel()
        dispatcher, _ = make_dispatcher(tmp_path / "chat.db", model)
        merger = ContinuousMessageMerger(dispatcher, debounce_seconds=0.01)

        zhang = asyncio.create_task(merger.submit("张三", "张三消息"))
        li = asyncio.create_task(merger.submit("李四", "李四消息"))
        zhang_result, li_result = await asyncio.gather(zhang, li)

        assert zhang_result is not None
        assert li_result is not None
        assert len(model.calls) == 2
        user_contexts = [
            [message.content for message in call if message.role == "user"]
            for call in model.calls
        ]
        assert ["张三消息"] in user_contexts
        assert ["李四消息"] in user_contexts

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


def test_human_takeover_never_calls_model(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = RecordingModel()
        dispatcher, _ = make_dispatcher(tmp_path / "chat.db", model)
        merger = ContinuousMessageMerger(dispatcher, debounce_seconds=0)
        dispatcher.set_human_takeover("张三", True)

        result = await merger.submit("张三", "只记录，不生成")

        assert result is not None
        assert result.skip_reason == "human_takeover"
        assert model.calls == []

    asyncio.run(scenario())


def test_message_received_during_takeover_stays_manual_after_resume(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        model = RecordingModel()
        dispatcher, _ = make_dispatcher(tmp_path / "chat.db", model)
        merger = ContinuousMessageMerger(dispatcher, debounce_seconds=0.02)
        dispatcher.set_human_takeover("张三", True)

        request = asyncio.create_task(merger.submit("张三", "人工期间收到"))
        await asyncio.sleep(0)
        dispatcher.set_human_takeover("张三", False)
        result = await request

        assert result is not None
        assert result.skip_reason == "human_takeover"
        assert model.calls == []

    asyncio.run(scenario())


def test_new_message_cancels_and_discards_stale_reply(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "chat.db"
        model = SlowThenFastModel()
        dispatcher, _ = make_dispatcher(database_path, model)
        merger = ContinuousMessageMerger(dispatcher, merge_window_seconds=0)

        old_request = asyncio.create_task(merger.submit("张三", "旧消息"))
        await asyncio.wait_for(model.started.wait(), timeout=1)
        new_request = asyncio.create_task(merger.submit("张三", "新消息"))

        old_result, new_result = await asyncio.gather(old_request, new_request)

        assert old_result is not None
        assert old_result.reply is None
        assert old_result.skip_reason == "stale"
        assert model.cancelled is True
        assert new_result is not None
        assert new_result.reply == "fresh reply"
        stored = Database(database_path).list_messages("张三")
        assert [(item.role, item.content) for item in stored] == [
            ("user", "旧消息"),
            ("user", "新消息"),
            ("assistant", "fresh reply"),
        ]

    asyncio.run(scenario())


def test_version_check_discards_reply_even_if_model_ignores_cancellation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "chat.db"
        model = CancellationIgnoringModel()
        dispatcher, _ = make_dispatcher(database_path, model)
        merger = ContinuousMessageMerger(dispatcher, debounce_seconds=0)

        old_request = asyncio.create_task(merger.submit("张三", "旧问题"))
        await asyncio.wait_for(model.started.wait(), timeout=1)
        new_request = asyncio.create_task(merger.submit("张三", "补充说明"))

        old_result, new_result = await asyncio.gather(old_request, new_request)

        assert old_result is not None
        assert old_result.skip_reason == "stale"
        assert new_result is not None
        assert new_result.reply == "fresh reply"
        assistant_messages = [
            item.content
            for item in Database(database_path).list_messages("张三")
            if item.role == "assistant"
        ]
        assert assistant_messages == ["fresh reply"]

    asyncio.run(scenario())


def test_recent_sqlite_history_is_restored_and_truncated_for_model(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "chat.db"
        database = Database(database_path)
        database.initialize()
        for role, content in (
            ("user", "太旧的用户消息"),
            ("assistant", "太旧的助手回复"),
            ("user", "上一条用户消息"),
            ("assistant", "上一条助手回复"),
        ):
            database.save_message("张三", Message(role=role, content=content))

        model = RecordingModel()
        dispatcher = MessageDispatcher(
            sessions=SessionManager(max_messages=3),
            engine=ReplyEngine(model, context_max_messages=3),
            memory=InMemoryStore(max_messages=3),
            database=database,
        )

        await dispatcher.dispatch("张三", "当前新消息")

        assert [(item.role, item.content) for item in model.calls[0]] == [
            ("user", "上一条用户消息"),
            ("assistant", "上一条助手回复"),
            ("user", "当前新消息"),
        ]
        assert sum(
            item.content == "当前新消息" for item in model.calls[0]
        ) == 1
        assert len(database.list_messages("张三")) == 6

    asyncio.run(scenario())


def test_human_takeover_cancels_in_progress_reply(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = SlowThenFastModel()
        dispatcher, _ = make_dispatcher(tmp_path / "chat.db", model)
        merger = ContinuousMessageMerger(dispatcher, merge_window_seconds=0)

        request = asyncio.create_task(merger.submit("张三", "生成中"))
        await asyncio.wait_for(model.started.wait(), timeout=1)
        dispatcher.set_human_takeover("张三", True)
        result = await request

        assert result is not None
        assert result.reply is None
        assert result.skip_reason == "human_takeover"
        assert model.cancelled is True
        assert [
            item.role for item in Database(tmp_path / "chat.db").list_messages("张三")
        ] == ["user"]

    asyncio.run(scenario())


def test_model_failure_keeps_received_message_in_sqlite(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "chat.db"
        dispatcher, _ = make_dispatcher(database_path, FailingModel())

        with pytest.raises(ModelConnectionError):
            await dispatcher.dispatch("张三", "即使失败也要保存")

        assert [
            (item.role, item.content)
            for item in Database(database_path).list_messages("张三")
        ] == [("user", "即使失败也要保存")]

    asyncio.run(scenario())
