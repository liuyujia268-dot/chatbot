from pathlib import Path

from app.storage.database import Database
from app.ui.repository import DesktopRepository


def make_repository(path: Path) -> DesktopRepository:
    database = Database(path)
    database.initialize()
    repository = DesktopRepository(database)
    repository.initialize()
    return repository


def add_contact(repository: DesktopRepository, account: str, user_id: str) -> None:
    repository.upsert_conversation(
        platform="qq",
        account_id=account,
        conversation_id=f"qq:private:{user_id}",
        conversation_type="private",
        peer_id=user_id,
        display_name=f"联系人{user_id}",
        nickname=f"昵称{user_id}",
    )


def test_new_messages_do_not_reorder_conversation_list(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "ui.db")
    add_contact(repository, "10001", "20001")
    add_contact(repository, "10001", "20002")

    repository.record_message(
        platform="qq",
        account_id="10001",
        conversation_id="qq:private:20001",
        event_key="in:m1",
        sender_id="20001",
        sender_name="联系人20001",
        role="user",
        source_type="other_user",
        text="新消息",
        parts_json="[]",
        created_at="2026-09-10T12:00:00+00:00",
        increment_unread=True,
    )

    items = repository.list_conversations(
        platform="qq",
        account_id="10001",
        conversation_type="private",
    )
    assert [item.peer_id for item in items] == ["20001", "20002"]
    assert items[0].unread_count == 1


def test_pin_manual_order_and_read_state_are_persistent(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "ui.db")
    for user_id in ("20001", "20002", "20003"):
        add_contact(repository, "10001", user_id)

    repository.reorder(
        platform="qq",
        account_id="10001",
        conversation_ids=[
            "qq:private:20003",
            "qq:private:20001",
            "qq:private:20002",
        ],
    )
    repository.set_pinned(
        platform="qq",
        account_id="10001",
        conversation_id="qq:private:20002",
        pinned=True,
    )
    items = repository.list_conversations(
        platform="qq",
        account_id="10001",
        conversation_type="private",
    )
    assert [item.peer_id for item in items] == ["20002", "20003", "20001"]


def test_desktop_message_event_is_deduplicated_and_can_be_marked_read(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path / "ui.db")
    add_contact(repository, "10001", "20001")
    arguments = dict(
        platform="qq",
        account_id="10001",
        conversation_id="qq:private:20001",
        event_key="in:same",
        sender_id="20001",
        sender_name="昵称",
        role="user",
        source_type="other_user",
        text="你好",
        parts_json='[{"type":"text","text":"你好"}]',
        created_at="2026-09-10T12:00:00+00:00",
        increment_unread=True,
    )
    assert repository.record_message(**arguments) is True
    assert repository.record_message(**arguments) is False
    assert len(
        repository.list_messages(
            platform="qq",
            account_id="10001",
            conversation_id="qq:private:20001",
        )
    ) == 1
    repository.mark_read(
        platform="qq",
        account_id="10001",
        conversation_id="qq:private:20001",
    )
    item = repository.list_conversations(
        platform="qq",
        account_id="10001",
        conversation_type="private",
    )[0]
    assert item.unread_count == 0


def test_backfilled_history_is_listed_by_message_time(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "ui.db")
    add_contact(repository, "10001", "20001")
    common = dict(
        platform="qq",
        account_id="10001",
        conversation_id="qq:private:20001",
        sender_id="20001",
        sender_name="联系人",
        role="user",
        source_type="other_user",
        parts_json="[]",
        increment_unread=False,
    )
    repository.record_message(
        **common,
        event_key="newer",
        text="较新消息",
        created_at="2026-09-11T12:00:00+00:00",
    )
    repository.record_message(
        **common,
        event_key="older-loaded-later",
        text="较旧历史",
        created_at="2026-09-10T12:00:00+00:00",
    )

    messages = repository.list_messages(
        platform="qq",
        account_id="10001",
        conversation_id="qq:private:20001",
    )

    assert [message.text for message in messages] == ["较旧历史", "较新消息"]
