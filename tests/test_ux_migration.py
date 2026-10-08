"""Old database copies only: preserve evidence, never infer ownership from text."""
import sqlite3

from app.adapters.onebot import parse_onebot_event
from app.core.identity import conversation_key
from app.core.session import Message
from app.memory.repository import MemoryRepository
from app.memory.types import CandidateProposal
from app.storage.database import Database
from app.storage.migration import migrate_legacy_accounts
from tests.ux_support import event


ROUTE = 'qq:private:20002'
KEY = conversation_key('qq', '10001', ROUTE)


def test_oldest_schema_migration_keeps_all_relations_and_is_idempotent(tmp_path):
    path = tmp_path / 'reviewed-copy.sqlite'
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
                     "role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        conn.executemany('INSERT INTO messages(session_id,role,content) VALUES (?,?,?)', [
            (ROUTE, 'user', '准备考试'), (ROUTE, 'user', '改成下个月'), (ROUTE, 'assistant', '未确认是否真的发送')])
    db = Database(path)
    db.initialize()
    repo = MemoryRepository(db)
    for mid, text in (('a', '准备考试'), ('b', '改成下个月')):
        db.save_inbound_platform_event(parse_onebot_event(event(mid, text)).message)
    summary1 = repo.add_summary(ROUTE, '早期摘要', 1, 1, None)
    summary2 = repo.add_summary(ROUTE, '增量摘要', 1, 2, summary1.id)
    old = repo.create_memory(conversation_id=ROUTE, memory_type='ongoing_event', memory_key='exam.date',
        content='准备考试', source_start_message_id=1, source_end_message_id=1, confidence=1)
    new = repo.create_memory(conversation_id=ROUTE, memory_type='ongoing_event', memory_key='exam.date',
        content='下个月考试', source_start_message_id=2, source_end_message_id=2, confidence=1, supersedes_memory_id=old.id)
    candidate = repo.add_candidate(CandidateProposal(ROUTE, 'ongoing_event', 'exam.date', '下个月考试', 1,
        (1, 2), '改成下个月', 'update', new.id))
    repo.set_account_profile_field('public_information', 'LEGACY_UNASSIGNED_ACCOUNT', {'kind': 'manual'})
    repo.set_contact_profile_field(ROUTE, 'notes', '旧手工资料', {'kind': 'manual'})
    repo.update_processing_state(ROUTE, 2)
    with db.connect() as conn:
        source_ids = [row[0] for row in conn.execute('SELECT id FROM inbound_platform_messages ORDER BY id')]
    migrate_legacy_accounts(db, message_event_links={1: source_ids[0], 2: source_ids[1]})
    before = None
    for _ in range(2):
        with db.connect() as conn:
            rows = {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
                    for table in ('messages', 'conversation_summaries', 'long_term_memories', 'memory_candidates',
                                  'contact_profiles', 'memory_processing_state', 'current_account_profile', 'legacy_assignment')}
            assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        if before is not None:
            assert rows == before
        before = rows
        migrate_legacy_accounts(db)
    assert repo.latest_summary(KEY).previous_summary_id == summary1.id
    assert repo.latest_summary(KEY).id == summary2.id
    assert repo.get_memory(old.id).status == 'superseded'
    assert repo.get_memory(new.id).supersedes_memory_id == old.id
    assert repo.get_candidate(candidate.id).source_message_ids == (1, 2)
    assert repo.get_candidate(candidate.id).conversation_id == KEY
    assert repo.get_candidate(candidate.id).target_memory_id == new.id
    assert repo.get_contact_profile(KEY) is None and repo.get_account_profile('qq:10001') is None
    assert [m.id for m in db.list_stored_messages(ROUTE)] == [3]
    assert len(rows['messages']) == 3


def test_identical_text_and_cross_contact_sources_never_prove_ownership(tmp_path):
    db = Database(tmp_path / 'ambiguous-copy.sqlite')
    db.initialize()
    for account in ('10001', '10002'):
        db.save_inbound_platform_event(parse_onebot_event(event('same', '相同文本', account=account)).message)
    unknown = db.save_message(ROUTE, Message('user', '相同文本'))
    other_route = 'qq:private:20003'
    other = db.save_message(other_route, Message('user', '另一个联系人'))
    db.save_inbound_platform_event(parse_onebot_event(event('other', '另一个联系人', sender='20003')).message)
    repo = MemoryRepository(db)
    invalid = repo.add_candidate(CandidateProposal(ROUTE, 'fact', 'test.invalid', '来源不属于此联系人', 1,
                                                  (other,), '另一个联系人', 'create'))
    with db.connect() as conn:
        eid = conn.execute("SELECT id FROM inbound_platform_messages WHERE message_id='other'").fetchone()[0]
    migrate_legacy_accounts(db, message_event_links={other: eid})
    assert db.list_stored_messages(ROUTE)[0].id == unknown
    assert repo.get_candidate(invalid.id).conversation_id == ROUTE
    assert db.list_stored_messages(conversation_key('qq', '10002', ROUTE)) == []
