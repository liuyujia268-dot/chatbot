from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.storage.database import Database
from app.ui.desktop import DesktopApp
from app.ui.repository import DesktopRepository
from app.ui.runtime import DesktopRuntime, parse_onebot_history_events


def history_event(
    message_id: str,
    *,
    timestamp: int,
    self_message: bool = False,
) -> dict:
    account_id = "10001"
    peer_id = "20002"
    return {
        "post_type": "message_sent" if self_message else "message",
        "message_type": "private",
        "self_id": account_id,
        "user_id": account_id if self_message else peer_id,
        "message_id": message_id,
        "time": timestamp,
        "sender": {
            "user_id": account_id if self_message else peer_id,
            "nickname": "测试昵称",
        },
        "message": [
            {"type": "text", "data": {"text": f"消息 {message_id}"}},
        ],
    }


def test_history_parser_normalizes_self_message_and_sorts_chronologically() -> None:
    parsed = parse_onebot_history_events(
        [
            history_event("new", timestamp=20, self_message=True),
            history_event("old", timestamp=10),
        ],
        account_id="10001",
        conversation_id="qq:private:20002",
    )

    assert [item.message.message_id for item in parsed] == ["old", "new"]
    assert [item.message.is_self for item in parsed] == [False, True]
    assert {
        item.message.conversation_id for item in parsed
    } == {"qq:private:20002"}


def test_runtime_requests_history_without_dispatching_chat_model() -> None:
    class FakeOneBot:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def call_action(self, action: str, params: dict):
            self.calls.append((action, params))
            return {"messages": [history_event("history-1", timestamp=10)]}

    async def scenario() -> None:
        runtime = DesktopRuntime(Settings(desktop_history_message_limit=7))
        client = FakeOneBot()
        runtime._onebot = client
        runtime._account_id = "10001"

        await runtime._load_history("qq:private:20002")

        event = runtime.events.get_nowait()
        assert event.kind == "history"
        assert len(event.payload["messages"]) == 1
        assert client.calls == [
            (
                "get_friend_msg_history",
                {
                    "user_id": "20002",
                    "message_seq": "0",
                    "count": 7,
                    "reverse_order": False,
                    "disable_get_url": False,
                },
            )
        ]

    asyncio.run(scenario())


def test_late_history_for_other_contact_never_refreshes_current_chat(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "ui.db")
    database.initialize()
    repository = DesktopRepository(database)
    repository.initialize()
    repository.upsert_conversation(
        platform="qq",
        account_id="10001",
        conversation_id="qq:private:20002",
        conversation_type="private",
        peer_id="20002",
        display_name="联系人 A",
    )
    parsed = parse_onebot_history_events(
        [history_event("late", timestamp=10)],
        account_id="10001",
        conversation_id="qq:private:20002",
    )
    refreshed: list[bool] = []
    app = SimpleNamespace(
        _account_id="10001",
        _current=SimpleNamespace(
            account_id="10001",
            conversation_id="qq:private:30003",
        ),
        _history_inflight={("10001", "qq:private:20002")},
        _history_loaded=set(),
        repository=repository,
        _refresh_messages=lambda: refreshed.append(True),
    )

    DesktopApp._record_history(
        app,
        {
            "account_id": "10001",
            "conversation_id": "qq:private:20002",
            "messages": parsed,
        },
    )

    assert refreshed == []
    assert ("10001", "qq:private:20002") in app._history_loaded
    assert len(
        repository.list_messages(
            platform="qq",
            account_id="10001",
            conversation_id="qq:private:20002",
        )
    ) == 1
