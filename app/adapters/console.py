"""Interactive local console adapter."""

from __future__ import annotations

import asyncio
import re

from app.adapters.base import PlatformAdapter
from app.core.dispatcher import ContinuousMessageMerger, DispatchResult, MessageDispatcher


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
    ) -> None:
        self._dispatcher = dispatcher
        self._merger = merger
        self._message_tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        print("命令行聊天模拟器已启动。")
        print("消息格式：[张三] 消息内容")
        print("人工接管：/takeover 张三 on；恢复自动回复：/takeover 张三 off")
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

            takeover = parse_takeover_command(line)
            if takeover:
                contact, enabled = takeover
                self._dispatcher.set_human_takeover(contact, enabled)
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

            task = asyncio.create_task(self._submit_and_print(contact, content))
            self._message_tasks.add(task)
            task.add_done_callback(self._message_tasks.discard)
            await asyncio.sleep(0)

        await self._merger.flush_all()
        if self._message_tasks:
            await asyncio.gather(*tuple(self._message_tasks))

    async def _submit_and_print(self, contact: str, content: str) -> None:
        try:
            result = await self._merger.submit(contact, content)
        except Exception as exc:
            print(f"[系统] 处理 {contact} 的消息失败：{exc}")
            return
        if result is not None:
            self._print_result(result)

    @staticmethod
    def _print_result(result: DispatchResult) -> None:
        if result.reply is None:
            print(f"[系统] {result.session_id} 当前由人工接管，已记录消息但不自动回复")
            return
        suffix = f"（合并 {result.message_count} 条消息）" if result.message_count > 1 else ""
        print(f"[{result.session_id}] 机器人{suffix}: {result.reply}")
