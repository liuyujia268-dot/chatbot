from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.adapters.image_resolver import ImageResolutionError, ImageResolver
from app.adapters.onebot import parse_onebot_event
from app.adapters.qq import QQAdapter
from app.core.dispatcher import ContinuousMessageMerger, MessageDispatcher
from app.core.memory import InMemoryStore
from app.core.platform import OutboundMessage
from app.core.identity import conversation_key
from app.core.reply_engine import ReplyEngine
from app.core.session import Message, SessionManager
from app.models.base import ChatModel, ModelTimeoutError
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.storage.database import Database
from app.style.repository import StyleRepository


class CountingModel(ChatModel):
    def __init__(self) -> None:
        self.calls: list[tuple[Message, ...]] = []

    async def generate(self, messages: Sequence[Message]) -> str:
        self.calls.append(tuple(messages))
        return "候选回复"


class SequenceModel(ChatModel):
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[Message, ...]] = []

    async def generate(self, messages: Sequence[Message]) -> str:
        self.calls.append(tuple(messages))
        return self.replies.pop(0)


class MidpointRandom:
    @staticmethod
    def uniform(low: float, high: float) -> float:
        return (low + high) / 2

    @staticmethod
    def random() -> float:
        return 0.0


class MutableClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class RecordingSender:
    def __init__(self) -> None:
        self.calls: list[OutboundMessage] = []

    async def send(self, message: OutboundMessage) -> str:
        self.calls.append(message)
        return f"ack-{len(self.calls)}"


class FailingSender:
    async def send(self, message: OutboundMessage) -> None:
        raise RuntimeError("send failed")


class StaleAwareModel(ChatModel):
    def __init__(self) -> None:
        self.calls: list[tuple[Message, ...]] = []
        self.first_started = asyncio.Event()

    async def generate(self, messages: Sequence[Message]) -> str:
        self.calls.append(tuple(messages))
        if len(self.calls) == 1:
            self.first_started.set()
            await asyncio.Event().wait()
        return "最新回复"


class TimeoutModel(ChatModel):
    async def generate(self, messages: Sequence[Message]) -> str:
        raise ModelTimeoutError("模型请求超时，请稍后重试。")


def event(
    message_id: str,
    text: str = "你好",
    *,
    user_id: str = "20002",
    nickname: str = "宇飞",
    group_id: str | None = None,
    self_message: bool = False,
    parts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    account_id = "10001"
    if self_message:
        user_id = account_id
    result = {
        "post_type": "message_sent" if self_message else "message",
        "message_type": "group" if group_id else "private",
        "self_id": account_id,
        "user_id": user_id,
        "message_id": message_id,
        "time": 1788796800,
        "sender": {"nickname": nickname},
        "message": parts or [{"type": "text", "data": {"text": text}}],
    }
    if group_id:
        result["group_id"] = group_id
    return result


def make_adapter(
    path: Path,
    *,
    primary_id: str | None = "20002",
    training_groups: tuple[str, ...] = ("90001",),
    reply_groups: tuple[str, ...] = (),
    dry_run: bool = True,
    proactive_contacts: tuple[str, ...] = (),
    proactive_excluded: tuple[str, ...] = (),
    proactive_limit: int = 0,
    proactive_min: float = 1800,
    proactive_max: float = 3600,
    proactive_quiet_start: str = "23:59",
    proactive_quiet_end: str = "07:00",
    admin_ids: tuple[str, ...] = (),
    admin_config_writer: Any | None = None,
    followup_enabled: bool = False,
    followup_probability: float = 0.15,
    followup_min_delay: float = 0,
    followup_max_delay: float = 0,
    random_source: Any | None = None,
    monotonic_clock: Any | None = None,
    wall_clock: Any | None = None,
    context_min: int = 3,
    ai_ids: set[tuple[str, str, str]] | None = None,
    image_resolver: Any | None = None,
    model_override: ChatModel | None = None,
    event_source: Any | None = None,
    sink: Any | None = None,
    sender_override: Any | None = None,
):
    database = Database(path)
    database.initialize()
    model = model_override or CountingModel()
    dispatcher = MessageDispatcher(
        sessions=SessionManager(),
        engine=ReplyEngine(model),
        memory=InMemoryStore(),
        database=database,
    )
    styles = StyleRepository(
        database,
        context_min_messages=context_min,
        context_max_messages=10,
    )
    memory_service = MemoryService(MemoryRepository(database))
    sender = sender_override or RecordingSender()
    adapter = QQAdapter(
        merger=ContinuousMessageMerger(dispatcher, debounce_seconds=0),
        database=database,
        style_repository=styles,
        image_resolver=image_resolver or ImageResolver(
            max_bytes=1024 * 1024,
            timeout_seconds=1,
            allow_remote_url=True,
        ),
        primary_contact_id=primary_id,
        primary_contact_name="宇飞",
        training_group_ids=training_groups,
        admin_contact_ids=admin_ids,
        admin_config_writer=admin_config_writer,
        memory_service=memory_service,
        profile_asset_root=path.parent,
        reply_group_ids=reply_groups,
        dry_run=dry_run,
        proactive_contact_ids=proactive_contacts,
        proactive_exclude_contact_ids=proactive_excluded,
        proactive_min_seconds=proactive_min,
        proactive_max_seconds=proactive_max,
        proactive_daily_limit=proactive_limit,
        proactive_quiet_start=proactive_quiet_start,
        proactive_quiet_end=proactive_quiet_end,
        proactive_prompt="自然地主动发一条简短消息",
        proactive_enabled=True,
        followup_enabled=followup_enabled,
        followup_probability=followup_probability,
        followup_min_delay_seconds=followup_min_delay,
        followup_max_delay_seconds=followup_max_delay,
        followup_prompt="补充一句新信息",
        event_source=event_source,
        sender=sender,
        sink=sink or (lambda value: None),
        ai_generated_message_ids=ai_ids,
        random_source=random_source,
        monotonic_clock=monotonic_clock,
        wall_clock=wall_clock,
    )
    return adapter, model, sender, database, styles


def stored_messages(database, route):
    # These tests explicitly inspect simulation history as well as ACKed history.
    return database.list_messages(conversation_key('qq', '10001', route), include_simulated=True)


def image_part(
    url: str = "https://images.example.test/qq.jpg",
) -> dict[str, Any]:
    return {
        "type": "image",
        "data": {
            "file": "qq.jpg",
            "url": url,
            "summary": "一张测试图",
            "file_size": 100,
            "sub_type": 0,
        },
    }


def at_self_part() -> dict[str, Any]:
    return {"type": "at", "data": {"qq": "10001"}}


def mobile_self_event(
    message_id: str,
    text: str = "我来人工回复",
    *,
    peer_id: str = "20002",
) -> dict[str, Any]:
    return {
        "post_type": "message",
        "message_type": "private",
        "self_id": "10001",
        "user_id": peer_id,
        "message_id": message_id,
        "time": 1788796800,
        "sender": {"user_id": "10001", "nickname": "当前账号"},
        "message": [{"type": "text", "data": {"text": text}}],
    }


class FailingImageResolver:
    async def resolve(self, part):
        raise ImageResolutionError("图片下载失败")

    @staticmethod
    def unavailable(part):
        return ImageResolver.unavailable(part)


def test_primary_private_enters_dispatcher_and_stays_dry_run(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, sender, _, _ = make_adapter(tmp_path / "chat.db")

        result = await adapter.handle_event(event("p1"))

        assert result is not None and result.reply == "候选回复"
        assert len(model.calls) == 1
        assert len(adapter.dry_run_records) == 1
        assert sender.calls == []

    asyncio.run(scenario())


def test_private_reply_calls_sender_when_dry_run_is_disabled(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, sender, _, _ = make_adapter(
            tmp_path / "chat.db",
            dry_run=False,
        )

        result = await adapter.handle_event(event("real-private"))

        assert result is not None and result.reply == "候选回复"
        assert len(model.calls) == 1
        assert len(sender.calls) == 1
        assert sender.calls[0].conversation_id == "qq:private:20002"
        assert adapter.dry_run_records == ()

    asyncio.run(scenario())


def test_multisentence_private_reply_is_sent_as_separate_bubbles(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        model = SequenceModel("先这样。再等等！\n最后一句")
        adapter, _, sender, _, _ = make_adapter(
            tmp_path / "chat.db",
            dry_run=False,
            model_override=model,
        )

        result = await adapter.handle_event(event("split-private"))

        assert result is not None
        assert [message.text for message in sender.calls] == [
            "先这样。",
            "再等等！",
            "最后一句",
        ]

    asyncio.run(scenario())


def test_multisentence_reply_stays_split_in_dry_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = SequenceModel("“知道啦。”别催？")
        adapter, _, sender, _, _ = make_adapter(
            tmp_path / "chat.db",
            model_override=model,
        )

        await adapter.handle_event(event("split-dry-run"))

        assert sender.calls == []
        assert [message.text for message in adapter.dry_run_records] == [
            "“知道啦。”",
            "别催？",
        ]

    asyncio.run(scenario())


def test_explicit_profile_request_attaches_public_profile_image(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        image = tmp_path / "assets" / "whale-girl.jpg"
        image.parent.mkdir()
        image.write_bytes(b"test-image")
        adapter, model, sender, database, _ = make_adapter(
            tmp_path / "chat.db",
            dry_run=False,
        )
        MemoryService(MemoryRepository(database)).set_account_profile(
            "public_image_path",
            "assets/whale-girl.jpg",
            account_key="qq:10001",
        )

        await adapter.handle_event(
            event("profile-image", "能发一下你的资料吗")
        )

        assert len(model.calls) == 1
        assert len(sender.calls) == 1
        assert sender.calls[0].metadata["local_image_paths"] == (
            str(image.resolve()),
        )
        assert str(image.resolve()) not in repr(model.calls[0])

    asyncio.run(scenario())


def test_ordinary_chat_does_not_attach_profile_image(tmp_path: Path) -> None:
    async def scenario() -> None:
        image = tmp_path / "whale-girl.jpg"
        image.write_bytes(b"test-image")
        adapter, _, sender, database, _ = make_adapter(
            tmp_path / "chat.db",
            dry_run=False,
        )
        MemoryService(MemoryRepository(database)).set_account_profile(
            "public_image_path",
            "whale-girl.jpg",
            account_key="qq:10001",
        )

        await adapter.handle_event(event("ordinary", "今天吃什么"))

        assert sender.calls[0].metadata.get("local_image_paths") is None

    asyncio.run(scenario())


def test_followup_does_not_resend_public_profile_image(tmp_path: Path) -> None:
    async def scenario() -> None:
        image = tmp_path / "whale-girl.jpg"
        image.write_bytes(b"test-image")
        model = SequenceModel("给你看看", "这张很可爱吧")
        adapter, _, sender, database, _ = make_adapter(
            tmp_path / "chat.db",
            dry_run=False,
            model_override=model,
            followup_enabled=True,
            followup_probability=1,
            random_source=MidpointRandom(),
        )
        MemoryService(MemoryRepository(database)).set_account_profile(
            "public_image_path",
            "whale-girl.jpg",
            account_key="qq:10001",
        )

        await adapter.handle_event(event("profile-followup", "看看你的照片"))

        assert len(sender.calls) == 2
        assert "local_image_paths" in sender.calls[0].metadata
        assert "local_image_paths" not in sender.calls[1].metadata

    asyncio.run(scenario())


def test_successful_reply_can_generate_one_separate_followup(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        model = SequenceModel("第一条回复", "第二条补充")
        adapter, _, sender, database, _ = make_adapter(
            tmp_path / "chat.db",
            dry_run=False,
            model_override=model,
            followup_enabled=True,
            followup_probability=1,
            random_source=MidpointRandom(),
        )

        result = await adapter.handle_event(event("followup"))

        assert result is not None and result.reply == "第一条回复"
        assert [message.text for message in sender.calls] == [
            "第一条回复",
            "第二条补充",
        ]
        assert any(
            item.role == "system" and "补充一句" in str(item.content)
            for item in model.calls[1]
        )
        assert [
            item.content
            for item in stored_messages(database, "qq:private:20002")
            if item.role == "assistant"
        ] == ["第一条回复", "第二条补充"]

    asyncio.run(scenario())


def test_new_human_self_message_cancels_pending_followup(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        model = SequenceModel("第一条回复")
        adapter, _, sender, _, _ = make_adapter(
            tmp_path / "chat.db",
            dry_run=False,
            model_override=model,
            followup_enabled=True,
            followup_probability=1,
            followup_min_delay=0.05,
            followup_max_delay=0.05,
            random_source=MidpointRandom(),
        )

        task = asyncio.create_task(
            adapter.handle_event(event("followup-cancel"))
        )
        for _ in range(50):
            if sender.calls:
                break
            await asyncio.sleep(0.005)
        await adapter.handle_event(
            mobile_self_event("manual-during-delay", "我接手了")
        )
        await task

        assert [message.text for message in sender.calls] == ["第一条回复"]
        assert len(model.calls) == 1

    asyncio.run(scenario())


def test_followup_identical_to_first_reply_is_not_sent_or_saved(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        model = SequenceModel("别急，慢慢来", "别急！慢慢来。")
        adapter, _, sender, database, _ = make_adapter(
            tmp_path / "chat.db",
            dry_run=False,
            model_override=model,
            followup_enabled=True,
            followup_probability=1,
            random_source=MidpointRandom(),
        )

        await adapter.handle_event(event("duplicate-followup"))

        assert [message.text for message in sender.calls] == ["别急，慢慢来"]
        assert len(model.calls) == 2
        assert [
            item.content
            for item in stored_messages(database, "qq:private:20002")
            if item.role == "assistant"
        ] == ["别急，慢慢来"]

    asyncio.run(scenario())


def test_followup_near_duplicate_is_not_sent_or_saved(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = SequenceModel("别急，慢慢来", "别着急，慢慢来呀")
        adapter, _, sender, database, _ = make_adapter(
            tmp_path / "chat.db",
            dry_run=False,
            model_override=model,
            followup_enabled=True,
            followup_probability=1,
            random_source=MidpointRandom(),
        )

        await adapter.handle_event(event("near-duplicate-followup"))

        assert [message.text for message in sender.calls] == ["别急，慢慢来"]
        assert [
            item.content
            for item in stored_messages(database, "qq:private:20002")
            if item.role == "assistant"
        ] == ["别急，慢慢来"]

    asyncio.run(scenario())


def test_followup_can_explicitly_decline_a_redundant_second_message(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        model = SequenceModel("第一条已经够了", "[[NO_FOLLOWUP]]")
        adapter, _, sender, database, _ = make_adapter(
            tmp_path / "chat.db",
            dry_run=False,
            model_override=model,
            followup_enabled=True,
            followup_probability=1,
            random_source=MidpointRandom(),
        )

        await adapter.handle_event(event("declined-followup"))

        assert [message.text for message in sender.calls] == ["第一条已经够了"]
        assert [
            item.content
            for item in stored_messages(database, "qq:private:20002")
            if item.role == "assistant"
        ] == ["第一条已经够了"]

    asyncio.run(scenario())


def test_failed_first_send_never_attempts_followup(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = SequenceModel("第一条回复", "不应生成")
        adapter, _, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            dry_run=False,
            model_override=model,
            sender_override=FailingSender(),
            followup_enabled=True,
            followup_probability=1,
            random_source=MidpointRandom(),
        )

        await adapter.handle_event(event("first-send-fails"))
        with adapter._database.connect() as connection:
            assert connection.execute("SELECT status FROM outbound_deliveries").fetchone()[0] == "failed"
        assert len(model.calls) == 1

    asyncio.run(scenario())


def test_non_primary_private_enters_dispatcher_and_stays_dry_run(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, database, _ = make_adapter(tmp_path / "chat.db")

        result = await adapter.handle_event(
            event("p2", user_id="30003", nickname="别人")
        )

        assert result is not None and result.reply == "候选回复"
        assert len(model.calls) == 1
        assert len(adapter.dry_run_records) == 1
        with database.connect() as connection:
            total = connection.execute(
                "SELECT COUNT(*) AS total FROM inbound_platform_messages"
            ).fetchone()["total"]
        assert total == 1

    asyncio.run(scenario())


def test_runtime_auto_reply_switch_records_without_calling_model(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, database, _ = make_adapter(tmp_path / "chat.db")
        adapter.set_auto_reply_enabled(False)

        result = await adapter.handle_event(event("auto-off", "先别回复"))

        assert result is None
        assert model.calls == []
        assert stored_messages(database, "qq:private:20002")[-1].content == "先别回复"

    asyncio.run(scenario())


def test_matching_nickname_is_only_a_candidate_without_configured_id(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        output: list[str] = []
        adapter, model, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            primary_id=None,
            sink=output.append,
        )

        await adapter.handle_event(event("candidate", nickname="Õ_Õ"))
        await adapter.handle_event(event("candidate-2", nickname="Õ_Õ"))

        assert len(model.calls) == 2
        assert len(adapter.dry_run_records) == 2
        assert any("QQ PRIMARY CANDIDATE" in line for line in output)
        assert any("nickname=Õ_Õ" in line for line in output)

    asyncio.run(scenario())


def test_primary_id_not_nickname_controls_identity(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter, model, _, database, _ = make_adapter(tmp_path / "chat.db")

        await adapter.handle_event(event("p3", nickname="改过的昵称"))

        assert len(model.calls) == 1

    asyncio.run(scenario())


def test_admin_instruction_creates_traceable_memory_without_model(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, database, _ = make_adapter(
            tmp_path / "chat.db",
            admin_ids=("20002",),
        )

        result = await adapter.handle_event(
            event("admin-memory", "#指令 [我不吃香菜]")
        )
        await adapter.handle_event(
            event("admin-memory", "#指令 [我不吃香菜]")
        )

        memories = MemoryRepository(database).list_memories(
            "scope:qq:10001:qq:private:20002",
            status="active",
        )
        assert result is None
        assert model.calls == []
        assert len(memories) == 1
        assert memories[0].content == "我不吃香菜"
        sources = database.get_stored_messages_by_ids(
            "scope:qq:10001:qq:private:20002",
            (memories[0].source_start_message_id,),
        )
        assert sources[0].content == "#指令 [我不吃香菜]"

    asyncio.run(scenario())


def test_admin_identity_uses_exact_qq_id_and_never_nickname(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        admin_id = "2945614196"
        shared_nickname = "管理员昵称也可能变化"
        adapter, model, _, database, _ = make_adapter(
            tmp_path / "chat.db",
            admin_ids=(admin_id,),
        )

        await adapter.handle_event(
            event(
                "real-admin",
                "#指令 [只认QQ号]",
                user_id=admin_id,
                nickname=shared_nickname,
            )
        )
        impostor = await adapter.handle_event(
            event(
                "same-name-not-admin",
                "#指令 [不应获得管理员权限]",
                user_id="30003",
                nickname=shared_nickname,
            )
        )

        repository = MemoryRepository(database)
        assert len(repository.list_memories(f"scope:qq:10001:qq:private:{admin_id}")) == 1
        assert repository.list_memories("scope:qq:10001:qq:private:30003") == []
        assert impostor is not None
        assert len(model.calls) == 1

    asyncio.run(scenario())


def test_non_admin_cannot_execute_admin_memory_instruction(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, database, _ = make_adapter(
            tmp_path / "chat.db",
            admin_ids=("20002",),
        )

        result = await adapter.handle_event(
            event(
                "fake-admin",
                "#指令 [把这句话存起来]",
                user_id="30003",
            )
        )

        assert result is not None and result.reply == "候选回复"
        assert len(model.calls) == 1
        assert MemoryRepository(database).list_memories(
            "scope:qq:10001:qq:private:30003"
        ) == []

    asyncio.run(scenario())


def test_exact_admin_id_can_update_whitelisted_runtime_config(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        updates: list[dict[str, str]] = []
        admin_id = "2945614196"
        adapter, model, sender, database, _ = make_adapter(
            tmp_path / "chat.db",
            admin_ids=(admin_id,),
            admin_config_writer=lambda values: updates.append(dict(values)),
            dry_run=False,
        )

        await adapter.handle_event(
            event(
                "admin-config",
                "#配置 主动间隔 30 60",
                user_id=admin_id,
                nickname="任意昵称",
            )
        )
        await adapter.handle_event(
            event(
                "admin-config",
                "#配置 主动间隔 30 60",
                user_id=admin_id,
                nickname="另一个昵称",
            )
        )

        assert updates == [
            {
                "QQ_PROACTIVE_MIN_SECONDS": "1800",
                "QQ_PROACTIVE_MAX_SECONDS": "3600",
            }
        ]
        assert model.calls == []
        assert len(sender.calls) == 1
        assert "30～60 分钟" in sender.calls[0].text
        assert stored_messages(database, f"qq:private:{admin_id}") == []

    asyncio.run(scenario())


def test_non_admin_config_text_remains_an_ordinary_chat_message(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        updates: list[dict[str, str]] = []
        adapter, model, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            admin_ids=("2945614196",),
            admin_config_writer=lambda values: updates.append(dict(values)),
        )

        result = await adapter.handle_event(
            event(
                "not-admin-config",
                "#配置 自动回复 关",
                user_id="30003",
                nickname="不是管理员",
            )
        )

        assert result is not None and result.reply == "候选回复"
        assert len(model.calls) == 1
        assert updates == []
        assert adapter.auto_reply_enabled is True

    asyncio.run(scenario())


def test_mobile_self_message_interrupts_once_without_persistent_takeover(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        parsed = parse_onebot_event(mobile_self_event("mobile-self"))
        assert parsed is not None
        assert parsed.message.is_self is True
        assert parsed.message.sender_id == "10001"
        assert parsed.peer_id == "20002"
        assert parsed.message.conversation_id == "qq:private:20002"

        adapter, model, _, database, _ = make_adapter(
            tmp_path / "chat.db",
        )
        await adapter.handle_event(mobile_self_event("mobile-self"))
        result = await adapter.handle_event(event("after-mobile", "你还在吗"))

        assert result is not None and result.reply == "候选回复"
        assert len(model.calls) == 1
        stored = stored_messages(database, "qq:private:20002")
        assert [(item.role, item.content) for item in stored] == [
            ("assistant", "我来人工回复"),
            ("user", "你还在吗"),
            ("assistant", "候选回复"),
        ]

    asyncio.run(scenario())


def test_private_self_event_without_peer_is_rejected() -> None:
    unresolved = {
        "post_type": "message_sent",
        "message_type": "private",
        "self_id": "10001",
        "user_id": "10001",
        "message_id": "missing-peer",
        "time": 1788796800,
        "sender": {"user_id": "10001", "nickname": "当前账号"},
        "message": [{"type": "text", "data": {"text": "人工消息"}}],
    }

    assert parse_onebot_event(unresolved) is None


def test_private_self_event_uses_explicit_target_instead_of_self() -> None:
    outgoing = {
        "post_type": "message_sent",
        "message_type": "private",
        "self_id": "10001",
        "user_id": "10001",
        "target_id": "20002",
        "message_id": "known-peer",
        "time": 1788796800,
        "sender": {"user_id": "10001", "nickname": "当前账号"},
        "message": [{"type": "text", "data": {"text": "人工消息"}}],
    }

    parsed = parse_onebot_event(outgoing)

    assert parsed is not None
    assert parsed.peer_id == "20002"
    assert parsed.message.conversation_id == "qq:private:20002"


def test_ai_sent_receipt_does_not_enable_human_takeover(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        ai_ids = {("qq", "10001", "ai-receipt")}
        adapter, model, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            ai_ids=ai_ids,
        )

        await adapter.handle_event(
            mobile_self_event("ai-receipt", "模型刚发出的内容")
        )
        result = await adapter.handle_event(event("after-ai", "继续聊"))

        assert result is not None and result.reply == "候选回复"
        assert len(model.calls) == 1

    asyncio.run(scenario())


def test_training_group_is_read_only_and_other_group_is_ignored(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        output: list[str] = []
        adapter, model, _, _, styles = make_adapter(
            tmp_path / "chat.db",
            sink=output.append,
        )

        await adapter.handle_event(event("g1", group_id="90001"))
        await adapter.handle_event(event("g2", group_id="99999"))

        assert model.calls == []
        assert styles.count_group_events("90001") == 1
        assert styles.count_group_events("99999") == 0
        assert output == []

    asyncio.run(scenario())


def test_explicit_reply_group_enters_dispatcher_but_training_group_stays_read_only(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, _, styles = make_adapter(
            tmp_path / "chat.db",
            reply_groups=("80001",),
        )
        ignored = await adapter.handle_event(
            event("reply-group-context", "普通群消息", group_id="80001")
        )
        result = await adapter.handle_event(
            event(
                "reply-group-mention",
                group_id="80001",
                parts=[
                    at_self_part(),
                    {"type": "text", "data": {"text": "有人吗"}},
                ],
            )
        )
        await adapter.handle_event(
            event("training-group", "训练内容", group_id="90001")
        )

        assert ignored is None
        assert result is not None and result.reply == "候选回复"
        assert len(model.calls) == 1
        assert len(adapter.dry_run_records) == 1
        assert styles.count_group_events("90001") == 1
        assert "普通群消息" in repr(model.calls[0])
        assert "[@当前账号]" in repr(model.calls[0])

    asyncio.run(scenario())


def test_reply_group_can_receive_random_proactive_message(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            reply_groups=("80001",),
        )
        await adapter.handle_event(
            event("group-context", "群里的普通上下文", group_id="80001")
        )

        reply = await adapter.trigger_proactive_once(force=True)

        assert reply == "候选回复"
        assert len(model.calls) == 1
        assert model.calls[0][-1].content == "群里的普通上下文"
        assert len(adapter.dry_run_records) == 1
        assert adapter.dry_run_records[0].conversation_id == "qq:group:80001"

    asyncio.run(scenario())


def test_proactive_message_only_targets_explicit_known_contact(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            proactive_contacts=("20002",),
            proactive_limit=1,
        )
        await adapter.handle_event(event("known", "刚聊完"))
        proactive = await adapter.trigger_proactive_once(force=True)
        limited = await adapter.trigger_proactive_once(force=True)

        assert proactive == "候选回复"
        assert limited is None
        assert len(model.calls) == 2
        assert len(adapter.dry_run_records) == 2
        assert any(
            item.role == "system" and "主动发一条" in str(item.content)
            for item in model.calls[-1]
        )

    asyncio.run(scenario())


def test_proactive_message_never_targets_unlisted_contact(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            proactive_contacts=("20002",),
        )
        await adapter.handle_event(
            event("other", "普通联系人", user_id="30003")
        )

        assert await adapter.trigger_proactive_once(force=True) is None
        assert len(model.calls) == 1

    asyncio.run(scenario())


def test_zero_daily_limit_allows_repeated_proactive_cycles(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, _, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            proactive_contacts=("20002",),
            proactive_limit=0,
        )
        await adapter.handle_event(event("unlimited-context-1", "第一轮"))
        first = await adapter.trigger_proactive_once(force=True)
        await adapter.handle_event(event("unlimited-context-2", "第二轮"))
        second = await adapter.trigger_proactive_once(force=True)

        assert first == "候选回复"
        assert second == "候选回复"
        assert len(adapter.dry_run_records) == 4

    asyncio.run(scenario())


def test_empty_allowlist_schedules_each_ordinary_contact_at_random_interval(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = MutableClock()
        adapter, model, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            proactive_contacts=(),
            proactive_min=2400,
            proactive_max=4800,
            random_source=MidpointRandom(),
            monotonic_clock=clock,
            wall_clock=lambda: datetime(2026, 9, 8, 12, 0),
        )
        await adapter.handle_event(
            event("ordinary", "刚聊完", user_id="30003")
        )

        clock.value = 3599
        assert await adapter.trigger_proactive_once() is None
        clock.value = 3600
        assert await adapter.trigger_proactive_once() == "候选回复"
        assert await adapter.trigger_proactive_once() is None
        assert len(model.calls) == 2
        assert len(adapter.dry_run_records) == 2

    asyncio.run(scenario())


def test_new_contact_activity_resets_its_proactive_due_time(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = MutableClock()
        adapter, model, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            proactive_contacts=(),
            proactive_min=2400,
            proactive_max=4800,
            random_source=MidpointRandom(),
            monotonic_clock=clock,
            wall_clock=lambda: datetime(2026, 9, 8, 12, 0),
        )
        await adapter.handle_event(event("activity-1", "第一轮"))
        clock.value = 3500
        await adapter.handle_event(event("activity-2", "又来了一条"))
        clock.value = 3600

        assert await adapter.trigger_proactive_once() is None
        assert len(model.calls) == 2

    asyncio.run(scenario())


def test_proactive_exclusion_wins_over_recent_contact_discovery(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            proactive_contacts=(),
            proactive_excluded=("30003",),
        )
        await adapter.handle_event(
            event("excluded", "普通消息", user_id="30003")
        )

        assert await adapter.trigger_proactive_once(force=True) is None
        assert len(model.calls) == 1

    asyncio.run(scenario())


def test_proactive_message_is_suppressed_during_quiet_hours(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = MutableClock()
        adapter, model, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            proactive_contacts=(),
            random_source=MidpointRandom(),
            monotonic_clock=clock,
            wall_clock=lambda: datetime(2026, 9, 8, 1, 0),
        )
        await adapter.handle_event(event("quiet", "夜里发来的消息"))
        clock.value = 5000

        assert await adapter.trigger_proactive_once() is None
        assert len(model.calls) == 1

    asyncio.run(scenario())


def test_proactive_quiet_window_ends_at_seven_in_the_morning(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = MutableClock()
        current = {"value": datetime(2026, 9, 8, 23, 59)}
        adapter, model, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            proactive_contacts=("20002",),
            proactive_min=1,
            proactive_max=1,
            proactive_quiet_start="23:59",
            proactive_quiet_end="07:00",
            monotonic_clock=clock,
            wall_clock=lambda: current["value"],
        )
        await adapter.handle_event(event("quiet-boundary", "刚聊完"))
        clock.value = 1
        assert await adapter.trigger_proactive_once() is None
        current["value"] = datetime(2026, 9, 9, 6, 59)
        clock.value = 61
        assert await adapter.trigger_proactive_once() is None
        current["value"] = datetime(2026, 9, 9, 7, 0)
        clock.value = 121
        assert await adapter.trigger_proactive_once() == "候选回复"
        assert len(model.calls) == 2

    asyncio.run(scenario())


def test_proactive_message_can_use_saved_history_after_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, database, _ = make_adapter(
            tmp_path / "chat.db",
            proactive_contacts=("20002",),
        )
        database.save_message(
            "scope:qq:10001:qq:private:20002",
            Message(role="user", content="之前的聊天"),
        )
        await adapter.handle_event(
            {
                "post_type": "meta_event",
                "meta_event_type": "lifecycle",
                "self_id": "10001",
            }
        )

        assert await adapter.trigger_proactive_once(force=True) == "候选回复"
        assert len(model.calls) == 1
        assert len(adapter.dry_run_records) == 1

    asyncio.run(scenario())


def test_human_self_creates_candidate_without_model_call(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter, model, _, _, styles = make_adapter(tmp_path / "chat.db")
        for index in range(3):
            await adapter.handle_event(
                event(f"ctx-{index}", f"上下文{index}", group_id="90001")
            )

        await adapter.handle_event(
            event(
                "human-1",
                "那算了，改天吧",
                group_id="90001",
                self_message=True,
            )
        )

        samples = styles.list_samples()
        assert model.calls == []
        assert len(samples) == 1
        assert samples[0].status == "candidate"
        assert samples[0].human_reply == "那算了，改天吧"

    asyncio.run(scenario())


def test_ai_generated_message_can_never_be_training_target(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        ai_key = ("qq", "10001", "ai-1")
        ai_ids: set[tuple[str, str, str]] = set()
        adapter, model, _, _, styles = make_adapter(
            tmp_path / "chat.db",
            ai_ids=ai_ids,
        )
        ai_ids.add(ai_key)
        for index in range(3):
            await adapter.handle_event(
                event(f"before-{index}", f"上下文{index}", group_id="90001")
            )

        await adapter.handle_event(
            event(
                "ai-1",
                "模型写的消息",
                group_id="90001",
                self_message=True,
            )
        )

        assert model.calls == []
        assert styles.list_samples() == []

    asyncio.run(scenario())


def test_duplicate_self_event_does_not_duplicate_style_sample(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, _, _, _, styles = make_adapter(tmp_path / "chat.db")
        for index in range(3):
            await adapter.handle_event(
                event(f"dctx-{index}", f"上下文{index}", group_id="90001")
            )
        own = event(
            "own-duplicate",
            "我觉得可以",
            group_id="90001",
            self_message=True,
        )

        await adapter.handle_event(own)
        await adapter.handle_event(own)

        assert len(styles.list_samples()) == 1

    asyncio.run(scenario())


def test_group_contexts_are_isolated_and_never_enter_private_history(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, _, _, database, styles = make_adapter(
            tmp_path / "chat.db",
            training_groups=("90001", "90002"),
            context_min=1,
        )
        await adapter.handle_event(event("ga", "A群秘密", group_id="90001"))
        await adapter.handle_event(event("gb", "B群内容", group_id="90002"))
        await adapter.handle_event(
            event(
                "gb-self",
                "B群真人回复",
                group_id="90002",
                self_message=True,
            )
        )

        sample = styles.list_samples()[0]
        assert "B群内容" in repr(sample.context_messages)
        assert "A群秘密" not in repr(sample.context_messages)
        assert stored_messages(database, "qq:group:90001") == []
        assert stored_messages(database, "qq:private:20002") == []

    asyncio.run(scenario())


def test_private_and_group_conversation_ids_cannot_collide() -> None:
    private = parse_onebot_event(event("same", user_id="90001"))
    group = parse_onebot_event(event("same2", group_id="90001"))

    assert private is not None and group is not None
    assert private.message.conversation_id == "qq:private:90001"
    assert group.message.conversation_id == "qq:group:90001"
    assert private.message.conversation_id != group.message.conversation_id


def test_pure_private_image_enters_vision_chat_chain(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter, model, _, database, _ = make_adapter(tmp_path / "chat.db")

        await adapter.handle_event(event("image-only", parts=[image_part()]))

        assert len(model.calls) == 1
        user = [item for item in model.calls[0] if item.role == "user"][-1]
        assert not isinstance(user.content, str)
        assert user.content[0].part_type == "image"
        assert database.get_image_description(
            platform="qq",
            account_id="10001",
            message_id="image-only",
            part_index=0,
        ) == "候选回复"

    asyncio.run(scenario())


def test_private_text_and_image_stay_one_ordered_message(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, _, _ = make_adapter(tmp_path / "chat.db")
        parts = [
            {"type": "text", "data": {"text": "这是"}},
            image_part(),
            {"type": "text", "data": {"text": "什么"}},
        ]

        await adapter.handle_event(event("mixed", parts=parts))

        user = [item for item in model.calls[0] if item.role == "user"][-1]
        assert not isinstance(user.content, str)
        assert [part.part_type for part in user.content] == [
            "text", "image", "text"
        ]

    asyncio.run(scenario())


def test_duplicate_image_message_does_not_trigger_second_reply(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, _, _ = make_adapter(tmp_path / "chat.db")
        incoming = event("image-duplicate", parts=[image_part()])

        await adapter.handle_event(incoming)
        duplicate = await adapter.handle_event(incoming)

        assert duplicate is None
        assert len(model.calls) == 1
        assert len(adapter.dry_run_records) == 1

    asyncio.run(scenario())


def test_failed_image_falls_back_to_text_without_crashing(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter, model, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            image_resolver=FailingImageResolver(),
        )
        parts = [
            image_part(),
            {"type": "text", "data": {"text": "这是什么"}},
        ]

        result = await adapter.handle_event(event("failed-image", parts=parts))

        assert result is not None
        assert len(model.calls) == 1
        user = [item for item in model.calls[0] if item.role == "user"][-1]
        assert not isinstance(user.content, str)
        assert user.content[0].available is False
        assert user.content[0].description == "图片加载失败，模型未看到图片"
        assert user.content[1].text == "这是什么"

    asyncio.run(scenario())


def test_failed_pure_image_is_recorded_without_claiming_vision(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, database, _ = make_adapter(
            tmp_path / "chat.db",
            image_resolver=FailingImageResolver(),
        )

        image_result = await adapter.handle_event(
            event("failed-pure-image", parts=[image_part()])
        )
        assert image_result is None
        assert model.calls == []
        stored = stored_messages(database, "qq:private:20002")
        assert "模型未看到图片" in stored[-1].content

        await adapter.handle_event(event("after-failed-image", "里面是谁"))

        assert len(model.calls) == 1
        assert "模型未看到图片" in repr(model.calls[0])

    asyncio.run(scenario())


def test_training_group_image_is_metadata_only_and_not_private_memory(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, model, _, database, styles = make_adapter(
            tmp_path / "chat.db",
            image_resolver=FailingImageResolver(),
        )

        await adapter.handle_event(
            event("group-image", group_id="90001", parts=[image_part()])
        )

        assert model.calls == []
        assert styles.count_group_events("90001") == 1
        assert stored_messages(database, "qq:group:90001") == []
        assert stored_messages(database, "qq:private:20002") == []

    asyncio.run(scenario())


def test_style_export_contains_only_accepted_and_redacted_fields(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter, _, _, _, styles = make_adapter(
            tmp_path / "chat.db",
            context_min=1,
        )
        await adapter.handle_event(
            event("export-context", "联系12345678", group_id="90001")
        )
        await adapter.handle_event(
            event(
                "export-self",
                "那就这么办",
                nickname="真实昵称",
                group_id="90001",
                self_message=True,
            )
        )
        sample = styles.list_samples()[0]
        assert styles.set_status(sample.sample_id, "accepted")
        target = tmp_path / "samples.jsonl"

        assert styles.export_accepted(target) == 1
        exported = target.read_text(encoding="utf-8")
        assert "[已脱敏]" in exported
        for secret in (
            "12345678", "90001", "10001", "真实昵称",
            "export-context", "export-self",
        ):
            assert secret not in exported
        assert '"context"' in exported
        assert '"response"' in exported
        assert '"conversation_type": "group"' in exported

    asyncio.run(scenario())


def test_event_reader_does_not_wait_for_previous_model_generation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        model = StaleAwareModel()

        async def source():
            yield event("slow-first", "第一条")
            await model.first_started.wait()
            yield event("new-second", "补充消息")
            for _ in range(50):
                if len(model.calls) >= 2:
                    break
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)

        adapter, _, sender, _, _ = make_adapter(
            tmp_path / "chat.db",
            model_override=model,
            event_source=source(),
        )

        await adapter.run()

        assert len(model.calls) == 2
        assert sender.calls == []
        assert [record.text for record in adapter.dry_run_records] == ["最新回复"]

    asyncio.run(scenario())


def test_model_timeout_has_specific_console_message(tmp_path: Path) -> None:
    async def scenario() -> None:
        output: list[str] = []
        adapter, _, _, _, _ = make_adapter(
            tmp_path / "chat.db",
            model_override=TimeoutModel(),
            sink=output.append,
        )

        await adapter._handle_event_safely(event("timeout"))

        assert any(line.startswith("[QQ 模型错误]") for line in output)
        assert not any("事件处理失败" in line for line in output)

    asyncio.run(scenario())
