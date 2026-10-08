from __future__ import annotations

from pathlib import Path

from app.storage.database import Database
from app.ui.repository import DesktopRepository


def test_purge_group_data_removes_group_rows_and_preserves_private(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "chat.db")
    database.initialize()
    DesktopRepository(database).initialize()
    group_id = "qq:group:90001"
    private_id = "qq:private:20002"

    with database.connect() as connection:
        for conversation_id in (group_id, private_id):
            connection.execute(
                "INSERT INTO messages (session_id, role, content) "
                "VALUES (?, 'user', 'hello')",
                (conversation_id,),
            )
            message_id = "group-message" if conversation_id == group_id else (
                "private-message"
            )
            connection.execute(
                "INSERT INTO inbound_platform_messages "
                "(platform, account_id, message_id, conversation_id, sender_id, "
                "event_timestamp, message_type, text, parts_json, is_self) "
                "VALUES ('qq', '10001', ?, ?, 'sender', '2026-09-29T12:00:00+08:00', "
                "'text', 'hello', '[]', 0)",
                (message_id, conversation_id),
            )
            connection.execute(
                "INSERT INTO image_descriptions "
                "(platform, account_id, message_id, part_index, description) "
                "VALUES ('qq', '10001', ?, 0, 'image')",
                (message_id,),
            )
            connection.execute(
                "INSERT INTO conversation_summaries "
                "(conversation_id, content, start_message_id, end_message_id) "
                "VALUES (?, 'summary', 1, 1)",
                (conversation_id,),
            )
            connection.execute(
                "INSERT INTO memory_candidates "
                "(conversation_id, memory_type, memory_key, content, confidence, "
                "source_message_ids, source_quote, action, status) "
                "VALUES (?, 'fact', 'key', 'memory', 0.9, '[1]', 'quote', "
                "'create', 'accepted')",
                (conversation_id,),
            )
            connection.execute(
                "INSERT INTO long_term_memories "
                "(conversation_id, memory_type, memory_key, content, "
                "source_start_message_id, source_end_message_id, confidence) "
                "VALUES (?, 'fact', 'key', 'memory', 1, 1, 0.9)",
                (conversation_id,),
            )
            connection.execute(
                "INSERT INTO contact_profiles (contact_id, notes) VALUES (?, 'note')",
                (conversation_id,),
            )
            connection.execute(
                "INSERT INTO memory_processing_state "
                "(conversation_id, last_processed_message_id) VALUES (?, 1)",
                (conversation_id,),
            )
            conversation_type = (
                "group" if conversation_id == group_id else "private"
            )
            peer_id = "90001" if conversation_id == group_id else "20002"
            connection.execute(
                "INSERT INTO desktop_conversations "
                "(platform, account_id, conversation_id, conversation_type, "
                "peer_id, display_name, sort_order) "
                "VALUES ('qq', '10001', ?, ?, ?, 'name', 1)",
                (conversation_id, conversation_type, peer_id),
            )
            connection.execute(
                "INSERT INTO desktop_messages "
                "(platform, account_id, conversation_id, event_key, sender_id, "
                "sender_name, role, source_type, text, created_at) "
                "VALUES ('qq', '10001', ?, ?, 'sender', 'name', 'user', "
                "'other_user', 'hello', '2026-09-29T12:00:00+08:00')",
                (conversation_id, f"event-{peer_id}"),
            )
            connection.execute(
                "INSERT INTO style_samples "
                "(platform, conversation_type, conversation_id, context_messages, "
                "human_reply_message_id, human_reply) "
                "VALUES ('qq', ?, ?, '[]', ?, 'reply')",
                (conversation_type, conversation_id, f"reply-{peer_id}"),
            )
        connection.execute(
            "INSERT INTO qq_training_group_events "
            "(platform, account_id, group_id, sender_id, message_id, "
            "event_timestamp, text, parts_json, is_self, source_type) "
            "VALUES ('qq', '10001', '90001', 'sender', 'training-message', "
            "'2026-09-29T12:00:00+08:00', 'hello', '[]', 0, 'other_user')"
        )

    deleted = database.purge_group_data()

    assert deleted["qq_training_group_events"] == 1
    with database.connect() as connection:
        group_checks = (
            ("messages", "session_id"),
            ("inbound_platform_messages", "conversation_id"),
            ("conversation_summaries", "conversation_id"),
            ("memory_candidates", "conversation_id"),
            ("long_term_memories", "conversation_id"),
            ("contact_profiles", "contact_id"),
            ("memory_processing_state", "conversation_id"),
            ("style_samples", "conversation_id"),
            ("desktop_conversations", "conversation_id"),
            ("desktop_messages", "conversation_id"),
        )
        for table, column in group_checks:
            total = connection.execute(
                f"SELECT COUNT(*) AS total FROM {table} WHERE {column} = ?",
                (group_id,),
            ).fetchone()["total"]
            assert total == 0, table
        private_checks = group_checks
        for table, column in private_checks:
            total = connection.execute(
                f"SELECT COUNT(*) AS total FROM {table} WHERE {column} = ?",
                (private_id,),
            ).fetchone()["total"]
            assert total == 1, table
        image_rows = connection.execute(
            "SELECT message_id FROM image_descriptions ORDER BY message_id"
        ).fetchall()
        assert [row["message_id"] for row in image_rows] == ["private-message"]
        training_total = connection.execute(
            "SELECT COUNT(*) AS total FROM qq_training_group_events"
        ).fetchone()["total"]
        assert training_total == 0


def test_purge_self_private_data_removes_only_invalid_self_chat(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "chat.db")
    database.initialize()
    DesktopRepository(database).initialize()
    account_id = "10001"
    self_conversation = "qq:private:10001"
    valid_conversation = "qq:private:20002"

    with database.connect() as connection:
        for conversation_id, peer_id in (
            (self_conversation, account_id),
            (valid_conversation, "20002"),
        ):
            message_id = f"message-{peer_id}"
            connection.execute(
                "INSERT INTO messages (session_id, role, content) "
                "VALUES (?, 'assistant', 'hello')",
                (conversation_id,),
            )
            connection.execute(
                "INSERT INTO inbound_platform_messages "
                "(platform, account_id, message_id, conversation_id, sender_id, "
                "event_timestamp, message_type, text, parts_json, is_self) "
                "VALUES ('qq', ?, ?, ?, ?, '2026-09-29T12:00:00+08:00', "
                "'text', 'hello', '[]', 1)",
                (account_id, message_id, conversation_id, account_id),
            )
            connection.execute(
                "INSERT INTO desktop_conversations "
                "(platform, account_id, conversation_id, conversation_type, "
                "peer_id, display_name, sort_order) "
                "VALUES ('qq', ?, ?, 'private', ?, 'name', 1)",
                (account_id, conversation_id, peer_id),
            )
            connection.execute(
                "INSERT INTO desktop_messages "
                "(platform, account_id, conversation_id, event_key, sender_id, "
                "sender_name, role, source_type, text, created_at) "
                "VALUES ('qq', ?, ?, ?, ?, 'me', 'assistant', 'human_self', "
                "'hello', '2026-09-29T12:00:00+08:00')",
                (
                    account_id,
                    conversation_id,
                    f"event-{peer_id}",
                    account_id,
                ),
            )

    deleted = database.purge_self_private_data(account_id)

    assert deleted["inbound_platform_messages"] == 1
    assert deleted["desktop_conversations"] == 1
    for table, column in (
        ("messages", "session_id"),
        ("inbound_platform_messages", "conversation_id"),
        ("desktop_conversations", "conversation_id"),
        ("desktop_messages", "conversation_id"),
    ):
        with database.connect() as connection:
            self_total = connection.execute(
                f"SELECT COUNT(*) AS total FROM {table} WHERE {column} = ?",
                (self_conversation,),
            ).fetchone()["total"]
            valid_total = connection.execute(
                f"SELECT COUNT(*) AS total FROM {table} WHERE {column} = ?",
                (valid_conversation,),
            ).fetchone()["total"]
        assert self_total == 0, table
        assert valid_total == 1, table
