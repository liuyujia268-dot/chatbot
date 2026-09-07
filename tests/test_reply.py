from __future__ import annotations

import asyncio

from app.core.reply_engine import ReplyEngine
from app.core.session import Session
from app.models.mock import MockModel


def test_mock_model_returns_fixed_reply() -> None:
    async def scenario() -> None:
        session = Session("test")
        session.add_message("user", "任意消息")
        engine = ReplyEngine(MockModel())

        first = await engine.generate(session)
        session.add_message("user", "完全不同的消息")
        second = await engine.generate(session)

        assert first == MockModel.DEFAULT_REPLY
        assert second == MockModel.DEFAULT_REPLY

    asyncio.run(scenario())
