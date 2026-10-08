"""Explicit, conservative migration of unscoped legacy QQ records.

Never invoked by application startup. Unknown ownership stays in its old namespace.
"""

from __future__ import annotations

import json

from app.core.identity import conversation_key, platform_conversation_id
from app.storage.database import Database


def migrate_legacy_accounts(database: Database, *, message_event_links: dict[int, int] | None = None) -> dict[str, int]:
    """Only an explicit source link (stored or manually reviewed) proves ownership.

    Identical text / a single currently known account is not sufficient evidence.
    """
    counts = {"assigned": 0, "unassigned": 0}
    with database.connect() as connection:
        messages = connection.execute(
            "SELECT * FROM messages WHERE session_id LIKE 'qq:%' ORDER BY id"
        ).fetchall()
        ownership: dict[int, str] = {}

        def note(table: str, record_id: object, old: str, target: str | None) -> None:
            connection.execute(
                "INSERT INTO legacy_assignment VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(table_name, record_id) DO UPDATE SET "
                "status=excluded.status, assigned_session_id=excluded.assigned_session_id",
                (table, str(record_id), old, "assigned" if target else "unassigned", target),
            )
            counts["assigned" if target else "unassigned"] += 1

        for message in messages:
            event_id = message['platform_event_id'] or (message_event_links or {}).get(int(message['id']))
            rows = connection.execute(
                "SELECT platform, account_id FROM inbound_platform_messages "
                "WHERE id = ? AND conversation_id = ? AND text = ? AND is_self = ?",
                (event_id, message["session_id"], message["content"], int(message["role"] == "assistant")),
            ).fetchall()
            target = None
            if len(rows) == 1 and message["role"] in {"user", "assistant"}:
                target = conversation_key(rows[0]["platform"], rows[0]["account_id"], message["session_id"])
                connection.execute("UPDATE messages SET session_id = ?,platform_event_id=?,history_kind='real' WHERE id = ?", (target, event_id, message["id"]))
                ownership[int(message["id"])] = target
            note("messages", message["id"], message["session_id"], target)

        # Repeated runs and derivative ranges can reference already migrated IDs.
        for row in connection.execute("SELECT id, session_id FROM messages WHERE session_id LIKE 'scope:%'"):
            ownership[int(row["id"])] = str(row["session_id"])

        def range_owner(old: str, start: int, end: int) -> str | None:
            rows = connection.execute(
                "SELECT id, session_id FROM messages WHERE id BETWEEN ? AND ? AND "
                "(session_id = ? OR session_id LIKE ?) ORDER BY id",
                (start, end, old, f"scope:%:{old}"),
            ).fetchall()
            ids = {int(row["id"]) for row in rows}
            owners = {ownership.get(record_id) for record_id in ids}
            return next(iter(owners)) if start in ids and end in ids and len(owners) == 1 and None not in owners else None

        for table, start_column, end_column in (
            ("conversation_summaries", "start_message_id", "end_message_id"),
            ("long_term_memories", "source_start_message_id", "source_end_message_id"),
        ):
            for row in connection.execute(f"SELECT * FROM {table} WHERE conversation_id LIKE 'qq:%' ORDER BY id").fetchall():
                old = row["conversation_id"]
                target = range_owner(old, row[start_column], row[end_column])
                parent_column = 'previous_summary_id' if table == 'conversation_summaries' else 'supersedes_memory_id'
                if target and row[parent_column] is not None:
                    parent = connection.execute(f'SELECT conversation_id FROM {table} WHERE id=?', (row[parent_column],)).fetchone()
                    if parent is None or parent[0] != target:
                        target = None
                if target:
                    connection.execute(f"UPDATE {table} SET conversation_id = ? WHERE id = ?", (target, row["id"]))
                note(table, row["id"], old, target)
        for row in connection.execute("SELECT * FROM memory_candidates WHERE conversation_id LIKE 'qq:%' ORDER BY id").fetchall():
            owners = {ownership.get(int(record_id)) for record_id in json.loads(row["source_message_ids"])}
            target = next(iter(owners)) if len(owners) == 1 and None not in owners else None
            if target and platform_conversation_id(target) != row['conversation_id']:
                target = None
            if target and row['target_memory_id'] is not None:
                parent = connection.execute('SELECT conversation_id FROM long_term_memories WHERE id=?', (row['target_memory_id'],)).fetchone()
                if parent is None or parent[0] != target:
                    target = None
            if target:
                connection.execute("UPDATE memory_candidates SET conversation_id = ? WHERE id = ?", (target, row["id"]))
            note("memory_candidates", row["id"], row["conversation_id"], target)

        # No account evidence exists on manually configured legacy profiles/state.
        for table, column in (("contact_profiles", "contact_id"), ("memory_processing_state", "conversation_id")):
            for row in connection.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE 'qq:%'").fetchall():
                note(table, row[0], row[0], None)
        if connection.execute("SELECT 1 FROM current_account_profile").fetchone():
            note("current_account_profile", 1, "", None)
    return counts
