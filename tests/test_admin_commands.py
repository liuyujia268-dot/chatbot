from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.admin_commands import AdminCommandHandler
from app.core.session import Message
from app.memory.context import ContextDebugger
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.storage.database import Database


def build_handler(path: Path, output: list[str]) -> tuple[Database, AdminCommandHandler]:
    database = Database(path)
    database.initialize()
    service = MemoryService(MemoryRepository(database))
    handler = AdminCommandHandler(
        service,
        processor=None,  # type: ignore[arg-type]
        debugger=ContextDebugger(sink=output.append),
        sink=output.append,
    )
    return database, handler


def test_memory_can_be_added_listed_and_disabled_from_cli(tmp_path: Path) -> None:
    async def scenario() -> None:
        output: list[str] = []
        database, handler = build_handler(tmp_path / "memory.db", output)
        source_id = database.save_message("张三", Message("user", "我不吃香菜"))

        assert await handler.handle(
            f"/memory add 张三 preference food.cilantro {source_id} 张三不吃香菜"
        )
        await handler.handle("/memory messages 张三 5")
        await handler.handle("/memory list 张三")
        await handler.handle("/memory disable 1")

        assert any(f"[消息 #{source_id}] user" in line for line in output)
        assert any("preference/food.cilantro" in line for line in output)
        assert output[-1] == "[记忆] 已禁用，原记录仍保留"

    asyncio.run(scenario())


def test_profile_account_and_debug_commands_are_human_controlled(tmp_path: Path) -> None:
    async def scenario() -> None:
        output: list[str] = []
        _, handler = build_handler(tmp_path / "memory.db", output)

        await handler.handle("/profile set 张三 relationship 熟悉的朋友")
        await handler.handle("/profile show 张三")
        await handler.handle("/account set private_information 不要透露住址")
        await handler.handle(
            "/account set public_image_path app/assets/whale_girl_profile.jpg"
        )
        await handler.handle("/account show")
        await handler.handle("/debug on")

        assert any("关系：熟悉的朋友" in line for line in output)
        account = next(
            line
            for line in output
            if line.startswith("[当前账号资料]") and "\n" in line
        )
        assert "不要透露住址" not in account
        assert "app/assets/whale_girl_profile.jpg" not in account
        assert "资料图：已配置（路径隐藏）" in account
        assert "内容隐藏" in account
        assert handler.debugger.enabled is True

    asyncio.run(scenario())
