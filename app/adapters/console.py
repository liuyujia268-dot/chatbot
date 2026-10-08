"""Interactive local console adapter."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from itertools import count
from uuid import uuid4

from app.adapters.base import PlatformAdapter
from app.adapters.admin_commands import AdminCommandHandler
from app.adapters.outbound import (
    ConsoleMessageSender,
    DryRunMessageSender,
    MessageSender,
    split_outbound_text,
)
from app.core.dispatcher import ContinuousMessageMerger, DispatchResult, MessageDispatcher
from app.core.platform import OutboundMessage, PlatformMessage
from app.core.identity import platform_conversation_id
from app.core.identity import conversation_key
from app.adapters.delivery import DeliveryService, STATUS_LABELS
from collections.abc import Callable
from app.models.base import ModelError


logger = logging.getLogger(__name__)


MESSAGE_PATTERN = re.compile(r"^\s*\[([^\[\]]+)\]\s*(.+?)\s*$")
TAKEOVER_PATTERN = re.compile(r"^/takeover\s+(\S+)\s+(on|off)$", re.IGNORECASE)


def parse_contact_message(line: str) -> tuple[str, str]:
    match = MESSAGE_PATTERN.match(line)
    if not match:
        raise ValueError("消息格式必须是：[联系人] 消息内容")
    contact, content = match.groups()
    return contact.strip(), content.strip()


def parse_takeover_command(line: str) -> tuple[str, bool] | None:
    match = TAKEOVER_PATTERN.match(line.strip())
    if not match:
        return None
    contact, state = match.groups()
    return contact, state.lower() == "on"


class ConsoleAdapter(PlatformAdapter):
    def __init__(
        self,
        dispatcher: MessageDispatcher,
        merger: ContinuousMessageMerger,
        admin_commands: AdminCommandHandler | None = None,
        sender: MessageSender | None = None,
        dry_run: bool = False,
        qq_mode: bool = False,
        qq_sender: MessageSender | None = None,
        qq_dry_run: bool = True,
        qq_primary_contact_id: str | None = None,
        qq_primary_contact_name: str = "宇飞",
        qq_account_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._merger = merger
        self._admin_commands = admin_commands
        concrete_sender = sender or ConsoleMessageSender()
        self._sender = (
            DryRunMessageSender(concrete_sender) if dry_run else concrete_sender
        )
        self._run_id = uuid4().hex
        self._message_ids = count(1)
        self._message_tasks: set[asyncio.Task[None]] = set()
        self._qq_mode = qq_mode
        self._qq_sender = qq_sender
        self._qq_dry_run = qq_dry_run
        self._qq_primary_id = qq_primary_contact_id
        self._qq_primary_name = qq_primary_contact_name
        self.qq_account_provider = qq_account_provider
        self._delivery = (DeliveryService(dispatcher.database, on_history=merger.accept_delivered_history)
                          if dispatcher is not None and merger is not None else None)
        if self._admin_commands is not None:
            self._admin_commands.account_provider = (lambda: f'qq:{self.qq_account_provider()}' if self.qq_account_provider and self.qq_account_provider() else None) if qq_mode else lambda: 'console:local'
        if qq_mode and qq_sender is None:
            raise ValueError("QQ console mode requires a QQ sender")

    async def run(self) -> None:
        if self._qq_mode:
            print("QQ 接入与本地管理控制台已启动。")
            print("手动私聊：[宇飞] 消息内容；或 [QQ号] 消息内容")
            print("手动群聊：[群:群号] 消息内容")
        else:
            print("命令行聊天模拟器已启动。")
            print("消息格式：[张三] 消息内容")
        if self._qq_mode:
            print(
                f"人工接管：/takeover {self._qq_primary_name} on；恢复："
                f"/takeover {self._qq_primary_name} off"
                "（其他联系人使用 QQ号）"
            )
        else:
            print(
                "人工接管：/takeover 张三 on；"
                "恢复自动回复：/takeover 张三 off"
            )
        print("记忆管理：/memory help；联系人画像：/profile show 张三")
        print("风格样本：/style help")
        print("退出：/quit")
        while True:
            try:
                line = await asyncio.to_thread(input, "> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if line.strip().lower() in {"/quit", "/exit"}:
                break
            if not line.strip():
                continue

            if self._admin_commands is not None:
                if await self._admin_commands.handle(line):
                    continue

            takeover = parse_takeover_command(line)
            if takeover:
                contact, enabled = takeover
                try:
                    conversation_id = (
                        self._qq_conversation_id(contact)
                        if self._qq_mode
                        else contact
                    )
                    if self._qq_mode:
                        account_id = self.qq_account_provider() if self.qq_account_provider else None
                        if not account_id:
                            raise ValueError('QQ账号尚未确认')
                        conversation_id = conversation_key('qq', account_id, conversation_id)
                    else:
                        conversation_id = conversation_key('console', 'local', conversation_id)
                except ValueError as exc:
                    print(f"[系统] {exc}")
                    continue
                self._dispatcher.set_human_takeover(
                    conversation_id,
                    enabled,
                )
                state = "开启" if enabled else "关闭"
                print(f"[系统] {contact} 的人工接管已{state}")
                continue
            if line.strip().lower().startswith("/takeover"):
                print("[系统] 用法：/takeover 联系人 on|off")
                continue

            try:
                contact, content = parse_contact_message(line)
            except ValueError as exc:
                print(f"[系统] {exc}")
                continue

            if self._qq_mode:
                await self.send_qq_message(contact, content)
                continue

            task = asyncio.create_task(self._submit_and_print(contact, content))
            self._message_tasks.add(task)
            task.add_done_callback(self._message_tasks.discard)
            await asyncio.sleep(0)

        await self._merger.flush_all()
        if self._message_tasks:
            await asyncio.gather(*tuple(self._message_tasks))
        await self._dispatcher.finish_background_tasks()

    async def send_qq_message(
        self,
        contact: str,
        content: str,
    ) -> OutboundMessage | None:
        """Send operator-authored text directly to QQ through NapCat."""
        try:
            conversation_id = self._qq_conversation_id(contact)
        except ValueError as exc:
            print(f"[系统] {exc}")
            return None
        account_id = self.qq_account_provider() if self.qq_account_provider else None
        if not account_id:
            print('[QQ 手动发送失败] QQ账号尚未确认；正文未提交')
            return None
        message = OutboundMessage(
            platform="qq",
            account_id=account_id,
            conversation_id=conversation_id,
            text=content,
            metadata={"source_type": "human_self"},
        )
        try:
            assert self._qq_sender is not None
            if self._delivery is not None:
                self._merger.interrupt_current_reply(message.session_id)
                record_id = self._delivery.prepare(message)
                record = await self._delivery.send(record_id, self._qq_sender, guard=lambda outbound: (
                    self.qq_account_provider is not None and self.qq_account_provider() == outbound.account_id))
                print(f"[QQ MANUAL] {STATUS_LABELS[record['status']]} record={record_id}")
                if record['status'] not in {'sent', 'simulated'}:
                    print('[QQ MANUAL] 正文已保留在发送记录中，可复制后核对重试')
                    return None
            else:
                # Lightweight adapters in unit tests may not own a dispatcher.
                await self._qq_sender.send(message)
        except Exception:
            logger.exception(
                "qq_manual_send_failed conversation=%s",
                conversation_id,
            )
            print("[QQ 手动发送失败] 请确认 NapCat WebSocket 已连接并查看日志")
            return None
        mode = "DRY RUN" if self._qq_dry_run else "SENT"
        print(f"[QQ MANUAL {mode}] {contact}: {content}")
        return message

    def _qq_conversation_id(self, contact: str) -> str:
        value = contact.strip()
        if value == self._qq_primary_name:
            if not self._qq_primary_id:
                raise ValueError("尚未配置 QQ_PRIMARY_CONTACT_ID")
            return f"qq:private:{self._qq_primary_id}"
        if value.startswith("qq:private:"):
            target = value.removeprefix("qq:private:")
            kind = "private"
        elif value.startswith("qq:group:"):
            target = value.removeprefix("qq:group:")
            kind = "group"
        elif value.startswith("群:"):
            target = value.removeprefix("群:")
            kind = "group"
        else:
            target = value
            kind = "private"
        if not target.isdecimal():
            raise ValueError(
                "QQ 模式请使用 [宇飞]、[QQ号] 或 [群:群号] 消息内容"
            )
        return f"qq:{kind}:{target}"

    async def _submit_and_print(self, contact: str, content: str) -> None:
        try:
            message = PlatformMessage(
                platform="console",
                account_id="local",
                conversation_id=contact,
                sender_id=contact,
                message_id=f"{self._run_id}-{next(self._message_ids)}",
                timestamp=datetime.now(UTC),
                message_type="text",
                text=content,
                is_self=False,
            )
            result = await self._merger.submit_platform(message)
        except ModelError as exc:
            print(f"[模型错误] {exc}")
            return
        except Exception as exc:
            logger.exception("unexpected_message_error contact=%s", contact)
            print(f"[系统] 处理 {contact} 的消息失败，请查看日志。")
            return
        if result is not None:
            await self._send_result(result)

    async def _send_result(self, result: DispatchResult) -> None:
        if result.reply is None:
            if result.skip_reason == "human_takeover":
                print(
                    f"[系统] {result.session_id} 的消息在人工接管期间收到，"
                    "已记录消息但不自动回复"
                )
            return
        outbound = OutboundMessage(
            platform="console",
            account_id="local",
            conversation_id=platform_conversation_id(result.session_id),
            text=result.reply,
            metadata={"message_count": result.message_count},
        )
        for index, text in enumerate(split_outbound_text(result.reply)):
            metadata = outbound.metadata if index == 0 else {}
            await self._sender.send(
                OutboundMessage(
                    platform=outbound.platform,
                    account_id=outbound.account_id,
                    conversation_id=outbound.conversation_id,
                    text=text,
                    metadata=metadata,
                )
            )
