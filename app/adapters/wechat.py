"""WeChat/wxauto adapter placeholder."""

from __future__ import annotations

from app.adapters.base import PlatformAdapter


class WeChatAdapter(PlatformAdapter):
    async def run(self) -> None:
        raise NotImplementedError("WeChat/wxauto integration has not been configured yet")

