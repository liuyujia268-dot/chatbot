"""U04/U09/U10/U14: ACK, persistent simulation and recoverable failures."""
import asyncio
from types import SimpleNamespace

import pytest

from app.adapters.delivery import DeliveryService
from app.adapters.outbound import DryRunMessageSender
from app.core.identity import conversation_key
from app.core.platform import OutboundMessage
from app.models.base import ModelTimeoutError, ModelAuthenticationError, ModelNotFoundError, ModelNetworkConfigurationError
from app.ui.runtime import DesktopRuntime, _SwitchableSender
from app.config import Settings
from app.ui.repository import DesktopRepository
from tests.ux_support import FakeModel, FakeSender, event, system


ROUTE = 'qq:private:20002'
KEY = conversation_key('qq', '10001', ROUTE)


def test_generation_is_candidate_until_platform_ack(tmp_path):
    async def run():
        s = system(tmp_path / 'db.sqlite')
        s.sender.error = ValueError('合成失败 TOKEN_MUST_NOT_LEAK')
        await s.adapter.handle_event(event('a'))
        assert [m.role for m in s.db.list_messages(KEY)] == ['user']
        with s.db.connect() as conn:
            record = dict(conn.execute('SELECT * FROM outbound_deliveries').fetchone())
        assert record['status'] == 'failed' and 'TOKEN_MUST_NOT_LEAK' not in repr(record)
        s.sender.error = None
        delivery = s.adapter._delivery
        await delivery.send(record['id'], s.sender)
        await delivery.send(record['id'], s.sender)
        assert len(s.sender.calls) == 1
        assert [m.role for m in s.db.list_messages(KEY)] == ['user', 'assistant']
    asyncio.run(run())


def test_dry_run_is_persistent_not_real_assistant_history(tmp_path):
    async def run():
        s = system(tmp_path / 'db.sqlite')
        live = s.sender
        s.adapter._sender = DryRunMessageSender(live)
        s.merger.set_simulation_mode(True)
        await s.adapter.handle_event(event('a'))
        assert live.calls == []
        assert [m.role for m in s.db.list_messages(KEY)] == ['user']
        assert [m.role for m in s.db.list_messages(KEY, include_simulated=True)] == ['user', 'assistant']
        view = DesktopRepository(s.db)
        view.initialize()
        messages = view.list_messages(platform='qq', account_id='10001', conversation_id=ROUTE)
        assert messages[0].status == 'simulated'
        restarted = system(tmp_path / 'db.sqlite')
        await restarted.adapter.handle_event(event('b', '新消息'))
        assert not any(m.role == 'assistant' for m in restarted.model.calls[-1])
    asyncio.run(run())


def test_timeout_and_lost_ack_unknown_never_blindly_retry(tmp_path):
    async def run():
        s = system(tmp_path / 'db.sqlite')
        service = s.adapter._delivery
        mid = service.prepare(OutboundMessage('qq', '10001', ROUTE, '未确认正文'))
        sender = FakeSender()
        sender.error = TimeoutError()
        assert (await service.send(mid, sender))['status'] == 'unknown'
        sender.error = None
        await service.send(mid, sender)
        assert sender.calls == [] and s.db.list_messages(KEY) == []
    asyncio.run(run())


def test_partial_batch_retries_same_records_without_resending_success(tmp_path):
    async def run():
        s = system(tmp_path / 'db.sqlite', model=FakeModel('第一句。第二句。第三句。'))
        def fail_next(_):
            s.sender.error = ValueError('第二段失败')
        s.sender.on_send = fail_next
        await s.adapter.handle_event(event('a'))
        with s.db.connect() as conn:
            rows = [dict(r) for r in conn.execute('SELECT * FROM outbound_deliveries ORDER BY segment_index')]
        assert [r['status'] for r in rows] == ['sent', 'failed', 'candidate']
        assert [m.content for m in s.db.list_messages(KEY) if m.role == 'assistant'] == ['第一句。']
        s.sender.error = None
        s.sender.on_send = None
        for row in rows:
            await s.adapter._delivery.send(row['id'], s.sender)
        assert [m.text for m in s.sender.calls] == ['第一句。', '第二句。', '第三句。']
        assert len([m for m in s.db.list_messages(KEY) if m.role == 'assistant']) == 3
    asyncio.run(run())


def test_pending_on_restart_unknown_and_body_target_preserved(tmp_path):
    s = system(tmp_path / 'db.sqlite')
    service = s.adapter._delivery
    mid = service.prepare(OutboundMessage('qq', '10001', ROUTE, '应保留的正文'))
    service._state(mid, 'pending')
    restarted = DeliveryService(s.db)
    restarted.recover_pending()
    row = restarted.get(mid)
    assert row['status'] == 'unknown' and row['text'] == '应保留的正文' and row['conversation_id'] == ROUTE


@pytest.mark.parametrize('failure', [ModelTimeoutError, ModelAuthenticationError, ModelNotFoundError, ModelNetworkConfigurationError])
def test_model_failure_structured_local_event_and_other_session_survives(tmp_path, failure):
    class Model(FakeModel):
        async def generate(self, messages):
            if '触发错误' in repr(messages):
                raise failure('TEST_API_KEY_SECRET')
            return await super().generate(messages)
    async def run():
        events = []
        s = system(tmp_path / 'db.sqlite', model=Model(), event_callback=lambda *args: events.append(args))
        await s.adapter._handle_event_safely(event('a', '触发错误'))
        error = next(p for k, p in events if k == 'model_error')
        assert error['account_id'] == '10001' and error['conversation_id'] == ROUTE
        assert error['code'] == failure.__name__ and 'TEST_API_KEY_SECRET' not in repr(events)
        assert s.sender.calls == []
        await s.adapter._handle_event_safely(event('b', '其他联系人', sender='20003'))
        assert len(s.sender.calls) == 1
    asyncio.run(run())


def test_manual_send_target_fixed_and_failure_recovery_does_not_need_draft(tmp_path):
    async def run():
        s = system(tmp_path / 'db.sqlite')
        runtime = DesktopRuntime(Settings())
        runtime._account_id = '10001'
        runtime._core = SimpleNamespace(merger=s.merger, database=s.db)
        runtime._manual_sender = _SwitchableSender(s.sender, lambda *args: None, dry_run=False)
        s.sender.error = ValueError('失败')
        await runtime._send_manual(ROUTE, '旧正文', account_id='10001')
        record = runtime.events.get_nowait().payload
        # Use persisted record, not a mutable GUI draft.
        mid = record['id']
        assert runtime._delivery.get(mid)['status'] == 'failed'
        assert s.db.list_messages(KEY) == []
        s.sender.error = None
        await runtime._retry_delivery(mid, '10001')
        await runtime._retry_delivery(mid, '10001')
        assert [m.text for m in s.sender.calls] == ['旧正文']
        assert s.sender.calls[0].conversation_id == ROUTE
    asyncio.run(run())


def test_final_transport_guard_blocks_not_yet_submitted_proactive_after_disable(tmp_path):
    from app.core.send_guard import check_submission
    class LockedSender(FakeSender):
        def __init__(self):
            super().__init__()
            self.waiting = asyncio.Event()
            self.release = asyncio.Event()
        async def send(self, message):
            self.waiting.set()
            await self.release.wait()
            check_submission()  # same boundary as OneBot's send lock
            return await super().send(message)
    async def run():
        s = system(tmp_path / 'db.sqlite', proactive_enabled=True)
        await s.adapter.handle_event(event('a'))
        sender = LockedSender()
        s.adapter._sender = sender
        pending = asyncio.create_task(s.adapter.trigger_proactive_once(force=True))
        await asyncio.wait_for(sender.waiting.wait(), 1)
        s.adapter.set_proactive_enabled(False)
        sender.release.set()
        assert await pending is None
        assert sender.calls == []
        with s.db.connect() as conn:
            row = conn.execute("SELECT status FROM outbound_deliveries ORDER BY created_at DESC,id DESC").fetchall()
            assert sorted(r[0] for r in row) == ['candidate', 'sent']
    asyncio.run(run())


def test_self_echo_after_ack_does_not_double_history_or_memory(tmp_path):
    async def run():
        s = system(tmp_path / 'db.sqlite')
        await s.adapter.handle_event(event('a'))
        echo = event('fake-out-1', '合成回复', sender='10001')
        echo['post_type'] = 'message_sent'
        echo['target_id'] = '20002'
        await s.adapter.handle_event(echo)
        assert [m.role for m in s.db.list_messages(KEY)] == ['user', 'assistant']
        assert len(s.model.calls) == 1
    asyncio.run(run())


def test_proactive_model_failure_also_emits_safe_scoped_event(tmp_path):
    class Model(FakeModel):
        async def generate(self, messages):
            if self.calls:
                raise ModelAuthenticationError('TEST_PRIVATE_KEY_MUST_NOT_LEAK')
            return await super().generate(messages)
    async def run():
        events, logs = [], []
        s = system(tmp_path / 'db.sqlite', model=Model(), proactive_enabled=True,
                   event_callback=lambda *args: events.append(args))
        s.adapter._sink = logs.append
        await s.adapter.handle_event(event('a'))
        with pytest.raises(ModelAuthenticationError):
            await s.adapter.trigger_proactive_once(force=True)
        payload = next(p for k, p in events if k == 'model_error')
        assert payload['account_id'] == '10001' and payload['conversation_id'] == ROUTE
        assert 'MODEL_API_KEY' in payload['message']
        assert 'TEST_PRIVATE_KEY_MUST_NOT_LEAK' not in repr(events + logs)
        assert len(s.sender.calls) == 1
    asyncio.run(run())


def test_retired_runtime_cannot_submit_after_waiting_on_send_lock(tmp_path):
    from app.core.send_guard import check_submission
    class LockedSender(FakeSender):
        def __init__(self):
            super().__init__()
            self.waiting, self.release = asyncio.Event(), asyncio.Event()
        async def send(self, message):
            self.waiting.set()
            await self.release.wait()
            check_submission()
            return await super().send(message)
    async def run():
        s = system(tmp_path / 'db.sqlite')
        live = LockedSender()
        gate = _SwitchableSender(live, lambda *args: None, dry_run=False)
        retired = False
        gate.allowed = lambda _: not retired
        service = s.adapter._delivery
        mid = service.prepare(OutboundMessage('qq', '10001', ROUTE, '旧运行实例正文'))
        pending = asyncio.create_task(service.send(mid, gate))
        await asyncio.wait_for(live.waiting.wait(), 1)
        retired = True
        live.release.set()
        assert (await pending)['status'] == 'candidate'
        assert live.calls == [] and s.db.list_messages(KEY) == []
    asyncio.run(run())
