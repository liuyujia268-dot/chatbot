"""NapCat/OneBot QQ adapter with explicit private and group reply rules."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from app.adapters.base import PlatformAdapter
from app.adapters.image_resolver import ImageResolutionError, ImageResolver
from app.adapters.onebot import ParsedQQEvent, parse_onebot_event
from app.adapters.outbound import DryRunMessageSender, MessageSender
from app.core.dispatcher import ContinuousMessageMerger, DispatchResult
from app.core.platform import OutboundMessage
from app.memory.service import MemoryService
from app.storage.database import Database
from app.style.repository import QQGroupEvent, StyleRepository
from app.models.base import ModelError


logger = logging.getLogger(__name__)


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
        memory_service: MemoryService | None = None,
        reply_group_ids: tuple[str, ...] = (),
        dry_run: bool = True,
        auto_reply_enabled: bool = True,
        proactive_enabled: bool = False,
        proactive_contact_ids: tuple[str, ...] = (),
        proactive_exclude_contact_ids: tuple[str, ...] = (),
        proactive_min_seconds: float = 2400.0,
        proactive_max_seconds: float = 4800.0,
        proactive_daily_limit: int = 3,
        proactive_recent_days: int = 7,
        proactive_quiet_start: str = "00:00",
        proactive_quiet_end: str = "08:00",
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
    ) -> None:
        self._merger = merger
        self._database = database
        self._styles = style_repository
        self._images = image_resolver
        self._primary_id = primary_contact_id
        self._primary_name = primary_contact_name
        self._admin_ids = frozenset(admin_contact_ids)
        self._admin_memory_prefix = admin_memory_prefix.strip() or "#指令"
        self._memory_service = memory_service
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
        self._awaiting_proactive_reply: set[str] = set()
        self._proactive_wakeup = asyncio.Event()
        self._account_id: str | None = None
        self._proactive_counts: dict[str, int] = {}
        self._proactive_day = self._wall_clock().date()
        if proactive_min_seconds > proactive_max_seconds:
            raise ValueError("proactive minimum cannot exceed maximum")
        if proactive_daily_limit < 1:
            raise ValueError("proactive daily limit must be at least 1")
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

    def set_proactive_enabled(self, enabled: bool) -> None:
        self._proactive_enabled = bool(enabled)
        self._proactive_wakeup.set()

    def set_followup_enabled(self, enabled: bool) -> None:
        self._followup_enabled = bool(enabled)

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
            self._sink(f"[QQ 模型错误] {exc}")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("qq_event_processing_failed")
            self._sink("[QQ] 事件处理失败，已跳过")

    async def handle_event(self, event: dict[str, Any]) -> DispatchResult | None:
        account_id = str(event.get("self_id", "")).strip()
        if account_id:
            changed = account_id != self._account_id
            self._account_id = account_id
            if changed:
                self._load_saved_proactive_targets()
        parsed = parse_onebot_event(event)
        if parsed is None:
            return None
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
                self._awaiting_proactive_reply.discard(
                    message.conversation_id
                )
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
        self._awaiting_proactive_reply.discard(message.conversation_id)
        if self._primary_id is None:
            self._sink(
                "[QQ PRIMARY CANDIDATE]\n"
                f"nickname={parsed.nickname or '-'}\n"
                f"user_id={message.sender_id}\n"
                f"conversation_id={message.conversation_id}\n"
                "请人工确认后写入 QQ_PRIMARY_CONTACT_ID"
            )
        admin_instruction = self._admin_instruction(parsed)
        if admin_instruction is not None:
            self._record_admin_memory(message, admin_instruction)
            return None
        if not self._auto_reply_enabled:
            self._merger.record_platform(message)
            return None

        resolved = await self._resolve_images(message)
        has_text = any(part.part_type == "text" for part in resolved.parts)
        has_image = any(
            part.part_type == "image" and part.model_image_url
            for part in resolved.parts
        )
        if not has_text and not has_image:
            self._merger.record_platform(resolved)
            return None
        result = await self._merger.submit_platform(resolved)
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
                    and conversation_id
                    not in self._awaiting_proactive_reply
                    and not self._merger.is_human_takeover(
                        conversation_id
                    )
                    and self._proactive_counts.get(conversation_id, 0)
                    < self._proactive_daily_limit
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
                self._sink(f"[QQ 主动消息模型错误] {exc}")
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
        if not force and self._is_quiet_time():
            for conversation_id, due in tuple(
                self._next_proactive_at.items()
            ):
                if due <= now:
                    self._next_proactive_at[conversation_id] = now + 60.0
            return None
        candidates = [
            contact
            for conversation_id, contact in self._proactive_targets.items()
            if self._proactive_counts.get(conversation_id, 0)
            < self._proactive_daily_limit
            and conversation_id not in self._awaiting_proactive_reply
            and not self._merger.is_human_takeover(conversation_id)
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
        reply = await self._merger.generate_proactive(
            contact.conversation_id,
            self._proactive_prompt,
        )
        if reply is None:
            self._schedule_proactive(contact.conversation_id)
            return None
        await self._sender.send(
            OutboundMessage(
                platform="qq",
                account_id=contact.account_id,
                conversation_id=contact.conversation_id,
                text=reply,
            )
        )
        self._proactive_counts[contact.conversation_id] = (
            self._proactive_counts.get(contact.conversation_id, 0) + 1
        )
        self._awaiting_proactive_reply.add(contact.conversation_id)
        self._schedule_proactive(contact.conversation_id)
        mode = "DRY RUN" if self._dry_run else "SENT"
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
        message = parsed.message
        if (
            message.sender_id not in self._admin_ids
            or any(part.part_type != "text" for part in message.parts)
        ):
            return None
        text = "".join(part.text or "" for part in message.parts).strip()
        if not text.startswith(self._admin_memory_prefix):
            return None
        remainder = text[len(self._admin_memory_prefix):]
        if remainder and not (remainder[0].isspace() or remainder[0] == "["):
            return None
        detail = remainder.strip()
        if detail.startswith("[") and detail.endswith("]"):
            detail = detail[1:-1].strip()
        return detail

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
            message.conversation_id
        )
        memory_key = (
            "admin_instruction."
            + hashlib.sha256(instruction.encode("utf-8")).hexdigest()[:16]
        )
        candidate, memory = self._memory_service.add_manual_memory(
            conversation_id=message.conversation_id,
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

    async def _send_result(
        self,
        parsed: ParsedQQEvent,
        result: DispatchResult,
        display_name: str,
    ) -> None:
        if not self._auto_reply_enabled:
            return
        message = parsed.message
        outbound = OutboundMessage(
            platform="qq",
            account_id=message.account_id,
            conversation_id=message.conversation_id,
            text=result.reply or "",
        )
        await self._sender.send(outbound)
        mode = "DRY RUN" if self._dry_run else "SENT"
        self._sink(
            f"[QQ {mode}]\n"
            f"联系人：{display_name}\n"
            f"nickname：{parsed.nickname or '-'}\n"
            f"user_id：{message.sender_id}\n"
            f"收到：{message.storage_text}\n"
            f"候选回复：{result.reply}"
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
        if delay:
            await asyncio.sleep(delay)
        reply = await self._merger.generate_followup(
            outbound.conversation_id,
            expected_version,
            self._followup_prompt,
        )
        if reply is None:
            return
        if not self._auto_reply_enabled or not self._followup_enabled:
            return
        await self._sender.send(replace(outbound, text=reply))
        self._schedule_proactive(outbound.conversation_id)
        mode = "DRY RUN" if self._dry_run else "SENT"
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
        self._awaiting_proactive_reply.discard(message.conversation_id)
        if not self._auto_reply_enabled:
            self._merger.record_platform(message)
            return None
        if not parsed.mentions_self:
            self._merger.record_platform(message)
            return None
        self._sink(
            "[QQ GROUP MENTION]\n"
            f"group={parsed.group_id}\n"
            f"sender={message.sender_id}\n"
            f"text={message.storage_text}"
        )
        resolved = await self._resolve_images(message)
        has_text = any(part.part_type == "text" for part in resolved.parts)
        has_image = any(
            part.part_type == "image" and part.model_image_url
            for part in resolved.parts
        )
        if not has_text and not has_image:
            self._merger.record_platform(resolved)
            return
        result = await self._merger.submit_platform(resolved)
        if result is not None and result.reply is not None:
            if not self._auto_reply_enabled:
                return result
            outbound = OutboundMessage(
                platform="qq",
                account_id=message.account_id,
                conversation_id=message.conversation_id,
                text=result.reply,
            )
            await self._sender.send(outbound)
            mode = "DRY RUN" if self._dry_run else "SENT"
            self._sink(
                f"[QQ GROUP {mode}]\n"
                f"group={parsed.group_id}\n"
                f"候选回复：{result.reply}"
            )
            self._schedule_proactive(message.conversation_id)
            await self._maybe_send_followup(
                outbound,
                expected_version=result.conversation_version,
                label=f"group={parsed.group_id}",
            )
        return result

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
