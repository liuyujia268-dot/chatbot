"""QQ/NapCat adapter placeholder."""

from __future__ import annotations

from app.adapters.base import PlatformAdapter


class QQAdapter(PlatformAdapter):
    async def run(self) -> None:
        raise NotImplementedError("QQ/NapCat integration has not been configured yet")

