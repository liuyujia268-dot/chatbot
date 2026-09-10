"""Human-controlled memory, profile, account, and debug CLI commands."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.memory.context import ContextDebugger
from app.memory.processor import MemoryProcessor
from app.memory.service import MemoryService
from app.models.base import ModelError
from app.style.repository import StyleRepository


class AdminCommandHandler:
    def __init__(
        self,
        service: MemoryService,
        processor: MemoryProcessor,
        debugger: ContextDebugger,
        *,
        style_repository: StyleRepository | None = None,
        style_export_path: Path | None = None,
        sink: Callable[[str], None] = print,
    ) -> None:
        self.service = service
        self.processor = processor
        self.debugger = debugger
        self.style_repository = style_repository
        self.style_export_path = style_export_path
        self._sink = sink

    async def handle(self, line: str) -> bool:
        command = line.strip()
        if not command.startswith(
            ("/memory", "/profile", "/account", "/debug", "/style")
        ):
            return False
        try:
            if command.startswith("/memory"):
                await self._memory(command)
            elif command.startswith("/profile"):
                self._profile(command)
            elif command.startswith("/account"):
                self._account(command)
            elif command.startswith("/style"):
                self._style(command)
            else:
                self._debug(command)
        except (ValueError, KeyError, ModelError) as exc:
            self._sink(f"[管理命令错误] {exc}")
        return True

    async def _memory(self, command: str) -> None:
        parts = command.split()
        if len(parts) < 2 or parts[1].lower() == "help":
            self._sink(self.memory_help())
            return
        action = parts[1].lower()
        if action in {"list", "show", "summary", "candidates", "messages"}:
            self._show_memory(action, parts)
            return
        if action == "process" and len(parts) == 3:
            report = await self.processor.process(parts[2], force=True)
            self._sink(
                f"[记忆维护] summary={report.summary_id} "
                f"candidates={len(report.candidate_ids)} "
                f"accepted={len(report.accepted_memory_ids)} "
                f"skip={report.skipped_reason or 'none'}"
            )
            return
        self._memory_mutation(command, action)

    def _show_memory(self, action: str, parts: list[str]) -> None:
        repository = self.service.repository
        if action == "list" and len(parts) in {3, 4}:
            status = None if len(parts) == 4 and parts[3] == "all" else "active"
            memories = repository.list_memories(parts[2], status=status)
            if not memories:
                self._sink("[记忆] 没有符合条件的记录")
                return
            for memory in memories:
                self._sink(
                    f"[记忆 #{memory.id}] {memory.status} "
                    f"{memory.memory_type}/{memory.memory_key} "
                    f"{memory.content} source="
                    f"{memory.source_start_message_id}-"
                    f"{memory.source_end_message_id}"
                )
            return
        if action == "show" and len(parts) == 3:
            memory = repository.get_memory(int(parts[2]))
            if memory is None:
                raise ValueError("记忆不存在")
            self._sink(
                f"[记忆 #{memory.id}] conversation={memory.conversation_id} "
                f"status={memory.status} type={memory.memory_type} "
                f"key={memory.memory_key} confidence={memory.confidence:.2f}\n"
                f"内容：{memory.content}\n"
                f"来源消息：{memory.source_start_message_id}-"
                f"{memory.source_end_message_id}"
            )
            return
        if action == "summary" and len(parts) == 3:
            summary = repository.latest_summary(parts[2])
            if summary is None:
                self._sink("[摘要] 暂无")
            else:
                self._sink(
                    f"[摘要 #{summary.id}] source="
                    f"{summary.start_message_id}-{summary.end_message_id}\n"
                    f"{summary.content}"
                )
            return
        if action == "candidates" and len(parts) == 3:
            candidates = repository.list_candidates(parts[2], status=None)
            if not candidates:
                self._sink("[候选] 暂无")
                return
            for candidate in candidates:
                self._sink(
                    f"[候选 #{candidate.id}] {candidate.status} "
                    f"{candidate.action} {candidate.memory_type}/"
                    f"{candidate.memory_key} {candidate.content}"
                    f" source_ids={','.join(map(str, candidate.source_message_ids))}"
                )
            return
        if action == "messages" and len(parts) in {3, 4}:
            limit = int(parts[3]) if len(parts) == 4 else 20
            messages = repository.database.list_recent_stored_messages(
                parts[2], limit=limit
            )
            if not messages:
                self._sink("[原始消息] 暂无")
                return
            for message in messages:
                self._sink(
                    f"[消息 #{message.id}] {message.role}: {message.content}"
                )
            return
        raise ValueError("记忆查看命令格式不正确，输入 /memory help")

    def _memory_mutation(self, command: str, action: str) -> None:
        repository = self.service.repository
        if action in {"disable", "delete"}:
            parts = command.split()
            if len(parts) != 3:
                raise ValueError("用法：/memory disable <memory_id>")
            if not repository.disable_memory(int(parts[2])):
                raise ValueError("记忆不存在或已经不是 active")
            self._sink("[记忆] 已禁用，原记录仍保留")
            return
        if action == "accept":
            parts = command.split()
            if len(parts) != 3:
                raise ValueError("用法：/memory accept <candidate_id>")
            memory = self.service.accept_candidate(int(parts[2]))
            self._sink(f"[记忆] 候选已接受为 #{memory.id}")
            return
        if action == "add":
            parts = command.split(maxsplit=6)
            if len(parts) != 7:
                raise ValueError(
                    "用法：/memory add <联系人> <type> <key> "
                    "<source_ids> <内容>"
                )
            candidate, memory = self.service.add_manual_memory(
                conversation_id=parts[2],
                memory_type=parts[3],
                memory_key=parts[4],
                source_message_ids=self._source_ids(parts[5]),
                content=parts[6],
            )
            self._report_candidate(candidate, memory)
            return
        if action == "update":
            parts = command.split(maxsplit=4)
            if len(parts) != 5:
                raise ValueError(
                    "用法：/memory update <memory_id> <source_ids> <内容>"
                )
            old = repository.get_memory(int(parts[2]))
            if old is None:
                raise ValueError("要更新的记忆不存在")
            candidate, memory = self.service.add_manual_memory(
                conversation_id=old.conversation_id,
                memory_type=old.memory_type,
                memory_key=old.memory_key,
                source_message_ids=self._source_ids(parts[3]),
                content=parts[4],
                target_memory_id=old.id,
            )
            self._report_candidate(candidate, memory)
            return
        raise ValueError("未知记忆命令，输入 /memory help")

    def _report_candidate(self, candidate, memory) -> None:
        if memory is not None:
            self._sink(
                f"[记忆] candidate #{candidate.id} 已接受为 memory #{memory.id}"
            )
        else:
            detail = (
                f"：{candidate.rejection_reason}"
                if candidate.rejection_reason
                else ""
            )
            self._sink(
                f"[记忆] candidate #{candidate.id} 状态="
                f"{candidate.status}{detail}"
            )

    @staticmethod
    def _source_ids(value: str) -> tuple[int, ...]:
        try:
            ids = tuple(int(item) for item in value.split(","))
        except ValueError as exc:
            raise ValueError("source_ids 使用逗号分隔的消息 ID") from exc
        if not ids or any(message_id < 1 for message_id in ids):
            raise ValueError("source_ids 必须是正整数")
        return ids

    def _profile(self, command: str) -> None:
        parts = command.split(maxsplit=4)
        if len(parts) == 3 and parts[1].lower() == "show":
            profile = self.service.repository.get_contact_profile(parts[2])
            if profile is None:
                self._sink(f"[联系人画像] {parts[2]} 暂无已配置字段")
                return
            self._sink(
                f"[联系人画像] {profile.contact_id}\n"
                f"常用称呼：{profile.preferred_name or '-'}\n"
                f"关系：{profile.relationship or '-'}\n"
                f"交流风格：{profile.communication_style or '-'}\n"
                f"稳定偏好：{profile.stable_preferences or '-'}\n"
                f"备注：{profile.notes or '-'}"
            )
            return
        if len(parts) == 5 and parts[1].lower() == "set":
            profile = self.service.set_contact_profile(parts[2], parts[3], parts[4])
            self._sink(f"[联系人画像] {profile.contact_id} 的 {parts[3]} 已更新")
            return
        raise ValueError("用法：/profile show <联系人> 或 /profile set <联系人> <field> <内容>")

    def _account(self, command: str) -> None:
        parts = command.split(maxsplit=3)
        if len(parts) == 2 and parts[1].lower() == "show":
            profile = self.service.repository.get_account_profile()
            if profile is None:
                self._sink("[当前账号资料] 暂无已配置字段")
                return
            private_state = "已配置（内容隐藏）" if profile.private_information else "-"
            self._sink(
                "[当前账号资料]\n"
                f"对外称呼：{profile.public_name or '-'}\n"
                f"基础背景：{profile.background or '-'}\n"
                f"可公开信息：{profile.public_information or '-'}\n"
                f"不应主动透露：{private_state}"
            )
            return
        if len(parts) == 4 and parts[1].lower() == "set":
            self.service.set_account_profile(parts[2], parts[3])
            self._sink(f"[当前账号资料] {parts[2]} 已更新")
            return
        raise ValueError("用法：/account show 或 /account set <field> <内容>")

    def _debug(self, command: str) -> None:
        parts = command.split()
        if len(parts) != 2 or parts[1].lower() not in {"on", "off"}:
            raise ValueError("用法：/debug on|off")
        self.debugger.enabled = parts[1].lower() == "on"
        self._sink(f"[Debug] {'已开启' if self.debugger.enabled else '已关闭'}")

    def _style(self, command: str) -> None:
        if self.style_repository is None:
            raise ValueError("聊天风格样本库未启用")
        parts = command.split()
        action = parts[1].lower() if len(parts) > 1 else "help"
        if action == "help":
            self._sink(
                "风格样本命令：\n/style stats\n/style list [status]\n"
                "/style show <sample_id>\n/style accept <sample_id>\n"
                "/style reject <sample_id>\n/style export"
            )
            return
        if action == "stats" and len(parts) == 2:
            stats = self.style_repository.stats()
            self._sink(
                "[风格样本] "
                f"candidate={stats['candidate']} "
                f"accepted={stats['accepted']} "
                f"rejected={stats['rejected']}"
            )
            return
        if action == "list" and len(parts) in {2, 3}:
            status = parts[2] if len(parts) == 3 else "candidate"
            samples = self.style_repository.list_samples(status)
            if not samples:
                self._sink("[风格样本] 暂无")
                return
            for sample in samples:
                self._sink(
                    f"[样本 #{sample.sample_id}] {sample.status} "
                    f"context={len(sample.context_messages)} "
                    f"reply={sample.human_reply[:80]}"
                )
            return
        if action == "show" and len(parts) == 3:
            sample = self.style_repository.get_sample(int(parts[2]))
            if sample is None:
                raise ValueError("风格样本不存在")
            self._sink(
                f"[样本 #{sample.sample_id}] {sample.status}\n"
                f"context={sample.context_messages}\n"
                f"response={sample.human_reply}"
            )
            return
        if action in {"accept", "reject"} and len(parts) == 3:
            status = "accepted" if action == "accept" else "rejected"
            if not self.style_repository.set_status(int(parts[2]), status):
                raise ValueError("风格样本不存在")
            self._sink(f"[风格样本] #{parts[2]} 已标记为 {status}")
            return
        if action == "export" and len(parts) == 2:
            if self.style_export_path is None:
                raise ValueError("STYLE_EXPORT_PATH 未配置")
            total = self.style_repository.export_accepted(self.style_export_path)
            self._sink(f"[风格样本] 已脱敏导出 {total} 条")
            return
        raise ValueError("风格样本命令格式不正确，输入 /style help")

    @staticmethod
    def memory_help() -> str:
        return (
            "记忆命令：\n/memory list <联系人> [all]\n"
            "/memory show <memory_id>\n/memory summary <联系人>\n"
            "/memory candidates <联系人>\n/memory messages <联系人> [limit]\n"
            "/memory process <联系人>\n"
            "/memory add <联系人> <type> <key> <source_ids> <内容>\n"
            "/memory update <memory_id> <source_ids> <内容>\n"
            "/memory accept <candidate_id>\n/memory disable <memory_id>"
        )
