from __future__ import annotations

from app.core.session import SessionManager


def test_get_or_create_reuses_session() -> None:
    manager = SessionManager()
    first = manager.get_or_create("user-1")
    second = manager.get_or_create("user-1")

    assert first is second


def test_session_records_messages_in_order() -> None:
    session = SessionManager().get_or_create("user-1")
    session.add_message("user", "hello")
    session.add_message("assistant", "hi")

    assert [item.content for item in session.messages] == ["hello", "hi"]


def test_session_history_is_bounded() -> None:
    session = SessionManager(max_messages=3).get_or_create("user-1")
    for index in range(5):
        session.add_message("user", str(index))

    assert [item.content for item in session.messages] == ["2", "3", "4"]
