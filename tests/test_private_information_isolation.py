from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

from app.adapters.admin_commands import AdminCommandHandler
from app.core.reply_engine import ReplyEngine
from app.core.session import Message, Session
from app.memory.context import ContextDebugger, MemoryContextProvider
from app.memory.processor import MemoryProcessor
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.models.base import ChatModel
from app.storage.database import Database


TEST_SECRET = "TEST_PRIVATE_SECRET_7F92A1"


class CapturingModel(ChatModel):
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[Message, ...]] = []

    async def generate(self, messages: Sequence[Message]) -> str:
        self.calls.append(tuple(messages))
        return self.response


def request_text(model: CapturingModel) -> str:
    return "\n".join(
        message.content for call in model.calls for message in call
    )


def test_private_information_never_enters_any_model_request(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "chat.db")
        database.initialize()
        repository = MemoryRepository(database)
        service = MemoryService(repository)
        service.set_account_profile("private_information", TEST_SECRET)
        database.save_message(
            "contact-1",
            Message("user", f"聊天中也意外出现 {TEST_SECRET}"),
        )
        database.save_message("contact-1", Message("assistant", "收到"))

        debug_output: list[str] = []
        debugger = ContextDebugger(enabled=True, sink=debug_output.append)
        chat_model = CapturingModel("正常回复")
        engine = ReplyEngine(
            chat_model,
            context_provider=MemoryContextProvider(service),
            context_debugger=debugger,
        )
        session = Session("contact-1")
        session.add_message("user", f"再次出现 {TEST_SECRET}")
        await engine.generate(session)

        maintenance_model = CapturingModel(
            '{"summary":"安全摘要","candidates":[]}'
        )
        processor = MemoryProcessor(
            service,
            maintenance_model,
            "只返回 JSON",
            extraction_interval_messages=2,
            summary_trigger_messages=2,
            batch_messages=10,
        )
        await processor.process("contact-1")

        command_output: list[str] = []
        handler = AdminCommandHandler(
            service,
            processor,
            debugger,
            sink=command_output.append,
        )
        await handler.handle("/account show")

        assert repository.get_account_profile().private_information == TEST_SECRET
        assert TEST_SECRET not in request_text(chat_model)
        assert TEST_SECRET not in request_text(maintenance_model)
        assert TEST_SECRET not in "\n".join(debug_output)
        assert TEST_SECRET not in "\n".join(command_output)
        assert "内容隐藏" in "\n".join(command_output)

    asyncio.run(scenario())


def test_private_information_stays_out_of_reply_repair_request(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "chat.db")
        database.initialize()
        repository = MemoryRepository(database)
        service = MemoryService(repository)
        service.set_account_profile("private_information", TEST_SECRET)
        model = CapturingModel("嗯")
        engine = ReplyEngine(
            model,
            context_provider=MemoryContextProvider(service),
            repair_prompt="信息不足时重新正常回答",
        )
        session = Session("contact-1")
        session.add_message(
            "user",
            f"为什么不能提到 {TEST_SECRET}",
        )

        await engine.generate(session)

        assert len(model.calls) == 2
        assert TEST_SECRET not in request_text(model)

    asyncio.run(scenario())
