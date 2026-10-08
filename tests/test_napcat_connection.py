"""Offline regression coverage for account-dependent NapCat network settings."""
import asyncio
import hashlib
import json
from urllib.parse import urlsplit

import pytest

from app.adapters.websocket_client import OneBotWebSocketEvents
from app.ui.napcat_connection import (
    NapCatConnectionProbe, NapCatConnectionState, OneBotEndpoint, select_local_endpoint,
    NapCatSwitchError, restart_existing_napcat_for_switch,
)
from app.ui.napcat_webui import WebUiLaunch
from tests.test_account_switch import LoginClient, runtime_for
from tests.ux_support import system


SECRET = 'TEST_NAPCAT_SECRET_7F92A1'


def config(port=3002, token=SECRET, **changes):
    return {'network': {'websocketServers': [dict(enable=True, host='127.0.0.1',
                                                port=port, token=token, **changes)]}}


class FakeHttp:
    def __init__(self):
        self.calls = []
        self.account = '10002'
        self.ready = True
        self.settings = config()
        self.switch_during_config = False
        self.login_error = False

    def __call__(self, request, *, timeout):
        route = urlsplit(request.full_url).path
        self.calls.append((route, json.loads(request.data), request.headers, timeout))
        if route == '/api/auth/login':
            payload = {'code': -1, 'message': SECRET} if self.login_error else {
                'code': 0, 'data': {'Credential': 'FAKE_IN_MEMORY_CREDENTIAL'}}
        elif route == '/api/QQLogin/CheckLoginStatus':
            payload = {'code': 0, 'data': {'isLogin': self.ready}}
        elif route == '/api/QQLogin/GetQQLoginInfo':
            payload = {'code': 0, 'data': {'uin': self.account, 'nick': '合成账号'}}
        elif route == '/api/OB11Config/GetConfig':
            payload = {'code': 0, 'data': self.settings}
            if self.switch_during_config:
                self.account = '10003'
        elif route == '/api/QQLogin/RestartNapCat':
            payload = {'code': 0, 'data': {}}
        else:
            pytest.fail(f'Unexpected NapCat action: {route}')

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return json.dumps(payload).encode()

        return Response()


def probe(http, **kwargs):
    return NapCatConnectionProbe('http://127.0.0.1:6099/webui?token=SHOULD_NOT_LEAK',
                                token=SECRET, config_path=None,
                                preferred_ws_url='ws://127.0.0.1:3001', opener=http, **kwargs)


def test_probe_uses_current_account_endpoint_and_only_read_apis(monkeypatch):
    monkeypatch.setattr('app.ui.napcat_webui._default_roots', lambda: [])
    http = FakeHttp()
    result = probe(http).inspect()
    assert result.available and result.ready and result.account_id == '10002'
    assert result.endpoint.url == 'ws://127.0.0.1:3002' and result.endpoint.token == SECRET
    assert http.calls[0][1] == {'hash': hashlib.sha256((SECRET + '.napcat').encode()).hexdigest()}
    assert all(route in {'/api/auth/login', '/api/QQLogin/CheckLoginStatus',
                         '/api/QQLogin/GetQQLoginInfo', '/api/OB11Config/GetConfig'}
               for route, *rest in http.calls)
    assert all(timeout == 2 for *rest, timeout in http.calls)
    assert SECRET not in repr(result) and SECRET not in result.hint
    assert 'FAKE_IN_MEMORY_CREDENTIAL' not in repr(result)
    # Tokens from an authenticated launch URL never leak into API query strings.
    assert all('?' not in route for route, *rest in http.calls)


def test_probe_handles_missing_server_and_login_gap(monkeypatch):
    monkeypatch.setattr('app.ui.napcat_webui._default_roots', lambda: [])
    http = FakeHttp()
    reader = probe(http)
    http.ready = False
    result = reader.inspect()
    assert result.available and not result.ready and result.account_id is None
    assert [call[0] for call in http.calls] == ['/api/auth/login', '/api/QQLogin/CheckLoginStatus']
    http.ready = True
    http.settings = {'network': {'websocketServers': []}}
    result = reader.inspect()
    assert result.account_id == '10002' and not result.ready
    assert '启用 WebSocket 服务端' in result.hint
    assert sum(call[0] == '/api/auth/login' for call in http.calls) == 1


def test_probe_does_not_mix_account_identity_and_configuration(monkeypatch):
    monkeypatch.setattr('app.ui.napcat_webui._default_roots', lambda: [])
    http = FakeHttp()
    http.switch_during_config = True
    result = probe(http).inspect()
    assert result.available and not result.ready and result.endpoint is None
    assert result.account_id is None


def test_failed_webui_auth_is_throttled_and_does_not_echo_secret(monkeypatch):
    monkeypatch.setattr('app.ui.napcat_webui._default_roots', lambda: [])
    http = FakeHttp()
    http.login_error = True
    now = [0]
    reader = probe(http, clock=lambda: now[0])
    for _ in range(5):
        result = reader.inspect()
        assert not result.available and SECRET not in result.hint
    assert len(http.calls) == 1
    now[0] = 31
    http.login_error = False
    assert reader.inspect().ready


@pytest.mark.parametrize('error', [TimeoutError(SECRET), OSError(SECRET)])
def test_http_failure_is_bounded_and_sanitized(monkeypatch, error):
    monkeypatch.setattr('app.ui.napcat_webui._default_roots', lambda: [])
    def fail(request, *, timeout):
        assert timeout == 2
        raise error
    result = probe(fail).inspect()
    assert not result.available and SECRET not in repr(result)


def test_remote_connection_does_not_auto_discover_or_send_credentials():
    reader = NapCatConnectionProbe('http://remote.invalid:6099/webui', token=SECRET,
                                  preferred_ws_url='ws://remote.invalid:3001',
                                  opener=lambda *args, **kwargs: pytest.fail('no remote probe'))
    assert not reader.inspect().available


@pytest.mark.parametrize('host', ['localhost', '127.0.0.1', '0.0.0.0', '::', '::1'])
def test_endpoint_selection_only_uses_local_enabled_servers(host):
    settings = config()
    settings['network']['websocketServers'][0]['host'] = host
    result = select_local_endpoint(settings, 'ws://localhost:3001')
    assert urlsplit(result.url).port == 3002
    assert urlsplit(result.url).hostname in {'127.0.0.1', '::1'}


@pytest.mark.parametrize('changes', [{'enable': False}, {'host': 'remote.invalid'},
                                    {'port': True}, {'port': 0}, {'port': 65536}])
def test_invalid_or_disabled_endpoints_are_not_guessed(changes):
    settings = config()
    settings['network']['websocketServers'][0].update(changes)
    assert select_local_endpoint(settings, 'ws://localhost:3001') is None


def test_multiple_servers_require_unique_configured_port():
    settings = config()
    settings['network']['websocketServers'].append(config(3003)['network']['websocketServers'][0])
    assert select_local_endpoint(settings, 'ws://localhost:3001') is None
    assert select_local_endpoint(settings, 'ws://localhost:3003').url == 'ws://127.0.0.1:3003'


def test_http_server_with_websocket_is_supported():
    settings = config()
    settings['network']['httpServers'] = settings['network'].pop('websocketServers')
    assert select_local_endpoint(settings, 'ws://localhost:3001') is None
    settings['network']['httpServers'][0]['enableWebsocket'] = True
    assert select_local_endpoint(settings, 'ws://localhost:3001').url == 'ws://127.0.0.1:3002'


def test_runtime_retargets_existing_onebot_client_without_rebuilding_chat(tmp_path):
    async def scenario():
        s = system(tmp_path / 'test.db')
        runtime = runtime_for(s)
        assert await runtime._load_directory(0)
        # Keep the existing sender/Dispatcher/adapter graph. Only reconnect the
        # transport with B's current local port/token and confirm its identity.
        client = OneBotWebSocketEvents('ws://127.0.0.1:3001', token='OLD_TOKEN',
                                       status_callback=runtime._on_status)
        runtime._onebot = client
        old_adapter = runtime._qq
        runtime._webui_account_id = '10001'
        runtime._apply_connection_snapshot(NapCatConnectionState(
            True, True, '10002', OneBotEndpoint('ws://127.0.0.1:3002', SECRET)))
        assert runtime._qq is old_adapter and runtime.account_id is None
        assert client.url == 'ws://127.0.0.1:3002' and client.token == SECRET
        assert not runtime.connected and old_adapter._account_id is None
        login = LoginClient()
        login.account = '10002'
        client.call_action = login.call_action
        runtime._connection_state = 'connected'
        assert await runtime._load_directory(runtime._connection_epoch)
        assert runtime.connected and runtime.account_id == '10002'
        assert s.model.calls == [] and s.sender.calls == []
        assert SECRET not in repr(list(runtime.events.queue))
    asyncio.run(scenario())


def test_webui_logout_blocks_old_onebot_account_and_sends(tmp_path):
    async def scenario():
        s = system(tmp_path / 'test.db')
        runtime = runtime_for(s)
        assert await runtime._load_directory(0)
        client = OneBotWebSocketEvents('ws://127.0.0.1:3001', status_callback=runtime._on_status)
        runtime._onebot = client
        client.call_action = LoginClient().call_action  # stale socket reports A
        runtime._apply_connection_snapshot(NapCatConnectionState(True, hint='QQ 正在切换'))
        assert not client._enabled and not runtime.connected
        runtime._connection_state = 'connected'
        assert not await runtime._load_directory(runtime._connection_epoch)
        assert runtime.account_id is None and s.model.calls == [] and s.sender.calls == []
    asyncio.run(scenario())


def test_delayed_probe_cannot_modify_retired_runtime(tmp_path):
    async def scenario():
        runtime = runtime_for(system(tmp_path / 'test.db'))
        client = OneBotWebSocketEvents('ws://127.0.0.1:3001', token='UNCHANGED')
        runtime._onebot = client
        runtime.request_stop()
        runtime._apply_connection_snapshot(NapCatConnectionState(
            True, True, '10002', OneBotEndpoint('ws://127.0.0.1:3002', SECRET)))
        assert client.url == 'ws://127.0.0.1:3001' and client.token == 'UNCHANGED'
        assert runtime.events.empty()
    asyncio.run(scenario())


def test_connection_watcher_operates_while_onebot_is_down(tmp_path):
    async def scenario():
        runtime = runtime_for(system(tmp_path / 'test.db'))
        runtime._shutdown = asyncio.Event()
        runtime._connection_state = 'error'
        states = []
        state = NapCatConnectionState(True, True, '10002', OneBotEndpoint('ws://localhost:3002', SECRET))

        class Probe:
            def inspect(self): return state

        def apply(result):
            states.append(result)
            runtime._shutdown.set()

        runtime._connection_probe = Probe()
        runtime._apply_connection_snapshot = apply
        await asyncio.wait_for(runtime._watch_connection_config(), 2)
        assert states == [state]
    asyncio.run(scenario())


def test_unsupported_webui_does_not_break_healthy_explicit_onebot(tmp_path):
    async def scenario():
        runtime = runtime_for(system(tmp_path / 'test.db'))
        assert await runtime._load_directory(0)
        runtime._apply_connection_snapshot(NapCatConnectionState(False, hint='不支持的 WebUI'))
        assert runtime.connected and runtime.account_id == '10001'
    asyncio.run(scenario())


def test_explicit_switch_can_restart_existing_service_without_config_writes(monkeypatch):
    monkeypatch.setattr('app.ui.napcat_webui._default_roots', lambda: [])
    http = FakeHttp()
    launch = WebUiLaunch(f'http://localhost:6099/webui?token={SECRET}', True, 'fake')
    restart_existing_napcat_for_switch(launch, opener=http)
    assert [call[0] for call in http.calls] == ['/api/auth/login', '/api/QQLogin/RestartNapCat']
    assert all(call[1] == {} for call in http.calls[1:])


def test_restart_failure_is_sanitized_and_remote_restart_is_rejected(monkeypatch):
    monkeypatch.setattr('app.ui.napcat_webui._default_roots', lambda: [])
    http = FakeHttp()
    http.login_error = True
    launch = WebUiLaunch(f'http://localhost:6099/webui?token={SECRET}', True, 'fake')
    with pytest.raises(NapCatSwitchError) as caught:
        restart_existing_napcat_for_switch(launch, opener=http)
    assert SECRET not in str(caught.value) and '第二实例' in str(caught.value)
    assert [call[0] for call in http.calls] == ['/api/auth/login']
    http.calls.clear()
    with pytest.raises(NapCatSwitchError):
        restart_existing_napcat_for_switch(WebUiLaunch('http://remote.invalid:6099', False, 'fake'), opener=http)
    assert http.calls == []
