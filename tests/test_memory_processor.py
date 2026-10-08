from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.core.session import Message
from app.memory.processor import MemoryMaintenanceCoordinator, MemoryProcessor
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.models.base import ChatModel
from app.storage.database import Database


class JsonModel(ChatModel):
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[Message, ...]] = []

    async def generate(self, messages: Sequence[Message]) -> str:
        self.requests.append(tuple(messages))
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


class FailingModel(ChatModel):
    async def generate(self, messages: Sequence[Message]) -> str:
        raise RuntimeError("maintenance failed")


def setup(path: Path, model: ChatModel) -> tuple[Database, MemoryRepository, MemoryProcessor]:
    database = Database(path)
    database.initialize()
    repository = MemoryRepository(database)
    service = MemoryService(repository)
    processor = MemoryProcessor(
        service,
        model,
        "return json",
        extraction_interval_messages=2,
        summary_trigger_messages=2,
        batch_messages=20,
    )
    return database, repository, processor


def test_summary_has_message_range_and_preserves_raw_messages(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = JsonModel([{"summary": "讨论驾照考试。", "candidates": []}])
        database, repository, processor = setup(tmp_path / "memory.db", model)
        first = database.save_message("张三", Message("user", "我准备考驾照"))
        last = database.save_message("张三", Message("assistant", "开始准备了啊"))

        report = await processor.process("张三")

        summary = repository.latest_summary("张三")
        assert summary is not None
        assert report.summary_id == summary.id
        assert (summary.start_message_id, summary.end_message_id) == (first, last)
        assert database.count_messages_after("张三", 0) == 2

    asyncio.run(scenario())


def test_summary_is_incrementally_linked_to_previous_summary(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = JsonModel([
            {"summary": "先聊到准备驾考。", "candidates": []},
            {"summary": "准备驾考，后来开始刷题。", "candidates": []},
        ])
        database, repository, processor = setup(tmp_path / "memory.db", model)
        first = database.save_message("张三", Message("user", "准备考驾照"))
        database.save_message("张三", Message("assistant", "嗯"))
        await processor.process("张三")
        old = repository.latest_summary("张三")
        database.save_message("张三", Message("user", "最近一直刷题"))
        last = database.save_message("张三", Message("assistant", "挺认真"))

        await processor.process("张三")

        current = repository.latest_summary("张三")
        assert old is not None and current is not None
        assert current.previous_summary_id == old.id
        assert (current.start_message_id, current.end_message_id) == (first, last)

    asyncio.run(scenario())


def test_maintenance_failure_is_contained_by_coordinator(tmp_path: Path) -> None:
    async def scenario() -> None:
        database, _, processor = setup(tmp_path / "memory.db", FailingModel())
        database.save_message("张三", Message("user", "一"))
        database.save_message("张三", Message("assistant", "二"))
        coordinator = MemoryMaintenanceCoordinator(processor)

        coordinator.schedule("张三")
        await coordinator.drain()

        assert database.count_messages_after("张三", 0) == 2

    asyncio.run(scenario())


def test_model_candidate_flows_through_validation_before_storage(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = JsonModel([])
        database, repository, processor = setup(tmp_path / "memory.db", model)
        source_id = database.save_message("张三", Message("user", "我不吃香菜"))
        database.save_message("张三", Message("assistant", "记住了"))
        model.responses.append({
            "summary": "聊到饮食偏好。",
            "candidates": [{
                "memory_type": "preference",
                "memory_key": "food.cilantro",
                "content": "张三不吃香菜",
                "confidence": 0.98,
                "source_message_ids": [source_id],
                "source_quote": "我不吃香菜",
                "action": "create",
                "target_memory_id": None,
            }],
        })

        report = await processor.process("张三")

        assert len(report.candidate_ids) == 1
        assert len(report.accepted_memory_ids) == 1
        memory = repository.get_memory(report.accepted_memory_ids[0])
        assert memory is not None and memory.content == "张三不吃香菜"

    asyncio.run(scenario())


@pytest.mark.parametrize("summary", [None, "", "   "])
def test_missing_summary_preserves_valid_candidates_and_raw_messages(tmp_path: Path, summary) -> None:
    async def scenario() -> None:
        model = JsonModel([])
        database, repository, processor = setup(tmp_path / "memory.db", model)
        source = database.save_message("张三", Message("user", "我不吃香菜"))
        last = database.save_message("张三", Message("assistant", "记住了"))
        model.responses.append({"summary": summary, "candidates": [{
            "memory_type": "preference", "memory_key": "food.cilantro",
            "content": "张三不吃香菜", "confidence": 0.98,
            "source_message_ids": [source], "source_quote": "我不吃香菜",
            "action": "create", "target_memory_id": None,
        }]})

        report = await processor.process("张三")

        assert report.skipped_reason == "summary_missing" and report.summary_id is None
        assert len(report.accepted_memory_ids) == 1
        assert repository.latest_summary("张三") is None
        assert repository.get_processing_state("张三") == last
        assert database.count_messages_after("张三", 0) == 2
        assert json.loads(model.requests[0][1].content)["summary_required"] is True
        assert "summary 必须是非空" in model.requests[0][0].content

    asyncio.run(scenario())


def test_omitted_summary_keeps_previous_summary_range(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = JsonModel([{"summary": "先讨论驾考。", "candidates": []}, {"candidates": []}])
        database, repository, processor = setup(tmp_path / "memory.db", model)
        database.save_message("张三", Message("user", "准备考驾照"))
        database.save_message("张三", Message("assistant", "嗯"))
        await processor.process("张三")
        before = repository.latest_summary("张三")
        database.save_message("张三", Message("user", "开始刷题"))
        database.save_message("张三", Message("assistant", "加油"))

        report = await processor.process("张三")

        assert report.skipped_reason == "summary_missing"
        assert repository.latest_summary("张三") == before
        assert database.count_messages_after("张三", 0) == 4

    asyncio.run(scenario())


def test_missing_summary_has_per_conversation_cooldown_and_recovers(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = JsonModel([
            {"candidates": []},
            {"summary": "李四独立会话。", "candidates": []},
            {"summary": "张三的有效摘要。", "candidates": []},
            {"summary": "张三新增消息的摘要。", "candidates": []},
        ])
        database, repository, processor = setup(tmp_path / "memory.db", model)
        for contact in ("张三", "李四"):
            database.save_message(contact, Message("user", "合成消息一"))
            database.save_message(contact, Message("assistant", "合成消息二"))
        clock = [0.0]
        coordinator = MemoryMaintenanceCoordinator(processor, failure_retry_seconds=10, clock=lambda: clock[0])
        coordinator.schedule("张三")
        await coordinator.drain()
        for _ in range(3):
            coordinator.schedule("张三")
            await coordinator.drain()
        assert len(model.requests) == 1  # Not one extra API call per new message.
        coordinator.schedule("李四")
        await coordinator.drain()
        assert len(model.requests) == 2 and repository.latest_summary("李四") is not None
        clock[0] = 10
        coordinator.schedule("张三")
        await coordinator.drain()
        assert repository.latest_summary("张三").content == "张三的有效摘要。"
        assert len(model.requests) == 3
        # Success removes the failure delay; another due batch is not held back.
        database.save_message("张三", Message("user", "三"))
        database.save_message("张三", Message("assistant", "四"))
        coordinator.schedule("张三")
        await coordinator.drain()
        assert len(model.requests) == 4
        assert database.count_messages_after("张三", 0) == 4

    asyncio.run(scenario())


@pytest.mark.parametrize("response", ["not json", '{"candidates": "not a list"}'])
def test_invalid_maintenance_output_is_cooled_down_without_data_loss(tmp_path: Path, response) -> None:
    class InvalidModel(ChatModel):
        calls = 0

        async def generate(self, messages) -> str:
            self.calls += 1
            return response

    async def scenario() -> None:
        model = InvalidModel()
        database, repository, processor = setup(tmp_path / "memory.db", model)
        database.save_message("张三", Message("user", "一"))
        database.save_message("张三", Message("assistant", "二"))
        coordinator = MemoryMaintenanceCoordinator(processor, failure_retry_seconds=10, clock=lambda: 0)
        coordinator.schedule("张三")
        await coordinator.drain()
        coordinator.schedule("张三")
        await coordinator.drain()
        assert model.calls == 1
        assert repository.get_processing_state("张三") == 0
        assert repository.latest_summary("张三") is None
        assert database.count_messages_after("张三", 0) == 2

    asyncio.run(scenario())
