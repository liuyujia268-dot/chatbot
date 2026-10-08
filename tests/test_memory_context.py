from __future__ import annotations

from pathlib import Path

from app.core.reply_engine import ReplyEngine
from app.core.session import Message, Session
from app.memory.context import ContextDebugger, MemoryContextProvider
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.models.mock import MockModel
from app.storage.database import Database


def test_context_sections_are_separate_and_recent_chat_is_limited(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "memory.db")
    database.initialize()
    repository = MemoryRepository(database)
    service = MemoryService(repository, context_limit=2, context_fallback=1)
    source_id = database.save_message("张三", Message("user", "我不吃香菜"))
    service.add_manual_memory(
        conversation_id="张三",
        memory_type="preference",
        memory_key="food.cilantro",
        content="张三不吃香菜",
        source_message_ids=(source_id,),
    )
    repository.add_summary("张三", "之前聊过聚餐。", source_id, source_id, None)
    service.set_contact_profile("张三", "relationship", "熟悉的朋友")
    service.set_account_profile("public_name", "小助手")
    session = Session("张三")
    for index in range(6):
        role = "user" if index % 2 == 0 else "assistant"
        session.add_message(role, f"历史-{index}")
    session.add_message("user", "那家店全是香菜")
    engine = ReplyEngine(
        MockModel(),
        system_prompt="聊天规则",
        context_max_messages=3,
        context_provider=MemoryContextProvider(service),
    )

    messages = engine.build_model_messages(session)

    system_texts = [item.content for item in messages if item.role == "system"]
    recent = [item for item in messages if item.role != "system"]
    assert len(recent) == 3
    assert sum(item.content == "那家店全是香菜" for item in recent) == 1
    assert any(text.startswith("【当前账号资料】") for text in system_texts)
    assert any(text.startswith("【当前联系人画像】") for text in system_texts)
    assert any(text.startswith("【相关长期记忆】") for text in system_texts)
    assert any(text.startswith("【当前话题摘要】") for text in system_texts)


def test_debug_output_redacts_api_key_and_is_off_by_default(tmp_path: Path) -> None:
    database = Database(tmp_path / "memory.db")
    database.initialize()
    service = MemoryService(MemoryRepository(database))
    api_key = "sk-test-secret-value"
    output: list[str] = []
    debugger = ContextDebugger(redacted_values=(api_key,), sink=output.append)
    session = Session("张三")
    session.add_message("user", f"误贴了 {api_key}")
    engine = ReplyEngine(
        MockModel(),
        context_provider=MemoryContextProvider(service),
        context_debugger=debugger,
    )

    engine.build_model_messages(session)
    assert output == []

    debugger.set_enabled(True)
    engine.build_model_messages(session)
    assert api_key not in output[-1]
    assert "***" in output[-1]


def test_public_profile_image_path_is_hidden_from_model_and_debug(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "memory.db")
    database.initialize()
    service = MemoryService(MemoryRepository(database))
    hidden_path = "app/assets/whale_girl_profile.jpg"
    service.set_account_profile("public_image_path", hidden_path)
    debugger = ContextDebugger(enabled=True)
    context = service.context_for("张三", "把你的资料发我看看")

    system_text = "\n".join(
        message.content
        for message in MemoryContextProvider.system_messages(context)
    )
    assert "已配置可公开的鲸鱼娘资料图" in system_text
    assert hidden_path not in system_text

    from app.memory.context import ModelContextSnapshot

    rendered = debugger.render(
        ModelContextSnapshot(
            conversation_id="张三",
            model_messages=(),
            recent_history=(),
            current_message=None,
            memory_context=context,
        )
    )
    assert hidden_path not in rendered
    assert "public_image_path=<路径隐藏>" in rendered
