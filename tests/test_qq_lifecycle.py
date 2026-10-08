"""Offline lifecycle regressions; no NapCat, QQ process control or live data."""
import asyncio
from pathlib import Path
import queue
import subprocess
import threading
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.ui.napcat_webui import wait_for_webui
from app.ui.qq_shutdown import QQShutdownError, QQShutdownResult, shutdown_qq_session, terminate_all_qq
from app.ui.runtime import DesktopRuntime, _SwitchableSender
from app.core.platform import OutboundMessage
from app.core.send_guard import OutboundNotSubmittedError


def events(runtime):
    result = []
    while True:
        try:
            result.append(runtime.events.get_nowait())
        except queue.Empty:
            return result


def directory_runtime(client):
    runtime = DesktopRuntime(Settings())
    runtime._onebot = client
    runtime._core = SimpleNamespace()  # directory-only; never build real core/DB
    runtime._takeovers_for = lambda account_id: ()
    runtime._directory_retry_seconds = 0.001
    return runtime


def test_directory_recovers_without_another_websocket_connection(caplog):
    async def scenario():
        class Client:
            attempts = 0

            async def call_action(self, action, params):
                if action == 'get_login_info':
                    self.attempts += 1
                    if self.attempts == 1:
                        raise ConnectionError('FAKE_SECRET_DO_NOT_DISPLAY')
                    return {'user_id': 10001, 'nickname': '合成账号'}
                return []

        client = Client()
        runtime = directory_runtime(client)
        runtime._on_status('connected', None)
        await asyncio.wait_for(runtime._directory_task, timeout=1)
        delivered = events(runtime)
        assert client.attempts == 2
        assert [event.kind for event in delivered] == ['status', 'directory_error', 'login', 'directory']
        assert runtime.connected
        assert 'FAKE_SECRET_DO_NOT_DISPLAY' not in repr(delivered) + caplog.text
        assert '正在重试' in delivered[1].payload

    asyncio.run(scenario())


def test_disconnect_cancels_directory_retry_and_retired_runtime_cannot_restart():
    async def scenario():
        started = asyncio.Event()

        class Client:
            async def call_action(self, action, params):
                started.set()
                await asyncio.Event().wait()

        runtime = directory_runtime(Client())
        runtime._on_status('connected', None)
        task = runtime._directory_task
        await asyncio.wait_for(started.wait(), timeout=1)
        runtime._on_status('disconnected', None)
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled() and runtime._directory_task is None
        assert not runtime.connected
        runtime.request_stop()
        runtime._on_status('connected', None)
        assert runtime._directory_task is None
        assert [event.kind for event in events(runtime)] == ['status', 'status']
        assert not runtime.send_message('qq:private:20002', '合成草稿')

    asyncio.run(scenario())


def test_directory_failure_from_old_connection_is_discarded():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Client:
            async def call_action(self, action, params):
                entered.set()
                await release.wait()
                raise ConnectionError('stale failure')

        runtime = directory_runtime(Client())
        task = asyncio.create_task(runtime._load_directory(0))
        await entered.wait()
        runtime._connection_epoch = 1
        release.set()
        assert not await task
        assert events(runtime) == []

    asyncio.run(scenario())


def test_exit_requested_before_worker_start_never_builds_core(monkeypatch):
    def forbidden(*args):
        pytest.fail('retired runtime must not initialize DB/model/network')

    monkeypatch.setattr('app.ui.runtime.build_core_runtime', forbidden)
    runtime = DesktopRuntime(Settings())
    runtime.request_stop()
    asyncio.run(runtime._async_main())
    assert runtime.stop(timeout_seconds=0)


def test_no_platform_send_until_relogin_confirmed_or_after_exit_requested():
    async def scenario():
        release = asyncio.Event()
        calls = []

        class Client:
            async def call_action(self, action, params):
                if action == 'get_login_info':
                    await release.wait()
                    return {'user_id': '10002', 'nickname': '合成新账号'}
                return []

        class Sender:
            async def send(self, message):
                calls.append(message)
                return 'fake-ack'

        runtime = directory_runtime(Client())
        runtime._account_id = '10001'
        runtime._connection_state = 'connected'
        runtime._login_confirmed_epoch = 0
        assert runtime.connected
        sender = _SwitchableSender(Sender(), lambda *args: None, dry_run=False)
        sender.allowed = runtime._outbound_allowed
        old = OutboundMessage('qq', '10001', 'qq:private:20002', '旧账号在途候选')
        runtime._on_status('disconnected', None)
        runtime._on_status('connected', None)
        assert not runtime.connected  # WebSocket != confirmed QQ login
        with pytest.raises(OutboundNotSubmittedError):
            await sender.send(old)
        release.set()
        await asyncio.wait_for(runtime._directory_task, timeout=1)
        assert runtime.connected and runtime.account_id == '10002'
        with pytest.raises(OutboundNotSubmittedError):
            await sender.send(old)
        runtime.request_stop()
        new = OutboundMessage('qq', '10002', 'qq:private:20002', '退出后禁止发送')
        with pytest.raises(OutboundNotSubmittedError):
            await sender.send(new)
        assert calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize('user_id', [None, '', 0, 'invalid'])
def test_unlogged_account_info_does_not_enable_sending(user_id):
    class Client:
        async def call_action(self, action, params):
            assert action == 'get_login_info'
            return {'user_id': user_id}

    runtime = directory_runtime(Client())
    assert not asyncio.run(runtime._load_directory(0))
    assert runtime.account_id is None and not runtime.connected
    assert [event.kind for event in events(runtime)] == ['directory_error']


def test_webui_wait_cancellation_stops_probing(monkeypatch):
    cancel = threading.Event()
    probes = []

    def ready(url):
        probes.append(url)
        cancel.set()
        return False

    monkeypatch.setattr('app.ui.napcat_webui.webui_is_ready', ready)
    assert not wait_for_webui('http://fake.invalid', interval_seconds=10, cancel_event=cancel)
    assert len(probes) == 1
    assert not wait_for_webui('http://fake.invalid', cancel_event=cancel)
    assert len(probes) == 1


class FakeProcesses:
    def __init__(self, mode='graceful'):
        self.mode = mode
        self.images = {101: 'QQ.exe', 102: 'QQ.exe', 103: 'QQCrashReporter.exe',
                       404: 'QQMusic.exe', 999: 'node.exe'}
        self.calls = []
        self.now = 0.0

    def run(self, args, **kwargs):
        self.calls.append(args)
        assert kwargs['timeout'] == 5 and kwargs.get('shell') is not True
        tool = Path(args[0]).name
        if tool == 'tasklist.exe':
            assert args[1] == '/FI' and args[3:] == ['/FO', 'CSV', '/NH']
            name = args[2].removeprefix('IMAGENAME eq ')
            output = '\n'.join(f'"{image}","{pid}","Console","1","100 K"'
                               for pid, image in self.images.items() if image == name)
            return SimpleNamespace(returncode=0, stdout=output)
        assert tool == 'taskkill.exe' and args[1] == '/PID' and args[3] == '/T'
        pid = int(args[2])
        assert pid in {101, 102, 103}, 'must never kill unrelated processes'
        if self.mode == 'denied' and pid == 102:
            return SimpleNamespace(returncode=1, stdout='ignored error text')
        if self.mode == 'force' and pid == 102 and '/F' not in args:
            return SimpleNamespace(returncode=1, stdout='still running')
        self.images.pop(pid, None)
        return SimpleNamespace(returncode=0, stdout='')

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def cleanup(self):
        return terminate_all_qq(runner=self.run, grace_seconds=0.4, force_seconds=0.4,
                                clock=self.clock, sleep=self.sleep)


@pytest.mark.parametrize('mode', ['graceful', 'force', 'denied'])
def test_shutdown_all_qq_uses_verified_pids_and_reports_remaining(mode):
    process = FakeProcesses(mode)
    result = process.cleanup()
    assert result.initial_count == 3
    assert result.forced == (mode != 'graceful')
    assert result.success == (mode != 'denied')
    assert result.remaining_pids == ((102,) if mode == 'denied' else ())
    forced = [call for call in process.calls if '/F' in call]
    assert [call[2] for call in forced] == ([] if mode == 'graceful' else ['102'])
    assert process.images[404] == 'QQMusic.exe' and process.images[999] == 'node.exe'


def test_shutdown_with_no_qq_does_not_issue_taskkill():
    process = FakeProcesses()
    process.images = {999: 'node.exe'}
    assert process.cleanup() == QQShutdownResult(0)
    assert all(Path(call[0]).name == 'tasklist.exe' for call in process.calls)


@pytest.mark.parametrize('failure', ['denied', 'malformed', 'timeout'])
def test_unverifiable_process_inventory_aborts_before_process_control(failure):
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        assert Path(args[0]).name == 'tasklist.exe'
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args, 5)
        return SimpleNamespace(returncode=int(failure == 'denied'), stdout='"QQ.exe","invalid"')

    with pytest.raises(QQShutdownError):
        terminate_all_qq(runner=runner)
    assert len(calls) == 1


def test_shutdown_stops_runtime_before_process_cleanup():
    calls = []
    runtime = SimpleNamespace(running=True)

    def stop(*, timeout_seconds):
        calls.append(('stop', timeout_seconds))
        runtime.running = False

    def cleanup(**kwargs):
        assert not runtime.running
        calls.append(('cleanup', kwargs))
        return QQShutdownResult(3)

    runtime.stop = stop
    result = shutdown_qq_session(runtime, cleanup=cleanup)
    assert result.success and [call[0] for call in calls] == ['stop', 'cleanup']


def test_runtime_shutdown_timeout_does_not_kill_qq():
    runtime = SimpleNamespace(running=True, stop=lambda **kwargs: False)

    def forbidden(**kwargs):
        pytest.fail('QQ must not be killed while chat runtime is still running')

    with pytest.raises(QQShutdownError, match='未执行 QQ 清理'):
        shutdown_qq_session(runtime, cleanup=forbidden)


def test_only_owned_launcher_is_stopped_before_qq_cleanup():
    process = FakeProcesses()
    calls = []

    class Launcher:
        pid = 888

        def poll(self):
            return None

        def wait(self, *, timeout):
            calls.append(('wait', timeout))
            return 0

    def runner(args, **kwargs):
        if Path(args[0]).name == 'taskkill.exe' and args[2] == '888':
            calls.append(('owned-stop', args))
            return SimpleNamespace(returncode=0, stdout='')
        return process.run(args, **kwargs)

    result = terminate_all_qq(runner=runner, owned_launcher=Launcher(),
                              clock=process.clock, sleep=process.sleep)
    assert result.success and [call[0] for call in calls] == ['owned-stop', 'wait']
    assert 999 in process.images  # unrelated node process is not the launcher
