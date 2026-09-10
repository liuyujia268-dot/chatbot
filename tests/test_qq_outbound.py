from __future__ import annotations

import asyncio
from typing import Any

from app.adapters.outbound import NapCatMessageSender
from app.core.platform import OutboundMessage


class FakeOneBotClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_action(
        self,
        action: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        self.calls.append((action, params))
        return {"message_id": "sent-42"}


def test_napcat_sender_uses_private_action_and_tracks_ai_message() -> None:
    async def scenario() -> None:
        client = FakeOneBotClient()
        sent: list[tuple[str, str]] = []
        sender = NapCatMessageSender(client, on_sent=lambda a, m: sent.append((a, m)))
        await sender.send(
            OutboundMessage(
                platform="qq",
                account_id="10001",
                conversation_id="qq:private:20002",
                text="真的发出去了",
            )
        )

        action, params = client.calls[0]
        assert action == "send_private_msg"
        assert params["user_id"] == "20002"
        assert params["message"][0]["data"]["text"] == "真的发出去了"
        assert sent == [("10001", "sent-42")]

    asyncio.run(scenario())


def test_napcat_sender_uses_group_action() -> None:
    async def scenario() -> None:
        client = FakeOneBotClient()
        sender = NapCatMessageSender(client)
        await sender.send(
            OutboundMessage(
                platform="qq",
                account_id="10001",
                conversation_id="qq:group:80001",
                text="群回复",
            )
        )

        action, params = client.calls[0]
        assert action == "send_group_msg"
        assert params["group_id"] == "80001"

    asyncio.run(scenario())
