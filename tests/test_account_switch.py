"""Account A -> login gap -> account B, using only synthetic OneBot events."""
import asyncio
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.core.identity import conversation_key
from app.core.platform import OutboundMessage
from app.core.send_guard import OutboundNotSubmittedError
from app.memory.repository import MemoryRepository
from app.ui.runtime import DesktopRuntime, _SwitchableSender
from tests.ux_support import FakeModel, FakeSender, event, system


class LoginClient:
    def __init__(self):
        self.account = '10001'
        self.calls = []
        self.changed_directory = asyncio.Event()

    async def call_action(self, action, params):
        self.calls.append((action, self.account))
        if action == 'get_login_info':
            return {'user_id': self.account, 'nickname': f'合成账号{self.account}'}
        if action == 'get_group_list' and self.account == '10002':
            self.changed_directory.set()
        return []


def runtime_for(s):
    runtime = DesktopRuntime(Settings())
    runtime._core = SimpleNamespace(database=s.db, merger=s.merger)
    runtime._qq = s.adapter
    runtime._onebot = LoginClient()
    runtime._connection_state = 'connected'
    return runtime


def test_account_watch_detects_switch_without_websocket_reconnect(tmp_path):
    async def scenario():
        s = system(tmp_path / 'test.db')
        runtime = runtime_for(s)
        runtime._account_check_seconds = 0.001
        assert await runtime._load_directory(0)
        runtime._onebot.account = '10002'
        await asyncio.wait_for(runtime._onebot.changed_directory.wait(), 1)
        assert runtime.account_id == '10002' and runtime.connected
        assert runtime._connection_epoch == 0  # no WebSocket reconnect was required
        assert s.adapter._account_id == '10002'
        assert [call for call in runtime._onebot.calls if call[0] == 'get_friend_list'] == [
            ('get_friend_list', '10001'), ('get_friend_list', '10002')]
        assert s.model.calls == [] and s.sender.calls == []

    asyncio.run(scenario())


def test_first_new_account_message_is_held_then_replayed_once_with_isolated_context(tmp_path):
    async def scenario():
        s = system(tmp_path / 'test.db')
        runtime = runtime_for(s)
        assert await runtime._load_directory(0)
        message_a = event('a', 'ONLY_A_CHAT_CONTEXT_MARKER')
        assert runtime._on_raw_event(message_a) is not False
        await s.adapter.handle_event(message_a)
        repo = MemoryRepository(s.db)
        a_key = conversation_key('qq', '10001', 'qq:private:20002')
        mid = s.db.list_stored_messages(a_key)[0].id
        repo.add_summary(a_key, 'ONLY_A_SUMMARY_MARKER', mid, mid, None)
        repo.create_memory(conversation_id=a_key, memory_type='fact', memory_key='a.fact',
            content='ONLY_A_MEMORY_MARKER', source_start_message_id=mid,
            source_end_message_id=mid, confidence=1)
        repo.set_contact_profile_field(a_key, 'notes', 'ONLY_A_CONTACT_MARKER', {'source': 'manual'})
        repo.set_account_profile_field('public_information', 'ONLY_A_ACCOUNT_MARKER',
                                      {'source': 'manual'}, account_key='qq:10001')
        runtime._onebot.account = '10002'
        message_b = event('b', '新账号的第一条消息', account='10002')
        assert runtime._on_raw_event(message_b) is False
        assert runtime._on_raw_event(message_b) is False
        assert not runtime.connected and runtime.account_id is None
        assert len(s.model.calls) == 1  # no model call during unconfirmed login
        assert await runtime._check_account_once(0)
        await asyncio.gather(*tuple(runtime._replay_tasks))
        assert runtime.account_id == '10002' and s.adapter._account_id == '10002'
        assert len(s.model.calls) == 2 and len(s.sender.calls) == 2
        request = repr(s.model.calls[-1])
        assert '新账号的第一条消息' in request
        for marker in ('ONLY_A_CHAT_CONTEXT_MARKER', 'ONLY_A_SUMMARY_MARKER', 'ONLY_A_MEMORY_MARKER',
                       'ONLY_A_CONTACT_MARKER', 'ONLY_A_ACCOUNT_MARKER'):
            assert marker not in request
        assert len(runtime._pending_account_events) == 0
        b_key = conversation_key('qq', '10002', 'qq:private:20002')
        assert len([m for m in s.db.list_stored_messages(b_key) if m.role == 'user']) == 1

    asyncio.run(scenario())


def test_login_gap_invalidates_old_generation_and_does_not_revive_it_after_login(tmp_path):
    async def scenario():
        model = FakeModel()
        s = system(tmp_path / 'test.db', model=model, proactive_enabled=True)
        runtime = runtime_for(s)
        assert await runtime._load_directory(0)
        model.block = True
        old = asyncio.create_task(s.adapter.handle_event(event('old')))
        await asyncio.wait_for(model.started.wait(), 1)
        runtime._onebot.account = 0  # WebSocket alive, QQ logged out
        assert not await runtime._check_account_once(0)
        assert runtime.account_id is None and s.adapter._account_id is None
        assert s.adapter._proactive_targets == {} and not runtime.connected
        model.release.set()
        await old
        assert s.sender.calls == []
        runtime._onebot.account = '10002'
        assert await runtime._check_account_once(0)
        assert runtime.connected
        assert s.sender.calls == []  # the old candidate was not resurrected
        model.block = False
        current = event('new', account='10002')
        assert runtime._on_raw_event(current) is not False
        await s.adapter.handle_event(current)
        assert len(s.sender.calls) == 1 and s.sender.calls[0].account_id == '10002'

    asyncio.run(scenario())


def test_meta_events_cannot_activate_an_unconfirmed_account(tmp_path):
    async def scenario():
        s = system(tmp_path / 'test.db', proactive_enabled=True)
        runtime = runtime_for(s)
        assert await runtime._load_directory(0)
        assert runtime._on_raw_event({'post_type': 'meta_event', 'meta_event_type': 'heartbeat', 'self_id': '10002'}) is False
        assert not runtime.connected and runtime.account_id is None
        assert s.adapter._account_id is None and s.adapter._proactive_targets == {}
        assert len(runtime._pending_account_events) == 0
        assert s.model.calls == []

    asyncio.run(scenario())


def test_manual_and_ai_senders_remain_blocked_while_login_is_unknown(tmp_path):
    async def scenario():
        s = system(tmp_path / 'test.db')
        runtime = runtime_for(s)
        assert await runtime._load_directory(0)
        live = FakeSender()
        senders = [_SwitchableSender(live, lambda *args: None, dry_run=False) for _ in range(2)]
        for sender in senders:
            sender.allowed = runtime._outbound_allowed
        runtime._onebot.account = 0
        assert not await runtime._check_account_once(0)
        message = OutboundMessage('qq', '10001', 'qq:private:20002', '旧账号正文')
        for sender in senders:
            with pytest.raises(OutboundNotSubmittedError):
                await sender.send(message)
        runtime._onebot.account = '10002'
        assert await runtime._check_account_once(0)
        for sender in senders:
            with pytest.raises(OutboundNotSubmittedError):
                await sender.send(message)
        assert live.calls == []

    asyncio.run(scenario())


def test_takeover_survives_switch_away_and_back_without_affecting_other_account(tmp_path):
    async def scenario():
        s = system(tmp_path / 'test.db')
        runtime = runtime_for(s)
        route = 'qq:private:20002'
        key_a, key_b = (conversation_key('qq', account, route) for account in ('10001', '10002'))
        s.merger.set_human_takeover(key_a, True)
        assert await runtime._load_directory(0)
        runtime._onebot.account = '10002'
        assert await runtime._check_account_once(0)
        assert not s.merger.is_human_takeover(key_b)
        await s.adapter.handle_event(event('b', account='10002'))
        runtime._onebot.account = '10001'
        assert await runtime._check_account_once(0)
        assert s.merger.is_human_takeover(key_a)
        await s.adapter.handle_event(event('a'))
        assert len(s.model.calls) == 1
        logins = [item.payload for item in list(runtime.events.queue) if item.kind == 'login']
        assert [item['account_id'] for item in logins] == ['10001', '10002', '10001']
        assert logins[-1]['takeovers'] == (route,)

    asyncio.run(scenario())


def test_unconfirmed_event_for_a_different_account_is_never_replayed(tmp_path):
    async def scenario():
        s = system(tmp_path / 'test.db')
        runtime = runtime_for(s)
        assert await runtime._load_directory(0)
        assert runtime._on_raw_event(event('delayed', 'OLD_EVENT_NOT_FOR_B', account='10002')) is False
        # get_login_info contradicts the event; the event cannot choose the account.
        assert await runtime._check_account_once(0)
        await asyncio.gather(*tuple(runtime._replay_tasks))
        assert runtime.account_id == '10001'
        assert s.model.calls == [] and s.sender.calls == []
        assert len(runtime._pending_account_events) == 0

    asyncio.run(scenario())


def test_account_watch_stops_on_disconnect_or_exit(tmp_path):
    async def scenario():
        s = system(tmp_path / 'test.db')
        runtime = runtime_for(s)
        assert await runtime._load_directory(0)
        task = runtime._account_watch_task
        assert task is not None
        runtime._on_status('disconnected', None)
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled() and runtime._account_watch_task is None
        assert not runtime.connected and s.adapter._account_id is None
        runtime.request_stop()
        count = len(runtime._onebot.calls)
        assert not await runtime._check_account_once(runtime._connection_epoch)
        assert len(runtime._onebot.calls) == count

    asyncio.run(scenario())


def test_switch_rejects_old_account_quick_login_until_different_account_confirmed(tmp_path):
    async def scenario():
        s = system(tmp_path / 'test.db')
        runtime = runtime_for(s)
        runtime.require_account_change('10001')
        assert runtime._on_raw_event(event('quick-login-old')) is False
        assert not await runtime._load_directory(0)
        assert not runtime.connected and runtime.account_id is None
        assert s.model.calls == [] and s.sender.calls == []
        assert [e.kind for e in list(runtime.events.queue)] == ['directory_error']
        assert runtime._switch_away_from_account == '10001'
        runtime._onebot.account = '10002'
        assert await runtime._check_account_once(0)
        await asyncio.gather(*tuple(runtime._replay_tasks))
        assert runtime.connected and runtime.last_confirmed_account_id == '10002'
        assert runtime._switch_away_from_account is None
        assert s.model.calls == []  # cached old-account event was not replayed into B

    asyncio.run(scenario())


def test_invalid_login_check_is_single_request_and_suspends_account(tmp_path):
    async def scenario():
        s = system(tmp_path / 'test.db')
        runtime = runtime_for(s)
        assert await runtime._load_directory(0)
        calls = []

        async def invalid(action, params):
            calls.append(action)
            return None

        runtime._onebot.call_action = invalid
        assert not await runtime._check_account_once(0)
        assert calls == ['get_login_info']
        assert runtime.account_id is None and not runtime.connected
        assert s.adapter._proactive_targets == {} and s.model.calls == []

    asyncio.run(scenario())


def test_directory_failure_after_account_change_is_retried_not_mistaken_for_success(tmp_path):
    async def scenario():
        s = system(tmp_path / 'test.db')
        runtime = runtime_for(s)
        assert await runtime._load_directory(0)
        client = runtime._onebot
        original = client.call_action
        attempts = []

        async def flaky(action, params):
            if action == 'get_friend_list' and client.account == '10002':
                attempts.append(action)
                if len(attempts) == 1:
                    raise RuntimeError('directory not ready')
            return await original(action, params)

        client.call_action = flaky
        client.account = '10002'
        assert not await runtime._check_account_once(0)
        assert runtime.account_id == '10002' and runtime._directory_account is None
        assert await runtime._check_account_once(0)
        assert runtime._directory_account == (0, '10002') and len(attempts) == 2
        assert s.model.calls == [] and s.sender.calls == []

    asyncio.run(scenario())
