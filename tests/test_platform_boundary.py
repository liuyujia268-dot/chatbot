from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.adapters.outbound import DryRunMessageSender
from app.core.dispatcher import ContinuousMessageMerger, MessageDispatcher
from app.core.memory import InMemoryStore
from app.core.platform import OutboundMessage, PlatformMessage
from app.core.reply_engine import ReplyEngine
from app.core.session import Message, SessionManager
from app.memory.context import ContextDebugger
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.models.base import ChatModel
from app.models.mock import MockModel
from app.storage.database import Database


class RecordingModel(ChatModel):
    def __init__(self) -> None:
        self.calls: list[tuple[Message, ...]] = []

    async def generate(self, messages: Sequence[Message]) -> str:
        self.calls.append(tuple(messages))
        return "ok"


class RecordingSender:
    def __init__(self) -> None:
        self.calls: list[OutboundMessage] = []

    async def send(self, message: OutboundMessage) -> None:
        self.calls.append(message)


def platform_message(
    message_id: str,
    *,
    platform: str = "test-platform",
    account_id: str = "account-1",
    conversation_id: str = "contact-1",
    text: str = "hello",
) -> PlatformMessage:
    return PlatformMessage(
        platform=platform,
        account_id=account_id,
        conversation_id=conversation_id,
        sender_id="sender-1",
        message_id=message_id,
        timestamp=datetime(2026, 9, 7, tzinfo=UTC),
        message_type="text",
        text=text,
        is_self=False,
    )


def make_merger(path: Path, model: ChatModel):
    database = Database(path)
    database.initialize()
    dispatcher = MessageDispatcher(
        sessions=SessionManager(),
        engine=ReplyEngine(model),
        memory=InMemoryStore(),
        database=database,
    )
    return database, ContinuousMessageMerger(dispatcher, debounce_seconds=0)


def test_same_platform_message_is_processed_only_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "chat.db"
        first_model = RecordingModel()
        database, merger = make_merger(database_path, first_model)
        message = platform_message("same-id")

        first = await merger.submit_platform(message)
        second_model = RecordingModel()
        _, restarted_merger = make_merger(database_path, second_model)
        duplicate = await restarted_merger.submit_platform(message)

        assert first is not None and first.reply == "ok"
        assert duplicate is None
        assert len(first_model.calls) == 1
        assert second_model.calls == []
        assert len(database.list_messages("contact-1")) == 2

    asyncio.run(scenario())


def test_same_message_id_on_different_accounts_does_not_conflict(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = RecordingModel()
        _, merger = make_merger(tmp_path / "chat.db", model)

        await merger.submit_platform(platform_message("42", account_id="a1"))
        await merger.submit_platform(platform_message("42", account_id="a2"))

        assert len(model.calls) == 2

    asyncio.run(scenario())


def test_same_message_id_on_different_platforms_does_not_conflict(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = RecordingModel()
        _, merger = make_merger(tmp_path / "chat.db", model)

        await merger.submit_platform(platform_message("42", platform="p1"))
        await merger.submit_platform(platform_message("42", platform="p2"))

        assert len(model.calls) == 2

    asyncio.run(scenario())


def test_external_slash_text_cannot_execute_local_admin_commands(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "chat.db")
        database.initialize()
        service = MemoryService(MemoryRepository(database))
        source_id = database.save_message(
            "contact-1", Message("user", "我不吃香菜")
        )
        _, memory = service.add_manual_memory(
            conversation_id="contact-1",
            memory_type="preference",
            memory_key="food.cilantro",
            content="contact-1 不吃香菜",
            source_message_ids=(source_id,),
        )
        assert memory is not None
        model = RecordingModel()
        debugger = ContextDebugger()
        dispatcher = MessageDispatcher(
            sessions=SessionManager(),
            engine=ReplyEngine(model, context_debugger=debugger),
            memory=InMemoryStore(),
            database=database,
        )
        merger = ContinuousMessageMerger(dispatcher, debounce_seconds=0)
        commands = (
            "/memory disable 1",
            "/profile set contact-1 relationship 陌生人",
            "/account set public_name 被篡改",
            "/debug on",
        )

        for index, text in enumerate(commands):
            await merger.submit_platform(platform_message(str(index), text=text))

        assert service.repository.get_memory(memory.id).status == "active"
        assert service.repository.get_contact_profile("contact-1") is None
        assert service.repository.get_account_profile() is None
        assert debugger.enabled is False
        sent_user_text = "\n".join(
            item.content
            for call in model.calls
            for item in call
            if item.role == "user"
        )
        assert all(command in sent_user_text for command in commands)

    asyncio.run(scenario())


def test_dry_run_records_but_never_calls_wrapped_sender() -> None:
    async def scenario() -> None:
        real_sender = RecordingSender()
        sender = DryRunMessageSender(real_sender)
        outbound = OutboundMessage(
            platform="test",
            account_id="account-1",
            conversation_id="contact-1",
            text="本来准备发送的回复",
        )

        await sender.send(outbound)

        assert sender.records == [outbound]
        assert real_sender.calls == []

    asyncio.run(scenario())


def test_console_platform_path_still_works_with_mock_model(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, merger = make_merger(tmp_path / "chat.db", MockModel())
        result = await merger.submit_platform(
            platform_message(
                "console-1",
                platform="console",
                account_id="local",
                conversation_id="骨灰",
                text="今晚吃了吗您",
            )
        )

        assert result is not None
        assert result.reply == MockModel.DEFAULT_REPLY

    asyncio.run(scenario())
