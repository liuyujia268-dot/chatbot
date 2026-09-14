"""Async QQ runtime hosted behind the Tkinter event queue."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.adapters.image_resolver import ImageResolver
from app.adapters.onebot import ParsedQQEvent, parse_onebot_event
from app.adapters.outbound import MessageSender, NapCatMessageSender
from app.adapters.qq import QQAdapter
from app.adapters.websocket_client import OneBotWebSocketEvents
from app.config import Settings
from app.core.platform import OutboundMessage, PlatformMessage
from app.runtime import CoreRuntime, build_core_runtime


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UiEvent:
    kind: str
    payload: Any = None


def parse_onebot_history_events(
    events: list[Any],
    *,
    account_id: str,
    conversation_id: str,
) -> tuple[ParsedQQEvent, ...]:
    """Normalize NapCat history records for the desktop projection only."""
    if conversation_id.startswith("qq:private:"):
        conversation_type = "private"
        peer_id = conversation_id.removeprefix("qq:private:")
    elif conversation_id.startswith("qq:group:"):
        conversation_type = "group"
        peer_id = conversation_id.removeprefix("qq:group:")
    else:
        return ()
    parsed_events: list[ParsedQQEvent] = []
    for raw in events:
        if not isinstance(raw, dict):
            continue
        event = dict(raw)
        event.setdefault("self_id", account_id)
        event.setdefault("message_type", conversation_type)
        if conversation_type == "private":
            if event.get("post_type") == "message_sent":
                event["target_id"] = peer_id
        else:
            event.setdefault("group_id", peer_id)
        parsed = parse_onebot_event(event)
        if (
            parsed is not None
            and parsed.message.account_id == account_id
            and parsed.message.conversation_id == conversation_id
        ):
            parsed_events.append(parsed)
    parsed_events.sort(
        key=lambda item: (
            item.message.timestamp,
            item.message.message_id,
        )
    )
    return tuple(parsed_events)


class _ObservableEventSource:
    def __init__(self, source, callback) -> None:
        self._source = source
        self._callback = callback

    async def __aiter__(self):
        async for event in self._source:
            self._callback(event)
            yield event


class _SwitchableSender:
    """Runtime Dry Run gate with a UI notification after every safe send."""

    def __init__(
        self,
        live_sender: MessageSender,
        callback,
        *,
        dry_run: bool,
    ) -> None:
        self._live_sender = live_sender
        self._callback = callback
        self.dry_run = dry_run
        self.records: list[OutboundMessage] = []

    async def send(self, message: OutboundMessage) -> str | None:
        message_id: str | None = None
        if self.dry_run:
            self.records.append(message)
        else:
            message_id = await self._live_sender.send(message)
        self._callback(message, self.dry_run, message_id)
        return message_id


class DesktopRuntime:
    """Own an asyncio loop in a worker thread while Tk owns the main thread."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.events: queue.Queue[UiEvent] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown: asyncio.Event | None = None
        self._core: CoreRuntime | None = None
        self._qq: QQAdapter | None = None
        self._ai_sender: _SwitchableSender | None = None
        self._manual_sender: _SwitchableSender | None = None
        self._onebot: OneBotWebSocketEvents | None = None
        self._ai_generated_ids: set[tuple[str, str, str]] = set()
        self._account_id: str | None = None
        self._directory_task: asyncio.Task[None] | None = None
        self._history_inflight: set[str] = set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def account_id(self) -> str | None:
        return self._account_id

    def start(self) -> None:
        if self.running:
            return
        self._thread = threading.Thread(
            target=self._thread_main,
            name="chatbot-qq-runtime",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        loop = self._loop
        shutdown = self._shutdown
        if loop is not None and shutdown is not None:
            try:
                loop.call_soon_threadsafe(shutdown.set)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def send_message(self, conversation_id: str, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        self._submit(self._send_manual(conversation_id, clean))

    def load_history(self, conversation_id: str) -> None:
        self._submit(self._load_history(conversation_id))

    def set_auto_reply(self, enabled: bool) -> None:
        self._call_qq("set_auto_reply_enabled", enabled)

    def set_proactive(self, enabled: bool) -> None:
        self._call_qq("set_proactive_enabled", enabled)

    def set_followup(self, enabled: bool) -> None:
        self._call_qq("set_followup_enabled", enabled)

    def set_dry_run(self, enabled: bool) -> None:
        def apply() -> None:
            if self._ai_sender is not None:
                self._ai_sender.dry_run = bool(enabled)
            if self._manual_sender is not None:
                self._manual_sender.dry_run = bool(enabled)
            self._emit("dry_run", bool(enabled))

        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(apply)

    def set_human_takeover(self, conversation_id: str, enabled: bool) -> None:
        def apply() -> None:
            if self._core is None:
                return
            self._core.merger.set_human_takeover(conversation_id, bool(enabled))
            self._emit(
                "takeover",
                {"conversation_id": conversation_id, "enabled": bool(enabled)},
            )

        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(apply)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception as exc:
            logger.exception("desktop_runtime_failed")
            self._emit("fatal", str(exc))

    async def _async_main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._shutdown = asyncio.Event()
        self._core = build_core_runtime(self.settings)
        onebot = OneBotWebSocketEvents(
            self.settings.qq_ws_url,
            self.settings.qq_ws_token,
            status_callback=self._on_status,
        )
        self._onebot = onebot
        ai_live = NapCatMessageSender(
            onebot,
            on_sent=lambda account_id, message_id: self._ai_generated_ids.add(
                ("qq", account_id, message_id)
            ),
        )
        manual_live = NapCatMessageSender(onebot)
        self._ai_sender = _SwitchableSender(
            ai_live,
            self._on_outbound,
            dry_run=self.settings.qq_dry_run,
        )
        self._manual_sender = _SwitchableSender(
            manual_live,
            self._on_outbound,
            dry_run=self.settings.qq_dry_run,
        )
        event_source = _ObservableEventSource(onebot, self._on_raw_event)
        self._qq = QQAdapter(
            merger=self._core.merger,
            database=self._core.database,
            style_repository=self._core.style_repository,
            image_resolver=ImageResolver(
                max_bytes=self.settings.qq_image_max_bytes,
                timeout_seconds=self.settings.qq_image_timeout_seconds,
                allow_remote_url=self.settings.qq_image_allow_remote_url,
            ),
            primary_contact_id=self.settings.qq_primary_contact_id,
            primary_contact_name=self.settings.qq_primary_contact_name,
            admin_contact_ids=self.settings.qq_admin_contact_ids,
            admin_memory_prefix=self.settings.qq_admin_memory_prefix,
            memory_service=self._core.memory_service,
            training_group_ids=self.settings.qq_training_group_ids,
            reply_group_ids=self.settings.qq_reply_group_ids,
            dry_run=False,
            auto_reply_enabled=self.settings.qq_auto_reply_enabled,
            proactive_enabled=self.settings.qq_proactive_enabled,
            proactive_contact_ids=self.settings.qq_proactive_contact_ids,
            proactive_exclude_contact_ids=(
                self.settings.qq_proactive_exclude_contact_ids
            ),
            proactive_min_seconds=self.settings.qq_proactive_min_seconds,
            proactive_max_seconds=self.settings.qq_proactive_max_seconds,
            proactive_daily_limit=self.settings.qq_proactive_daily_limit,
            proactive_recent_days=self.settings.qq_proactive_recent_days,
            proactive_quiet_start=self.settings.qq_proactive_quiet_start,
            proactive_quiet_end=self.settings.qq_proactive_quiet_end,
            proactive_prompt=self._core.proactive_prompt,
            followup_enabled=self.settings.qq_followup_enabled,
            followup_probability=self.settings.qq_followup_probability,
            followup_min_delay_seconds=(
                self.settings.qq_followup_min_delay_seconds
            ),
            followup_max_delay_seconds=(
                self.settings.qq_followup_max_delay_seconds
            ),
            followup_prompt=self._core.followup_prompt,
            event_source=event_source,
            sender=self._ai_sender,
            sink=lambda text: logger.info("qq_ui_event %s", text.splitlines()[0]),
            ai_generated_message_ids=self._ai_generated_ids,
        )
        task = asyncio.create_task(self._qq.run())
        self._emit("runtime_ready")
        try:
            await self._shutdown.wait()
        finally:
            task.cancel()
            if self._directory_task is not None:
                self._directory_task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self._core.dispatcher.finish_background_tasks()
            self._emit("status", ("stopped", None))

    def _on_status(self, status: str, detail: str | None) -> None:
        self._emit("status", (status, detail))
        if status == "connected":
            if self._directory_task is not None:
                self._directory_task.cancel()
            self._directory_task = asyncio.create_task(self._load_directory())

    async def _load_directory(self) -> None:
        client = self._onebot
        if client is None:
            return
        try:
            login = await client.call_action("get_login_info", {})
            if not isinstance(login, dict):
                raise RuntimeError("NapCat 未返回登录账号信息")
            account_id = str(login.get("user_id", "")).strip()
            if not account_id:
                raise RuntimeError("NapCat 当前没有已登录 QQ 账号")
            self._account_id = account_id
            self._emit(
                "login",
                {
                    "account_id": account_id,
                    "nickname": str(login.get("nickname", "")).strip(),
                },
            )
            friends = await client.call_action("get_friend_list", {})
            groups = await client.call_action("get_group_list", {})
            self._emit(
                "directory",
                {
                    "account_id": account_id,
                    "friends": friends if isinstance(friends, list) else [],
                    "groups": groups if isinstance(groups, list) else [],
                },
            )
        except Exception as exc:
            logger.exception("qq_directory_load_failed")
            self._emit("directory_error", str(exc))

    async def _load_history(self, conversation_id: str) -> None:
        client = self._onebot
        account_id = self._account_id
        if (
            client is None
            or not account_id
            or conversation_id in self._history_inflight
        ):
            return
        if conversation_id.startswith("qq:private:"):
            action = "get_friend_msg_history"
            identifier_name = "user_id"
            peer_id = conversation_id.removeprefix("qq:private:")
        elif conversation_id.startswith("qq:group:"):
            action = "get_group_msg_history"
            identifier_name = "group_id"
            peer_id = conversation_id.removeprefix("qq:group:")
        else:
            return
        self._history_inflight.add(conversation_id)
        try:
            data = await client.call_action(
                action,
                {
                    identifier_name: peer_id,
                    "message_seq": "0",
                    "count": self.settings.desktop_history_message_limit,
                    "reverse_order": False,
                    "disable_get_url": False,
                },
            )
            raw_messages = (
                data.get("messages", [])
                if isinstance(data, dict)
                else []
            )
            messages = parse_onebot_history_events(
                raw_messages if isinstance(raw_messages, list) else [],
                account_id=account_id,
                conversation_id=conversation_id,
            )
            self._emit(
                "history",
                {
                    "account_id": account_id,
                    "conversation_id": conversation_id,
                    "messages": messages,
                },
            )
        except Exception as exc:
            logger.warning(
                "qq_history_load_failed conversation=%s error_type=%s",
                conversation_id,
                type(exc).__name__,
            )
            self._emit(
                "history_error",
                {
                    "account_id": account_id,
                    "conversation_id": conversation_id,
                },
            )
        finally:
            self._history_inflight.discard(conversation_id)

    def _on_raw_event(self, event: dict[str, Any]) -> None:
        parsed = parse_onebot_event(event)
        if parsed is not None:
            self._account_id = parsed.message.account_id
            key = parsed.message.deduplication_key
            source_type = (
                "ai_generated"
                if key in self._ai_generated_ids
                else ("human_self" if parsed.message.is_self else "other_user")
            )
            self._emit(
                "message",
                {"parsed": parsed, "source_type": source_type},
            )

    def _on_outbound(
        self,
        message: OutboundMessage,
        dry_run: bool,
        message_id: str | None,
    ) -> None:
        self._emit(
            "outbound",
            {
                "message": message,
                "dry_run": dry_run,
                "event_key": (
                    f"platform:{message_id}"
                    if message_id
                    else f"out:{uuid4().hex}"
                ),
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    async def _send_manual(self, conversation_id: str, text: str) -> None:
        sender = self._manual_sender
        account_id = self._account_id
        if sender is None or not account_id:
            self._emit("send_error", "QQ 尚未登录或 WebSocket 未连接")
            return
        assert self._core is not None
        self._core.merger.interrupt_current_reply(conversation_id)
        message = OutboundMessage(
            platform="qq",
            account_id=account_id,
            conversation_id=conversation_id,
            text=text,
            metadata={"source_type": "human_self", "sender_name": "我"},
        )
        try:
            message_id = await sender.send(message)
            self._core.merger.record_human_self_platform(
                PlatformMessage(
                    platform="qq",
                    account_id=account_id,
                    conversation_id=conversation_id,
                    sender_id=account_id,
                    message_id=message_id or f"desktop-manual-{uuid4().hex}",
                    timestamp=datetime.now(UTC),
                    message_type="text",
                    text=text,
                    is_self=True,
                )
            )
        except Exception as exc:
            logger.exception("desktop_manual_send_failed")
            self._emit("send_error", str(exc))

    def _call_qq(self, method: str, enabled: bool) -> None:
        def apply() -> None:
            if self._qq is not None:
                getattr(self._qq, method)(bool(enabled))

        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(apply)

    def _submit(self, coroutine) -> None:
        loop = self._loop
        if loop is None:
            coroutine.close()
            self._emit("send_error", "QQ 连接尚未初始化")
            return
        asyncio.run_coroutine_threadsafe(coroutine, loop)

    def _emit(self, kind: str, payload: Any = None) -> None:
        self.events.put(UiEvent(kind=kind, payload=payload))
