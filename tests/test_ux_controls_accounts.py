"""U01/U02/U03/U05: synthetic, offline acceptance regressions."""
import asyncio
from types import SimpleNamespace

from app.config import Settings
from app.core.identity import conversation_key
from app.core.session import Message
from app.memory.repository import MemoryRepository
from app.storage.migration import migrate_legacy_accounts
from app.ui.runtime import DesktopRuntime, _SwitchableSender
from tests.ux_support import FakeModel, FakeSender, event, system


ROUTE = "qq:private:20002"


def test_actual_runtime_switches_ack_and_gate_both_senders():
    async def run():
        runtime = DesktopRuntime(Settings(qq_dry_run=False))
        live = FakeSender()
        runtime._loop = asyncio.get_running_loop()
        runtime._ai_sender = _SwitchableSender(live, lambda *args: None, dry_run=False)
        runtime._manual_sender = _SwitchableSender(live, lambda *args: None, dry_run=False)
        runtime.set_dry_run(True)
        await asyncio.sleep(0)
        assert runtime.controls.dry_run
        from app.core.platform import OutboundMessage
        message = OutboundMessage("qq", "10001", ROUTE, "合成消息")
        await runtime._ai_sender.send(message)
        await runtime._manual_sender.send(message)
        assert live.calls == []
        replacement = DesktopRuntime(Settings(qq_dry_run=False))
        replacement.restore_controls(runtime.controls)
        assert replacement.controls.dry_run  # saved startup setting must not win
        assert runtime.events.get_nowait().kind == "dry_run"
    asyncio.run(run())


def test_takeover_restores_before_inbound_after_reconnect_and_restart(tmp_path):
    async def run():
        first = system(tmp_path / "test.db")
        key = conversation_key("qq", "10001", ROUTE)
        first.merger.set_human_takeover(key, True)
        second = system(tmp_path / "test.db")
        await second.adapter.handle_event(event("one"))
        assert second.model.calls == [] and second.sender.calls == []
        assert second.merger.is_human_takeover(key)
        assert not second.merger.is_human_takeover(conversation_key("qq", "10002", ROUTE))
        runtime = DesktopRuntime(Settings())
        runtime._account_id = '10001'
        runtime._core = SimpleNamespace(merger=second.merger, database=second.db)
        runtime._manual_sender = _SwitchableSender(second.sender, lambda *args: None, dry_run=False)
        await runtime._send_manual(ROUTE, '接管期间仍可人工发送', account_id='10001')
        assert len(second.sender.calls) == 1 and second.model.calls == []
        second.merger.set_human_takeover(key, False)
        await second.adapter.handle_event(event("two"))
        assert len(second.model.calls) == 1 and len(second.sender.calls) == 2
    asyncio.run(run())


def test_complete_model_context_isolated_by_account_and_contact(tmp_path):
    async def run():
        s = system(tmp_path / "test.db")
        marker = "ONLY_ACCOUNT_A_SECRET_MARKER"
        contact_marker = 'ONLY_CONTACT_A_PRIVATE_MARKER'
        key = conversation_key("qq", "10001", ROUTE)
        await s.adapter.handle_event(event("a", contact_marker))
        repo = MemoryRepository(s.db)
        mid = s.db.list_stored_messages(key)[0].id
        repo.add_summary(key, contact_marker, mid, mid, None)
        repo.create_memory(conversation_id=key, memory_type="fact", memory_key="test.fact", content=contact_marker,
                           source_start_message_id=mid, source_end_message_id=mid, confidence=1)
        repo.set_contact_profile_field(key, "notes", contact_marker, {"source": "manual"})
        repo.set_account_profile_field("public_information", marker, {"source": "manual"}, account_key="qq:10001")
        s.adapter.activate_account("10002")
        await s.adapter.handle_event(event("b", "新的账号", account="10002"))
        assert marker not in repr(s.model.calls[-1])
        assert contact_marker not in repr(s.model.calls[-1])
        await s.adapter.handle_event(event("c", "不同联系人", sender="20003", account="10002"))
        s.adapter.activate_account('10001')
        await s.adapter.handle_event(event('e', '同账号另一联系人', sender='20003'))
        assert marker in repr(s.model.calls[-1])  # confirmed account-wide public info
        assert contact_marker not in repr(s.model.calls[-1])
        restarted = system(tmp_path / "test.db")
        await restarted.adapter.handle_event(event("d", "重新启动", account="10002"))
        assert marker not in repr(restarted.model.calls[-1])
        assert contact_marker not in repr(restarted.model.calls[-1])
    asyncio.run(run())


def test_proactive_disable_invalidates_inflight_and_each_segment(tmp_path):
    async def run():
        model = FakeModel("普通回复", "第一段。第二段。")
        s = system(tmp_path / "test.db", model=model, proactive_enabled=True)
        await s.adapter.handle_event(event("a"))
        model.block = True
        task = asyncio.create_task(s.adapter.trigger_proactive_once(force=True))
        await model.started.wait()
        s.adapter.set_proactive_enabled(False)
        model.release.set()
        assert await task is None
        assert len(s.sender.calls) == 1
        s.adapter.set_proactive_enabled(True)
        model.block = False
        model.replies = ["第一段。第二段。"]
        s.sender.on_send = lambda _: s.adapter.set_proactive_enabled(False)
        await s.adapter.trigger_proactive_once(force=True)
        assert len(s.sender.calls) == 2  # one already-submitted segment is not recalled
    asyncio.run(run())


def test_account_switch_cancels_old_generation(tmp_path):
    async def run():
        model = FakeModel()
        model.block = True
        s = system(tmp_path / "test.db", model=model)
        pending = asyncio.create_task(s.adapter.handle_event(event("a")))
        await model.started.wait()
        s.adapter.activate_account("10002")
        model.release.set()
        await pending
        assert s.sender.calls == []
    asyncio.run(run())


def test_followup_disable_invalidates_inflight_and_remaining_segments(tmp_path):
    from app.core.platform import OutboundMessage
    async def run():
        model = FakeModel('第一条普通回应')
        s = system(tmp_path / 'test.db', model=model, followup_enabled=False, followup_probability=1,
                   followup_min_delay_seconds=0, followup_max_delay_seconds=0)
        await s.adapter.handle_event(event('a'))
        outbound = OutboundMessage('qq', '10001', ROUTE, '第一条普通回应')
        key = conversation_key('qq', '10001', ROUTE)
        version = s.merger.conversation_version(key)
        s.adapter.set_followup_enabled(True)
        model.block = True
        task = asyncio.create_task(s.adapter._maybe_send_followup(outbound, expected_version=version, label='测试'))
        await asyncio.wait_for(model.started.wait(), 1)
        s.adapter.set_followup_enabled(False)
        model.release.set()
        await task
        assert len(s.sender.calls) == 1
        s.adapter.set_followup_enabled(True)
        model.block = False
        model.replies = ['换个细节聊。然后再补一句。']
        s.sender.on_send = lambda _: s.adapter.set_followup_enabled(False)
        await s.adapter._maybe_send_followup(outbound, expected_version=version, label='测试')
        assert len(s.sender.calls) == 2
    asyncio.run(run())


def test_legacy_migration_preserves_sources_and_unknown_ownership(tmp_path):
    s = system(tmp_path / "legacy-copy.db")
    from app.adapters.onebot import parse_onebot_event
    s.db.save_inbound_platform_event(parse_onebot_event(event("a", "可归属文本")).message)
    mid = s.db.save_message(ROUTE, Message("user", "可归属文本"))
    unknown = s.db.save_message(ROUTE, Message("user", "没有账号来源"))
    repo = MemoryRepository(s.db)
    summary = repo.add_summary(ROUTE, "确定来源摘要", mid, mid, None)
    mixed = repo.add_summary(ROUTE, "不可归属范围", mid, unknown, summary.id)
    repo.set_contact_profile_field(ROUTE, "notes", "旧手工资料", {"source": "manual"})
    key = conversation_key("qq", "10001", ROUTE)
    with s.db.connect() as conn:
        eid = conn.execute('SELECT id FROM inbound_platform_messages').fetchone()[0]
    migrate_legacy_accounts(s.db, message_event_links={mid: eid})
    assert repo.latest_summary(key).id == summary.id
    assert repo.latest_summary(ROUTE).id == mixed.id
    assert s.db.list_stored_messages(key)[0].id == mid
    assert s.db.list_stored_messages(ROUTE)[0].id == unknown
    assert repo.get_contact_profile(key) is None
    migrate_legacy_accounts(s.db)
    with s.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
        assert conn.execute("SELECT start_message_id FROM conversation_summaries WHERE id=?", (summary.id,)).fetchone()[0] == mid
        assert conn.execute("SELECT status FROM legacy_assignment WHERE table_name='messages' AND record_id=?", (str(unknown),)).fetchone()[0] == "unassigned"


def test_confirming_login_never_deletes_ambiguous_legacy_self_route(tmp_path):
    class Client:
        async def call_action(self, action, params):
            if action == 'get_login_info': return {'user_id': '10001', 'nickname': '合成账号'}
            if action == 'get_friend_list': return [{'user_id': '10001'}, {'user_id': '20002'}]
            return []
    async def run():
        s = system(tmp_path / 'db.sqlite')
        route = 'qq:private:10001'
        old_id = s.db.save_message(route, Message('user', '来源账号未知，必须保留'))
        other_key = conversation_key('qq', '10002', route)
        s.db.save_message(other_key, Message('user', '另一账号的正常私聊'))
        runtime = DesktopRuntime(Settings())
        runtime._core = SimpleNamespace(database=s.db, merger=s.merger)
        runtime._onebot = Client()
        await runtime._load_directory()
        await runtime._load_history(route)
        assert s.db.list_stored_messages(route)[0].id == old_id
        assert s.db.list_stored_messages(other_key)[0].content == '另一账号的正常私聊'
        events = list(runtime.events.queue)
        directory = next(e.payload for e in events if e.kind == 'directory')
        assert [str(item['user_id']) for item in directory['friends']] == ['20002']
    asyncio.run(run())
