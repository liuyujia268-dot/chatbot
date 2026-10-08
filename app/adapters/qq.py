"""NapCat/OneBot QQ adapter with explicit private and group reply rules."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
import time
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.adapters.base import PlatformAdapter
from app.adapters.image_resolver import ImageResolutionError, ImageResolver
from app.adapters.onebot import ParsedQQEvent, parse_onebot_event
from app.adapters.outbound import (
    DryRunMessageSender,
    MessageSender,
    split_outbound_text,
)
from app.core.dispatcher import ContinuousMessageMerger, DispatchResult
from app.core.dispatcher import _replies_too_similar
from app.core.platform import OutboundMessage
from app.core.identity import conversation_key, account_key_for
from app.memory.service import MemoryService
from app.storage.database import Database
from app.style.repository import QQGroupEvent, StyleRepository
from app.models.base import ModelError
from app.adapters.delivery import DeliveryService
from uuid import uuid4


logger = logging.getLogger(__name__)

_PROFILE_IMAGE_REQUEST_PHRASES = (
    "你的资料",
    "你资料",
    "鲸鱼娘资料",
    "个人资料",
    "发资料",
    "资料图",
    "你的照片",
    "你照片",
    "发张照片",
    "发个照片",
    "照片呢",
    "你的图片",
    "你图片",
    "发张图",
    "发个图",
    "你长什么样",
    "鲸鱼娘长什么样",
    "看看你",
    "看下你",
    "看看鲸鱼娘",
)
_PROFILE_IMAGE_EXTENSIONS = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})


@dataclass(frozen=True, slots=True)
class _ProactiveTarget:
    account_id: str
    conversation_id: str
    target_id: str
    target_type: str
    display_name: str
    nickname: str | None


class QQAdapter(PlatformAdapter):
    def __init__(
        self,
        *,
        merger: ContinuousMessageMerger,
        database: Database,
        style_repository: StyleRepository,
        image_resolver: ImageResolver,
        primary_contact_id: str | None,
        primary_contact_name: str,
        training_group_ids: tuple[str, ...],
        admin_contact_ids: tuple[str, ...] = (),
        admin_memory_prefix: str = "#指令",
        admin_config_prefix: str = "#配置",
        admin_config_writer: Callable[[dict[str, str]], None] | None = None,
        memory_service: MemoryService | None = None,
        profile_asset_root: Path | None = None,
        reply_group_ids: tuple[str, ...] = (),
        dry_run: bool = True,
        auto_reply_enabled: bool = True,
        proactive_enabled: bool = False,
        proactive_contact_ids: tuple[str, ...] = (),
        proactive_exclude_contact_ids: tuple[str, ...] = (),
        proactive_min_seconds: float = 1800.0,
        proactive_max_seconds: float = 3600.0,
        proactive_daily_limit: int = 0,
        proactive_recent_days: int = 7,
        proactive_quiet_start: str = "23:59",
        proactive_quiet_end: str = "07:00",
        proactive_prompt: str = "",
        followup_enabled: bool = False,
        followup_probability: float = 0.15,
        followup_min_delay_seconds: float = 2.0,
        followup_max_delay_seconds: float = 7.0,
        followup_prompt: str = "",
        event_source: AsyncIterable[dict[str, Any]] | None = None,
        sender: MessageSender | None = None,
        sink: Callable[[str], None] = print,
        ai_generated_message_ids: set[tuple[str, str, str]] | None = None,
        random_source: random.Random | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
        event_callback: Callable[[str, Any], None] | None = None,
        image_wait_seconds: float = 12.0,
    ) -> None:
        self._merger = merger
        self._database = database
        self._styles = style_repository
        self._images = image_resolver
        self._image_wait_seconds = image_wait_seconds
        self._group_requests: dict[str, tuple[str, str]] = {}
        self._primary_id = primary_contact_id
        self._primary_name = primary_contact_name
        self._admin_ids = frozenset(admin_contact_ids)
        self._admin_memory_prefix = admin_memory_prefix.strip() or "#指令"
        self._admin_config_prefix = admin_config_prefix.strip() or "#配置"
        self._admin_config_writer = admin_config_writer
        self._memory_service = memory_service
        self._profile_asset_root = (profile_asset_root or Path.cwd()).resolve()
        self._training_groups = frozenset(training_group_ids)
        self._reply_groups = frozenset(reply_group_ids)
        self._event_source = event_source
        concrete_sender = sender or _ForbiddenSender()
        if not dry_run and sender is None:
            raise ValueError("real QQ sending requires a configured sender")
        self._sender = (
            DryRunMessageSender(concrete_sender) if dry_run else concrete_sender
        )
        self._dry_run = dry_run
        self._event_callback = event_callback or (lambda *args: None)
        self._delivery = DeliveryService(database, callback=lambda record: self._event_callback("delivery", record),
                                         on_history=merger.accept_delivered_history)
        self._delivery.recover_pending()
        self._merger.set_simulation_mode(bool(getattr(self._sender, 'dry_run', False)))
        self._auto_reply_enabled = auto_reply_enabled
        self._sink = sink
        self._ai_generated_ids = (
            ai_generated_message_ids
            if ai_generated_message_ids is not None
            else set()
        )
        self._proactive_enabled = proactive_enabled
        self._proactive_ids = frozenset(proactive_contact_ids)
        self._proactive_excluded_ids = frozenset(
            proactive_exclude_contact_ids
        )
        self._proactive_min = proactive_min_seconds
        self._proactive_max = proactive_max_seconds
        self._proactive_daily_limit = proactive_daily_limit
        self._proactive_recent_days = proactive_recent_days
        self._proactive_prompt = proactive_prompt.strip()
        self._quiet_start_text = proactive_quiet_start
        self._quiet_end_text = proactive_quiet_end
        self._quiet_start = self._minutes(proactive_quiet_start)
        self._quiet_end = self._minutes(proactive_quiet_end)
        self._followup_enabled = followup_enabled
        self._followup_probability = followup_probability
        self._followup_min_delay = followup_min_delay_seconds
        self._followup_max_delay = followup_max_delay_seconds
        self._followup_prompt = followup_prompt.strip()
        self._random = random_source or random.Random()
        self._monotonic = monotonic_clock or time.monotonic
        self._wall_clock = wall_clock or (lambda: datetime.now().astimezone())
        self._known_private_contacts: dict[str, _ProactiveTarget] = {}
        self._proactive_targets: dict[str, _ProactiveTarget] = {}
        self._next_proactive_at: dict[str, float] = {}
        self._proactive_wakeup = asyncio.Event()
        self._account_id: str | None = None
        self._account_epoch = 0
        self._proactive_epoch = 0
        self._followup_epoch = 0
        self._active_session_ids: set[str] = set()
        self._proactive_counts: dict[str, int] = {}
        self._proactive_day = self._wall_clock().date()
        if proactive_min_seconds > proactive_max_seconds:
            raise ValueError("proactive minimum cannot exceed maximum")
        if proactive_daily_limit < 0:
            raise ValueError("proactive daily limit must not be negative")
        if proactive_recent_days < 1:
            raise ValueError("proactive recent days must be at least 1")
        if proactive_enabled and not self._proactive_prompt:
            raise ValueError("proactive prompt is required when enabled")
        if not 0 <= followup_probability <= 1:
            raise ValueError("follow-up probability must be between 0 and 1")
        if (
            followup_min_delay_seconds < 0
            or followup_min_delay_seconds > followup_max_delay_seconds
        ):
            raise ValueError("follow-up delay range is invalid")
        if followup_enabled and not self._followup_prompt:
            raise ValueError("follow-up prompt is required when enabled")
        if self._admin_ids and self._memory_service is None:
            raise ValueError("admin memory commands require memory service")

    @property
    def dry_run_records(self) -> tuple[OutboundMessage, ...]:
        if isinstance(self._sender, DryRunMessageSender):
            return tuple(self._sender.records)
        return ()

    @property
    def dry_run(self) -> bool:
        return bool(getattr(self._sender, 'dry_run', self._dry_run))

    @property
    def account_id(self) -> str | None:
        return self._account_id

    def _notify_controls(self) -> None:
        self._event_callback('adapter_controls', {'dry_run': self.dry_run, 'auto_reply': self._auto_reply_enabled,
                             'proactive': self._proactive_enabled, 'followup': self._followup_enabled})

    @property
    def auto_reply_enabled(self) -> bool:
        return self._auto_reply_enabled

    @property
    def proactive_enabled(self) -> bool:
        return self._proactive_enabled

    @property
    def followup_enabled(self) -> bool:
        return self._followup_enabled

    def set_auto_reply_enabled(self, enabled: bool) -> None:
        self._auto_reply_enabled = bool(enabled)
        if not enabled:
            for session_id in tuple(self._active_session_ids):
                self._merger.interrupt_current_reply(session_id)
        self._notify_controls()

    def set_proactive_enabled(self, enabled: bool) -> None:
        self._proactive_enabled = bool(enabled)
        if not enabled:
            self._proactive_epoch += 1
        self._proactive_wakeup.set()
        self._notify_controls()

    def set_followup_enabled(self, enabled: bool) -> None:
        self._followup_enabled = bool(enabled)
        if not enabled:
            self._followup_epoch += 1
        self._notify_controls()

    def activate_account(self, account_id: str | None) -> None:
        if account_id == self._account_id:
            return
        for session_id in tuple(self._active_session_ids):
            self._merger.interrupt_current_reply(session_id)
        self._active_session_ids.clear()
        self._account_epoch += 1
        self._account_id = account_id
        self._known_private_contacts.clear()
        self._proactive_targets.clear()
        self._next_proactive_at.clear()
        self._proactive_counts.clear()
        self._group_requests.clear()
        self._load_saved_proactive_targets()

    async def run(self) -> None:
        if self._event_source is None:
            raise RuntimeError("QQ event source is not configured")
        tasks: set[asyncio.Task[None]] = set()
        proactive_task = asyncio.create_task(self._run_proactive_loop())
        try:
            async for event in self._event_source:
                task = asyncio.create_task(self._handle_event_safely(event))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        finally:
            proactive_task.cancel()
            for task in tasks:
                task.cancel()
            pending = [*tasks]
            pending.append(proactive_task)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def _handle_event_safely(self, event: dict[str, Any]) -> None:
        try:
            await self.handle_event(event)
        except ModelError as exc:
            logger.warning("qq_model_error error=%s", type(exc).__name__)
            self._model_error(event, exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("qq_event_processing_failed")
            self._sink("[QQ] 事件处理失败，已跳过")

    def _model_error(self, event: dict[str, Any], exc: ModelError) -> None:
        parsed = parse_onebot_event(event)
        if parsed is None:
            return
        self._notify_model_error(parsed.message.account_id, parsed.message.conversation_id, exc)

    def _notify_model_error(self, account_id: str, conversation_id: str, exc: ModelError) -> None:
        hints = {
            "ModelTimeoutError": "模型超时；检查服务状态后发送新消息重试",
            "ModelAuthenticationError": "模型认证失败；检查 MODEL_API_KEY，然后重连",
            "ModelNotFoundError": "模型不存在；检查 MODEL_NAME 和 MODEL_BASE_URL，然后重连",
            "ModelConnectionError": "无法连接模型；检查 MODEL_BASE_URL 与服务状态",
            "ModelNetworkConfigurationError": "模型代理/网络配置失败；在设置检查模型网络模式和代理地址，保存后重连；缺少 SOCKS 支持时更新完整发行包",
        }
        hint = hints.get(type(exc).__name__, "模型返回异常；检查模型配置或服务状态后重试")
        self._sink(f"[QQ 模型错误] {hint}")
        self._event_callback("model_error", {"account_id": account_id,
            "conversation_id": conversation_id, "code": type(exc).__name__, "message": hint})

    async def handle_event(self, event: dict[str, Any]) -> DispatchResult | None:
        account_id = str(event.get("self_id", "")).strip()
        if account_id:
            if self._account_id is not None and self._account_id != account_id:
                return None  # a delayed event is not proof of a new login
            self.activate_account(account_id)
        parsed = parse_onebot_event(event)
        if parsed is None:
            return None
        self._active_session_ids.add(parsed.message.session_id)
        if parsed.conversation_type == "private":
            return await self._handle_private(parsed)
        return await self._handle_group(parsed)

    async def _handle_private(
        self,
        parsed: ParsedQQEvent,
    ) -> DispatchResult | None:
        message = parsed.message
        contact_id = parsed.peer_id
        is_primary = (
            self._primary_id is not None
            and contact_id == self._primary_id
        )
        display_name = (
            self._primary_name
            if is_primary
            else (
                parsed.nickname
                if not message.is_self and parsed.nickname
                else contact_id
            )
        )
        if message.is_self:
            self._register_private_target(
                account_id=message.account_id,
                user_id=contact_id,
                display_name=display_name,
                nickname=None,
                reset=True,
            )
            source_type = self._source_type(
                message.deduplication_key,
                message.is_self,
            )
            if source_type == "ai_generated":
                self._database.save_inbound_platform_event(message)
                return None
            if self._merger.record_human_self_platform(message):
                self._sink(
                    "[QQ HUMAN TAKEOVER]\n"
                    f"联系人：{display_name}\n"
                    f"user_id：{contact_id}\n"
                    "检测到当前账号人工发送，已暂停该会话自动回复"
                )
            return None
        self._sink(
            f"[QQ PRIVATE]\n{display_name} ({message.sender_id}):\n"
            f"{message.storage_text}"
        )
        self._register_private_target(
            account_id=message.account_id,
            user_id=contact_id,
            display_name=display_name,
            nickname=parsed.nickname,
            reset=True,
        )
        if self._primary_id is None:
            self._sink(
                "[QQ PRIMARY CANDIDATE]\n"
                f"nickname={parsed.nickname or '-'}\n"
                f"user_id={message.sender_id}\n"
                f"conversation_id={message.conversation_id}\n"
                "请人工确认后写入 QQ_PRIMARY_CONTACT_ID"
            )
        admin_config = self._admin_config_command(parsed)
        if admin_config is not None:
            await self._handle_admin_config(message, admin_config)
            return None
        admin_instruction = self._admin_instruction(parsed)
        if admin_instruction is not None:
            self._record_admin_memory(message, admin_instruction)
            return None
        if not self._auto_reply_enabled:
            self._merger.record_platform(message)
            return None

        # Dedup, arrival order and request version are registered before media I/O.
        result = await self._merger.submit_platform(message, defer_commit=True, prepare=self._prepare_images)
        if result is not None and result.skip_reason == 'unsupported_media':
            return None
        if result is not None and result.reply is not None:
            await self._send_result(parsed, result, display_name)
        return result

    async def _run_proactive_loop(self) -> None:
        while True:
            try:
                if not self._proactive_enabled:
                    await self._proactive_wakeup.wait()
                    self._proactive_wakeup.clear()
                    continue
                self._load_saved_proactive_targets()
                due_values = [
                    due
                    for conversation_id, due in self._next_proactive_at.items()
                    if conversation_id in self._proactive_targets
                    and not self._merger.is_human_takeover(
                        conversation_key("qq", self._account_id or "unknown", conversation_id)
                    )
                    and self._has_proactive_capacity(conversation_id)
                ]
                if not due_values:
                    try:
                        await asyncio.wait_for(
                            self._proactive_wakeup.wait(),
                            timeout=60.0,
                        )
                        self._proactive_wakeup.clear()
                    except TimeoutError:
                        self._reset_proactive_counts_if_needed()
                    continue
                timeout = max(0.0, min(due_values) - self._monotonic())
                try:
                    await asyncio.wait_for(
                        self._proactive_wakeup.wait(),
                        timeout=min(timeout, 60.0),
                    )
                    self._proactive_wakeup.clear()
                    continue
                except TimeoutError:
                    if timeout <= 60.0:
                        await self.trigger_proactive_once()
            except ModelError as exc:
                logger.warning(
                    "qq_proactive_model_error error=%s",
                    type(exc).__name__,
                )
                # trigger_proactive_once emitted the safe, conversation-scoped hint.
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("qq_proactive_failed")
                self._sink("[QQ] 主动消息生成或发送失败，已跳过")

    async def trigger_proactive_once(self, *, force: bool = False) -> str | None:
        """Send at most one due proactive message."""
        self._reset_proactive_counts_if_needed()
        self._load_saved_proactive_targets()
        now = self._monotonic()
        if not self._proactive_enabled or (not force and self._is_quiet_time()):
            for conversation_id, due in tuple(
                self._next_proactive_at.items()
            ):
                if due <= now:
                    self._next_proactive_at[conversation_id] = now + 60.0
            return None
        candidates = [
            contact
            for conversation_id, contact in self._proactive_targets.items()
            if self._has_proactive_capacity(conversation_id)
            and not self._merger.is_human_takeover(
                conversation_key("qq", contact.account_id, conversation_id)
            )
            and (
                force
                or self._next_proactive_at.get(conversation_id, float("inf"))
                <= now
            )
        ]
        if not candidates:
            return None
        contact = min(
            candidates,
            key=lambda item: self._next_proactive_at.get(
                item.conversation_id,
                now,
            ),
        )
        session_id = conversation_key("qq", contact.account_id, contact.conversation_id)
        self._active_session_ids.add(session_id)
        version = self._merger.conversation_version(session_id)
        epoch = (self._account_epoch, self._proactive_epoch)
        try:
            reply = await self._merger.generate_proactive(
                session_id,
                self._proactive_prompt,
                defer_commit=True,
            )
        except ModelError as exc:
            self._notify_model_error(contact.account_id, contact.conversation_id, exc)
            if contact.account_id == self._account_id:
                self._schedule_proactive(contact.conversation_id)
            raise
        if reply is None:
            self._schedule_proactive(contact.conversation_id)
            return None
        sent = await self._send_generated(
            OutboundMessage(
                platform="qq",
                account_id=contact.account_id,
                conversation_id=contact.conversation_id,
                text=reply,
            ),
            expected_version=version,
            purpose="proactive",
            epoch=epoch,
        )
        if not sent:
            return None
        self._proactive_counts[contact.conversation_id] = (
            self._proactive_counts.get(contact.conversation_id, 0) + 1
        )
        self._schedule_proactive(contact.conversation_id)
        mode = "DRY RUN" if self.dry_run else "SENT"
        if contact.target_type == "group":
            self._sink(
                f"[QQ GROUP PROACTIVE {mode}]\n"
                f"group={contact.target_id}\n"
                f"消息：{reply}"
            )
        else:
            self._sink(
                f"[QQ PROACTIVE {mode}]\n"
                f"联系人：{contact.display_name}\n"
                f"nickname：{contact.nickname or '-'}\n"
                f"user_id：{contact.target_id}\n"
                f"消息：{reply}"
            )
        return reply

    def _admin_instruction(self, parsed: ParsedQQEvent) -> str | None:
        return self._admin_command(parsed, self._admin_memory_prefix)

    def _admin_config_command(self, parsed: ParsedQQEvent) -> str | None:
        return self._admin_command(parsed, self._admin_config_prefix)

    def _admin_command(
        self,
        parsed: ParsedQQEvent,
        prefix: str,
    ) -> str | None:
        message = parsed.message
        if (
            message.sender_id not in self._admin_ids
            or any(part.part_type != "text" for part in message.parts)
        ):
            return None
        text = "".join(part.text or "" for part in message.parts).strip()
        if not text.startswith(prefix):
            return None
        remainder = text[len(prefix):]
        if remainder and not (remainder[0].isspace() or remainder[0] == "["):
            return None
        detail = remainder.strip()
        if detail.startswith("[") and detail.endswith("]"):
            detail = detail[1:-1].strip()
        return detail

    async def _handle_admin_config(self, message, command: str) -> None:
        if not self._database.save_inbound_platform_event(message):
            return
        self._merger.interrupt_current_reply(message.session_id)
        try:
            response = self._apply_admin_config(command)
        except (OSError, ValueError) as exc:
            response = f"配置未变更：{exc}"
        await self._sender.send(
            OutboundMessage(
                platform="qq",
                account_id=message.account_id,
                conversation_id=message.conversation_id,
                text=response,
            )
        )
        self._sink(f"[管理员配置] {response}")

    def _apply_admin_config(self, command: str) -> str:
        parts = command.split()
        if not parts or parts[0].lower() in {"帮助", "help"}:
            return self._admin_config_help()
        action = parts[0].lower()
        if action in {"查看", "status"} and len(parts) == 1:
            limit = (
                "不限"
                if self._proactive_daily_limit == 0
                else str(self._proactive_daily_limit)
            )
            return (
                "当前配置："
                f"自动回复={'开' if self._auto_reply_enabled else '关'}；"
                f"主动聊天={'开' if self._proactive_enabled else '关'}；"
                f"追加回复={'开' if self._followup_enabled else '关'}；"
                f"追加概率={self._followup_probability * 100:g}%；"
                f"主动间隔={self._proactive_min / 60:g}-"
                f"{self._proactive_max / 60:g}分钟；"
                f"主动上限={limit}；"
                f"静默时段={self._quiet_start_text}-"
                f"{self._quiet_end_text}"
            )
        if action in {"自动回复", "主动聊天", "追加回复"} and len(parts) == 2:
            enabled = self._parse_switch(parts[1])
            if action == "自动回复":
                key = "QQ_AUTO_REPLY_ENABLED"
                setter = self.set_auto_reply_enabled
            elif action == "主动聊天":
                key = "QQ_PROACTIVE_ENABLED"
                setter = self.set_proactive_enabled
            else:
                key = "QQ_FOLLOWUP_ENABLED"
                setter = self.set_followup_enabled
            self._write_admin_config({key: "true" if enabled else "false"})
            setter(enabled)
            return f"{action}已{'开启' if enabled else '关闭'}"
        if action == "追加概率" and len(parts) == 2:
            probability = float(parts[1])
            if not 0 <= probability <= 100:
                raise ValueError("追加概率需为 0～100")
            self._write_admin_config(
                {"QQ_FOLLOWUP_PROBABILITY": f"{probability / 100:g}"}
            )
            self._followup_probability = probability / 100
            return f"追加回复概率已设为 {probability:g}%"
        if action == "主动间隔" and len(parts) == 3:
            minimum = float(parts[1])
            maximum = float(parts[2])
            if (
                not math.isfinite(minimum)
                or not math.isfinite(maximum)
                or minimum <= 0
                or maximum < minimum
            ):
                raise ValueError("主动间隔需为正数，且最小值不大于最大值")
            self._write_admin_config(
                {
                    "QQ_PROACTIVE_MIN_SECONDS": f"{minimum * 60:g}",
                    "QQ_PROACTIVE_MAX_SECONDS": f"{maximum * 60:g}",
                }
            )
            self._proactive_min = minimum * 60
            self._proactive_max = maximum * 60
            self._reschedule_proactive_targets()
            return f"主动聊天间隔已设为 {minimum:g}～{maximum:g} 分钟"
        if action == "主动上限" and len(parts) == 2:
            limit = int(parts[1])
            if limit < 0:
                raise ValueError("主动上限不能小于 0；0 表示不限")
            self._write_admin_config({"QQ_PROACTIVE_DAILY_LIMIT": str(limit)})
            self._proactive_daily_limit = limit
            self._proactive_wakeup.set()
            return (
                "主动聊天每日上限已设为不限"
                if limit == 0
                else f"主动聊天每日上限已设为 {limit} 次"
            )
        if action == "静默时段" and len(parts) == 3:
            start, end = parts[1], parts[2]
            start_minutes = self._minutes(start)
            end_minutes = self._minutes(end)
            self._write_admin_config(
                {
                    "QQ_PROACTIVE_QUIET_START": start,
                    "QQ_PROACTIVE_QUIET_END": end,
                }
            )
            self._quiet_start_text = start
            self._quiet_end_text = end
            self._quiet_start = start_minutes
            self._quiet_end = end_minutes
            self._proactive_wakeup.set()
            return f"主动聊天静默时段已设为 {start}～{end}"
        raise ValueError("未知或格式错误的配置命令；发送 #配置 帮助 查看用法")

    def _write_admin_config(self, updates: dict[str, str]) -> None:
        if self._admin_config_writer is None:
            raise ValueError("当前运行方式未启用远程配置写入")
        self._admin_config_writer(updates)

    @staticmethod
    def _parse_switch(value: str) -> bool:
        normalized = value.strip().lower()
        if normalized in {"开", "开启", "on", "true", "1"}:
            return True
        if normalized in {"关", "关闭", "off", "false", "0"}:
            return False
        raise ValueError("开关值只能是 开 或 关")

    @staticmethod
    def _admin_config_help() -> str:
        return (
            "管理员配置命令：#配置 查看；#配置 自动回复 开|关；"
            "#配置 主动聊天 开|关；#配置 追加回复 开|关；"
            "#配置 追加概率 0-100；#配置 主动间隔 最小分钟 最大分钟；"
            "#配置 主动上限 次数（0=不限）；#配置 静默时段 HH:MM HH:MM"
        )

    def _record_admin_memory(
        self,
        message,
        instruction: str,
    ) -> None:
        recorded_version = self._merger.record_platform(message)
        if recorded_version is None:
            return
        if not 2 <= len(instruction) <= 300:
            self._sink("[管理员记忆] 内容需为 2～300 个字符，未写入")
            return
        assert self._memory_service is not None
        source_message_id = self._database.latest_message_id(
            message.session_id
        )
        memory_key = (
            "admin_instruction."
            + hashlib.sha256(instruction.encode("utf-8")).hexdigest()[:16]
        )
        candidate, memory = self._memory_service.add_manual_memory(
            conversation_id=message.session_id,
            memory_type="fact",
            memory_key=memory_key,
            content=instruction,
            source_message_ids=(source_message_id,),
        )
        if memory is None:
            self._sink(
                "[管理员记忆] 未写入，"
                f"candidate_id={candidate.id} status={candidate.status}"
            )
            return
        self._sink(
            "[管理员记忆] 已写入长期记忆，"
            f"memory_id={memory.id}"
        )

    def _register_private_target(
        self,
        *,
        account_id: str,
        user_id: str,
        display_name: str,
        nickname: str | None,
        reset: bool,
    ) -> None:
        if (
            user_id in self._proactive_excluded_ids
            or (self._proactive_ids and user_id not in self._proactive_ids)
        ):
            return
        target = _ProactiveTarget(
            account_id=account_id,
            conversation_id=f"qq:private:{user_id}",
            target_id=user_id,
            target_type="private",
            display_name=display_name,
            nickname=nickname,
        )
        self._known_private_contacts[user_id] = target
        self._register_proactive_target(target, reset=reset)

    def _register_proactive_target(
        self,
        target: _ProactiveTarget,
        *,
        reset: bool = False,
    ) -> None:
        self._proactive_targets[target.conversation_id] = target
        if reset or target.conversation_id not in self._next_proactive_at:
            self._schedule_proactive(target.conversation_id)

    def _schedule_proactive(self, conversation_id: str) -> None:
        self._next_proactive_at[conversation_id] = (
            self._monotonic()
            + self._random.uniform(self._proactive_min, self._proactive_max)
        )
        self._proactive_wakeup.set()

    def _reschedule_proactive_targets(self) -> None:
        for conversation_id in tuple(self._proactive_targets):
            self._schedule_proactive(conversation_id)

    def _has_proactive_capacity(self, conversation_id: str) -> bool:
        return (
            self._proactive_daily_limit == 0
            or self._proactive_counts.get(conversation_id, 0)
            < self._proactive_daily_limit
        )

    def _load_saved_proactive_targets(self) -> None:
        if self._account_id is None:
            return
        configured_ids = self._proactive_ids
        if not configured_ids:
            since = datetime.now(UTC) - timedelta(
                days=self._proactive_recent_days
            )
            configured_ids = frozenset(
                self._database.list_recent_private_contacts(
                    platform="qq",
                    account_id=self._account_id,
                    since_timestamp=since.isoformat(),
                )
            )
        for user_id in configured_ids:
            self._register_private_target(
                account_id=self._account_id,
                user_id=user_id,
                display_name=(
                    self._primary_name
                    if user_id == self._primary_id
                    else user_id
                ),
                nickname=None,
                reset=False,
            )
        for group_id in self._reply_groups:
            self._register_proactive_target(
                _ProactiveTarget(
                    account_id=self._account_id,
                    conversation_id=f"qq:group:{group_id}",
                    target_id=group_id,
                    target_type="group",
                    display_name=f"群 {group_id}",
                    nickname=None,
                )
            )

    def _is_quiet_time(self) -> bool:
        if self._quiet_start == self._quiet_end:
            return False
        now = self._wall_clock()
        minute = now.hour * 60 + now.minute
        if self._quiet_start < self._quiet_end:
            return self._quiet_start <= minute < self._quiet_end
        return minute >= self._quiet_start or minute < self._quiet_end

    @staticmethod
    def _minutes(value: str) -> int:
        hour, minute = (int(part) for part in value.split(":", 1))
        if hour not in range(24) or minute not in range(60):
            raise ValueError("quiet time must be HH:MM")
        return hour * 60 + minute

    def _reset_proactive_counts_if_needed(self) -> None:
        today = self._wall_clock().date()
        if today != self._proactive_day:
            self._proactive_day = today
            self._proactive_counts.clear()

    async def _resolve_images(self, message):
        resolved_parts = []
        for part in message.parts:
            if part.part_type != "image":
                resolved_parts.append(part)
                continue
            try:
                resolved_parts.append(await self._images.resolve(part))
            except ImageResolutionError as exc:
                logger.info(
                    "qq_image_unavailable message=%s part=%s reason=%s",
                    message.message_id,
                    part.part_index,
                    exc,
                )
                self._sink(f"[QQ 图片] {exc}，已降级处理")
                resolved_parts.append(self._images.unavailable(part))
        return replace(message, parts=tuple(resolved_parts))

    async def _prepare_images(self, message):
        if not message.has_images:
            return message
        try:
            return await asyncio.wait_for(self._resolve_images(message), timeout=self._image_wait_seconds)
        except Exception:
            self._sink('[QQ 图片] 图片处理超时或失败，已降级；文字继续处理')
            return replace(message, parts=tuple(
                replace(part, available=False, resolved_url=None, url=None) if part.part_type == 'image' else part
                for part in message.parts))

    async def _send_generated(
        self,
        outbound: OutboundMessage,
        *,
        expected_version: int | None = None,
        purpose: str = "reply",
        epoch: tuple[int, int] | None = None,
    ) -> int:
        """Send one model reply as separate conversational message bubbles."""
        parts = split_outbound_text(outbound.text)
        batch_id = uuid4().hex
        records = [self._delivery.prepare(replace(outbound, text=text, metadata=outbound.metadata if index == 0 else {}),
                    batch_id=batch_id, index=index) for index, text in enumerate(parts)]
        sent = 0
        for record_id in records:
            if not self._can_send_generated(outbound, purpose, epoch):
                break
            if (
                expected_version is not None
                and not self._merger.is_version_current(
                    outbound.session_id,
                    expected_version,
                )
            ):
                break
            record = await self._delivery.send(record_id, self._sender,
                guard=lambda message: self._can_send_generated(message, purpose, epoch) and (
                    expected_version is None or self._merger.is_version_current(message.session_id, expected_version)))
            if record['status'] not in {'sent', 'simulated'}:
                break
            sent += 1
        return sent

    def _can_send_generated(
        self, outbound: OutboundMessage, purpose: str, epoch: tuple[int, int] | None,
    ) -> bool:
        if outbound.account_id != self._account_id:
            return False
        if self._merger.is_human_takeover(outbound.session_id):
            return False
        if purpose == "proactive":
            return self._proactive_enabled and epoch == (
                self._account_epoch, self._proactive_epoch
            )
        if not self._auto_reply_enabled:
            return False
        if purpose == "followup":
            return self._followup_enabled and epoch == (
                self._account_epoch, self._followup_epoch
            )
        return True

    async def _send_result(
        self,
        parsed: ParsedQQEvent,
        result: DispatchResult,
        display_name: str,
    ) -> None:
        if not self._auto_reply_enabled:
            return
        message = parsed.message
        image_metadata = self._profile_image_metadata(result.merged_content)
        outbound = OutboundMessage(
            platform="qq",
            account_id=message.account_id,
            conversation_id=message.conversation_id,
            text=result.reply or "",
            metadata=image_metadata,
        )
        sent = await self._send_generated(
            outbound,
            expected_version=result.conversation_version,
        )
        if sent == 0:
            return
        mode = "DRY RUN" if self.dry_run else "SENT"
        self._sink(
            f"[QQ {mode}]\n"
            f"联系人：{display_name}\n"
            f"nickname：{parsed.nickname or '-'}\n"
            f"user_id：{message.sender_id}\n"
            f"收到：{message.storage_text}\n"
            f"候选回复：{result.reply}"
            + ("\n资料图：已附带" if image_metadata else "")
        )
        self._schedule_proactive(message.conversation_id)
        await self._maybe_send_followup(
            outbound,
            expected_version=result.conversation_version,
            label=f"联系人：{display_name}",
        )

    async def _maybe_send_followup(
        self,
        outbound: OutboundMessage,
        *,
        expected_version: int,
        label: str,
    ) -> None:
        if (
            not self._auto_reply_enabled
            or not self._followup_enabled
            or self._random.random() >= self._followup_probability
        ):
            return
        delay = self._random.uniform(
            self._followup_min_delay,
            self._followup_max_delay,
        )
        epoch = (self._account_epoch, self._followup_epoch)
        if delay:
            await asyncio.sleep(delay)
        if not self._can_send_generated(outbound, "followup", epoch):
            return
        reply = await self._merger.generate_followup(
            outbound.session_id,
            expected_version,
            self._followup_prompt,
            defer_commit=True,
        )
        if reply is None:
            return
        if _replies_too_similar(outbound.text, reply):
            return
        if not self._auto_reply_enabled or not self._followup_enabled:
            return
        sent = await self._send_generated(
            replace(outbound, text=reply, metadata={}),
            expected_version=expected_version,
            purpose="followup",
            epoch=epoch,
        )
        if sent == 0:
            return
        self._schedule_proactive(outbound.conversation_id)
        mode = "DRY RUN" if self.dry_run else "SENT"
        self._sink(
            f"[QQ FOLLOW-UP {mode}]\n"
            f"{label}\n"
            f"追加回复：{reply}"
        )

    async def _handle_group(
        self,
        parsed: ParsedQQEvent,
    ) -> DispatchResult | None:
        if parsed.group_id in self._training_groups:
            self._handle_training_group(parsed)
            return None
        if parsed.group_id not in self._reply_groups:
            return None
        message = parsed.message
        group_target = _ProactiveTarget(
            account_id=message.account_id,
            conversation_id=message.conversation_id,
            target_id=parsed.group_id or "",
            target_type="group",
            display_name=f"群 {parsed.group_id}",
            nickname=None,
        )
        self._register_proactive_target(group_target, reset=True)
        if message.is_self:
            source_type = self._source_type(
                message.deduplication_key,
                message.is_self,
            )
            if source_type == "ai_generated":
                self._database.save_inbound_platform_event(message)
                return None
            if self._merger.record_human_self_platform(message):
                self._sink(
                    "[QQ GROUP HUMAN TAKEOVER]\n"
                    f"group={parsed.group_id}\n"
                    "检测到当前账号人工发送，已暂停该群会话自动回复"
                )
            return None
        if not self._auto_reply_enabled:
            self._merger.record_platform(message)
            return None
        if not parsed.mentions_self:
            pending = self._group_requests.get(message.session_id)
            correction = pending and pending[0] == message.sender_id and any(
                message.storage_text.lstrip().startswith(word)
                for word in ('补充', '等等', '不是', '改成', '不对', '更正', '我是说', '刚才说错', '另外'))
            if not correction:
                self._merger.record_platform(message, invalidate=False, background_to_context=not pending)
                return None
        self._sink(
            "[QQ GROUP MENTION]\n"
            f"group={parsed.group_id}\n"
            f"sender={message.sender_id}\n"
            f"text={message.storage_text}"
        )
        request = (message.sender_id, message.message_id)
        self._group_requests[message.session_id] = request
        try:
            result = await self._merger.submit_platform(message, defer_commit=True, prepare=self._prepare_images)
            return await self._deliver_group_result(parsed, result)
        finally:
            if self._group_requests.get(message.session_id) == request:
                self._group_requests.pop(message.session_id, None)

    async def _deliver_group_result(self, parsed: ParsedQQEvent, result: DispatchResult | None) -> DispatchResult | None:
        message = parsed.message
        if result is not None and result.reply is not None:
            if not self._auto_reply_enabled:
                return result
            image_metadata = self._profile_image_metadata(
                result.merged_content
            )
            outbound = OutboundMessage(
                platform="qq",
                account_id=message.account_id,
                conversation_id=message.conversation_id,
                text=result.reply,
                metadata=image_metadata,
            )
            sent = await self._send_generated(
                outbound,
                expected_version=result.conversation_version,
            )
            if sent == 0:
                return result
            mode = "DRY RUN" if self.dry_run else "SENT"
            self._sink(
                f"[QQ GROUP {mode}]\n"
                f"group={parsed.group_id}\n"
                f"候选回复：{result.reply}"
                + ("\n资料图：已附带" if image_metadata else "")
            )
            self._schedule_proactive(message.conversation_id)
            await self._maybe_send_followup(
                outbound,
                expected_version=result.conversation_version,
                label=f"group={parsed.group_id}",
            )
        return result

    def _profile_image_metadata(self, text: str) -> dict[str, object]:
        if not _requests_account_profile_image(text):
            return {}
        if self._memory_service is None:
            return {}
        profile = self._memory_service.repository.get_account_profile(
            f"qq:{self._account_id}" if self._account_id else None
        )
        if profile is None or not profile.public_image_path:
            return {}
        configured = Path(profile.public_image_path)
        candidate = (
            configured
            if configured.is_absolute()
            else self._profile_asset_root / configured
        ).resolve()
        try:
            candidate.relative_to(self._profile_asset_root)
        except ValueError:
            logger.warning("qq_profile_image_outside_asset_root")
            return {}
        if (
            candidate.suffix.lower() not in _PROFILE_IMAGE_EXTENSIONS
            or not candidate.is_file()
        ):
            logger.warning("qq_profile_image_unavailable")
            return {}
        return {"local_image_paths": (str(candidate),)}

    def _handle_training_group(self, parsed: ParsedQQEvent) -> None:
        message = parsed.message
        source_type = self._source_type(message.deduplication_key, message.is_self)
        sample = self._styles.record_group_event(
            QQGroupEvent(
                message=message,
                group_id=parsed.group_id or "",
                nickname=parsed.nickname,
                reply_to_message_id=parsed.reply_to_message_id,
                source_type=source_type,
            )
        )
        if sample is not None:
            self._sink(
                "[STYLE CANDIDATE]\n"
                f"context_messages={len(sample.context_messages)}\n"
                f"human_reply={sample.human_reply}\n"
                f"sample_id={sample.sample_id}"
            )

    def _source_type(
        self,
        key: tuple[str, str, str],
        is_self: bool,
    ) -> str:
        if key in self._ai_generated_ids:
            return "ai_generated"
        return "human_self" if is_self else "other_user"


class _ForbiddenSender:
    async def send(self, message: OutboundMessage) -> None:
        raise AssertionError("QQ transport send must never be called in dry-run mode")


def _requests_account_profile_image(text: str) -> bool:
    compact = "".join(text.lower().split())
    return any(phrase in compact for phrase in _PROFILE_IMAGE_REQUEST_PHRASES)
