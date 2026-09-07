from __future__ import annotations

from pathlib import Path

import pytest

from app.core.session import Message
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.memory.types import CandidateProposal
from app.storage.database import Database


def make_service(path: Path, **kwargs) -> tuple[Database, MemoryService]:
    database = Database(path)
    database.initialize()
    repository = MemoryRepository(database)
    return database, MemoryService(repository, **kwargs)


def proposal(
    conversation_id: str,
    message_id: int,
    quote: str,
    *,
    memory_type: str = "preference",
    memory_key: str = "food.cilantro",
    content: str = "不吃香菜",
    action: str = "create",
    target_memory_id: int | None = None,
    confidence: float = 0.95,
) -> CandidateProposal:
    return CandidateProposal(
        conversation_id=conversation_id,
        memory_type=memory_type,
        memory_key=memory_key,
        content=content,
        confidence=confidence,
        source_message_ids=(message_id,),
        source_quote=quote,
        action=action,
        target_memory_id=target_memory_id,
    )


def test_memory_candidate_is_created_and_traceable(tmp_path: Path) -> None:
    database, service = make_service(tmp_path / "memory.db")
    source_id = database.save_message("张三", Message("user", "我不吃香菜"))

    candidate, memory = service.submit_candidate(
        proposal("张三", source_id, "我不吃香菜")
    )

    assert candidate.status == "accepted"
    assert memory is not None
    assert memory.source_start_message_id == source_id
    assert memory.source_end_message_id == source_id
    assert memory.status == "active"


def test_contact_memory_is_strictly_isolated(tmp_path: Path) -> None:
    database, service = make_service(tmp_path / "memory.db", context_fallback=2)
    source_id = database.save_message("张三", Message("user", "我准备考驾照"))
    _, memory = service.submit_candidate(
        proposal(
            "张三",
            source_id,
            "我准备考驾照",
            memory_type="ongoing_event",
            memory_key="exam.driving",
            content="张三正在准备驾照考试",
        )
    )

    assert memory is not None
    assert service.context_for("张三", "最近一直在刷题").memories == (memory,)
    assert service.context_for("李四", "最近干嘛呢").memories == ()


def test_candidate_rejects_evidence_from_another_contact(tmp_path: Path) -> None:
    database, service = make_service(tmp_path / "memory.db")
    source_id = database.save_message("张三", Message("user", "我不吃香菜"))

    candidate, memory = service.submit_candidate(
        proposal("李四", source_id, "我不吃香菜")
    )

    assert memory is None
    assert candidate.status == "rejected"
    assert "conversation" in (candidate.rejection_reason or "")


def test_memory_update_supersedes_old_value(tmp_path: Path) -> None:
    database, service = make_service(tmp_path / "memory.db")
    old_id = database.save_message("张三", Message("user", "我现在在上海"))
    _, old = service.submit_candidate(
        proposal(
            "张三", old_id, "我现在在上海", memory_type="fact",
            memory_key="location.current", content="张三目前在上海",
        )
    )
    assert old is not None
    new_id = database.save_message(
        "张三", Message("user", "我上个月搬到杭州了")
    )
    candidate, new = service.submit_candidate(
        proposal(
            "张三", new_id, "搬到杭州", memory_type="fact",
            memory_key="location.current", content="张三目前在杭州",
            action="update", target_memory_id=old.id,
        )
    )

    assert candidate.status == "accepted"
    assert new is not None and new.supersedes_memory_id == old.id
    assert service.repository.get_memory(old.id).status == "superseded"
    active = service.repository.list_memories("张三", status="active")
    assert [item.content for item in active] == ["张三目前在杭州"]


def test_disabled_memory_is_not_selected(tmp_path: Path) -> None:
    database, service = make_service(tmp_path / "memory.db")
    source_id = database.save_message("张三", Message("user", "我不吃香菜"))
    _, memory = service.submit_candidate(
        proposal("张三", source_id, "我不吃香菜")
    )
    assert memory is not None

    assert service.repository.disable_memory(memory.id)

    assert service.repository.get_memory(memory.id).status == "disabled"
    assert service.context_for("张三", "香菜").memories == ()


def test_contact_profile_fields_are_read_with_provenance(tmp_path: Path) -> None:
    _, service = make_service(tmp_path / "memory.db")

    profile = service.set_contact_profile("张三", "relationship", "熟悉的朋友")
    service.set_contact_profile("张三", "communication_style", "日常聊天较随意")

    loaded = service.context_for("张三", "在吗").contact_profile
    assert loaded is not None
    assert profile.relationship == "熟悉的朋友"
    assert loaded.communication_style == "日常聊天较随意"
    assert loaded.provenance["relationship"]["kind"] == "manual"
    assert service.context_for("李四", "在吗").contact_profile is None


def test_low_confidence_candidate_remains_pending(tmp_path: Path) -> None:
    database, service = make_service(tmp_path / "memory.db")
    source_id = database.save_message("张三", Message("user", "我可能要换工作"))

    candidate, memory = service.submit_candidate(
        proposal(
            "张三", source_id, "可能要换工作", memory_type="ongoing_event",
            memory_key="work.change", content="张三可能准备换工作",
            confidence=0.6,
        )
    )

    assert candidate.status == "pending"
    assert memory is None


def test_summary_range_cannot_cross_contacts(tmp_path: Path) -> None:
    database, service = make_service(tmp_path / "memory.db")
    start = database.save_message("张三", Message("user", "张三的消息"))
    end = database.save_message("李四", Message("user", "李四的消息"))

    with pytest.raises(ValueError, match="range"):
        service.repository.add_summary("张三", "错误摘要", start, end, None)
