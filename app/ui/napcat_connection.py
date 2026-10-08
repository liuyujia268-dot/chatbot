"""Read-only NapCat account/network discovery, independent of OneBot availability.

Uses the official WebUI auth/login, QQLogin and OB11Config read endpoints.
Never reads messages or calls login/logout/restart/config-write/QQ send actions.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from app.ui.napcat_webui import is_local_webui, resolve_webui_launch


@dataclass(frozen=True, slots=True)
class OneBotEndpoint:
    url: str = field(repr=False)
    token: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class NapCatConnectionState:
    available: bool
    ready: bool = False
    account_id: str | None = None
    endpoint: OneBotEndpoint | None = None
    hint: str = ''


class _ProbeError(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # never forward WebUI credentials to another origin


def select_local_endpoint(config: dict, preferred_url: str) -> OneBotEndpoint | None:
    """Use a configured local server, not a guessed/disabled/reverse WS endpoint."""
    network = config.get('network')
    if not isinstance(network, dict):
        return None
    endpoints = []
    for key in ('websocketServers', 'httpServers'):
        entries = network.get(key, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or entry.get('enable') is not True:
                continue
            if key == 'httpServers' and entry.get('enableWebsocket') is not True:
                continue
            host = str(entry.get('host') or '127.0.0.1').strip().casefold()
            if host not in {'localhost', '127.0.0.1', '0.0.0.0', '::', '::1'}:
                continue
            port = entry.get('port')
            if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
                continue
            host = '[::1]' if host in {'::', '::1'} else '127.0.0.1'
            endpoints.append(OneBotEndpoint(f'ws://{host}:{port}', str(entry.get('token') or '') or None))
    preferred = urlsplit(preferred_url)
    matches = [item for item in endpoints if urlsplit(item.url).port == preferred.port]
    if len(matches) == 1:
        return matches[0]
    return endpoints[0] if len(endpoints) == 1 else None


class NapCatConnectionProbe:
    """Bounded HTTP requests, credentials held in memory only; call off the UI loop."""

    def __init__(self, base_url: str, *, token: str | None = None,
                 config_path: Path | None = None, preferred_ws_url: str,
                 opener=None, timeout_seconds: float = 2.0, clock=time.monotonic):
        self._base_url = base_url
        self._token = token
        self._config_path = config_path
        self._preferred_ws_url = preferred_ws_url
        self._opener = opener or build_opener(ProxyHandler({}), _NoRedirect()).open
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._credential = None
        self._credential_time = 0.0
        self._retry_after = 0.0

    def _post(self, route: str, body: dict | None = None):
        # Deliberately only these read/auth routes. Do not add SetConfig or send.
        allowed = {'/auth/login', '/QQLogin/CheckLoginStatus',
                   '/QQLogin/GetQQLoginInfo', '/OB11Config/GetConfig'}
        if route not in allowed:
            raise _ProbeError('禁止调用非只读 NapCat 接口')
        parsed = urlsplit(self._base_url)
        request = Request(urlunsplit((parsed.scheme, parsed.netloc, '/api' + route, '', '')),
                          data=json.dumps(body or {}).encode('utf-8'), method='POST',
                          headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
        if route != '/auth/login':
            request.add_header('Authorization', f'Bearer {self._credential}')
        with self._opener(request, timeout=self._timeout_seconds) as response:
            raw = response.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise _ProbeError('WebUI 配置响应过大，请检查 NapCat')
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise _ProbeError('WebUI 接口格式不支持，请检查 NapCat 版本') from None
        if not isinstance(payload, dict) or payload.get('code') != 0:
            # Never echo a raw server response: it may contain sensitive values.
            self._credential = None
            raise _ProbeError('WebUI 读取失败：检查 QQ_WEBUI_TOKEN、登录状态或 NapCat 版本')
        return payload.get('data')

    def _authenticate(self) -> None:
        if self._credential and self._clock() - self._credential_time < 3000:
            return
        if self._clock() < self._retry_after:
            raise _ProbeError('WebUI 鉴权暂不可用：检查 QQ_WEBUI_TOKEN（不是 QQ_WS_TOKEN）')
        launch = resolve_webui_launch(self._base_url, explicit_token=self._token,
                                      explicit_config_path=self._config_path,
                                      discover_config=False)
        self._config_path = launch.config_path or self._config_path
        token = next((value for key, value in parse_qsl(urlsplit(launch.url).query)
                      if key.casefold() == 'token'), '')
        if not token:
            raise _ProbeError('无法检查换号配置：设置 QQ_WEBUI_TOKEN 或 webui.json 路径')
        self._retry_after = self._clock() + 30  # avoid hammering auth/rate limiting
        data = self._post('/auth/login', {'hash': hashlib.sha256((token + '.napcat').encode()).hexdigest()})
        if not isinstance(data, dict) or not isinstance(data.get('Credential'), str) or not data['Credential']:
            raise _ProbeError('WebUI 鉴权失败或需要二次验证：请检查 WebUI 登录设置')
        self._credential = data['Credential']
        self._credential_time = self._clock()
        self._retry_after = 0

    def inspect(self) -> NapCatConnectionState:
        if not is_local_webui(self._base_url) or not is_local_webui(self._preferred_ws_url):
            return NapCatConnectionState(False, hint='远程 OneBot 使用设置中的 QQ_WS_URL / QQ_WS_TOKEN')
        try:
            self._authenticate()
            status = self._post('/QQLogin/CheckLoginStatus')
            if not isinstance(status, dict) or not isinstance(status.get('isLogin'), bool):
                raise _ProbeError('WebUI 登录状态格式不支持，请检查 NapCat 版本')
            if not status['isLogin']:
                return NapCatConnectionState(True, hint='QQ 尚未登录/正在切换账号，等待 NapCat 登录完成')
            info = self._post('/QQLogin/GetQQLoginInfo')
            account = str(info.get('uin') or info.get('user_id') or '').strip() if isinstance(info, dict) else ''
            if not account.isdecimal() or int(account) <= 0:
                raise _ProbeError('QQ 账号尚未就绪，等待 NapCat 登录完成')
            config = self._post('/OB11Config/GetConfig')
            endpoint = select_local_endpoint(config if isinstance(config, dict) else {}, self._preferred_ws_url)
            # A manual switch during these HTTP reads must not combine A's
            # identity with B's token/config. OneBot will confirm identity too.
            again = self._post('/QQLogin/GetQQLoginInfo')
            again_account = str(again.get('uin') or again.get('user_id') or '').strip() if isinstance(again, dict) else ''
            if again_account != account:
                return NapCatConnectionState(True, hint='正在切换 QQ 账号，等待配置稳定后重新连接')
            if endpoint is None:
                return NapCatConnectionState(True, account_id=account, hint=(
                    'QQ 已登录，但没有唯一可用的本地 OneBot WebSocket 服务。'
                    '请在该账号的 NapCat「网络配置」启用 WebSocket 服务端；'
                    '多个服务时让 QQ_WS_URL 指向其中一个端口，然后保存。'))
            return NapCatConnectionState(True, True, account, endpoint)
        except _ProbeError as exc:
            return NapCatConnectionState(False, hint=str(exc))
        except (OSError, URLError, HTTPError, ValueError):
            self._credential = None
            self._retry_after = 0  # service startup is not an authentication failure
            return NapCatConnectionState(False, hint='WebUI 尚未就绪或接口无法访问；检查 QQ_WEBUI_URL，后台将继续重试')


class NapCatSwitchError(RuntimeError):
    """Safe user-facing failure for an explicitly requested account switch."""


def restart_existing_napcat_for_switch(launch, *, opener=None) -> None:
    """ONLY after confirmed QQ cleanup/user switch: reuse the existing WebUI.

    This deliberately lives outside the read-only probe. Automatic connection
    discovery must never restart NapCat. No config/login credentials are changed.
    """
    if not is_local_webui(launch.url):
        raise NapCatSwitchError('不能通过本机换号流程重启远端 NapCat')
    client = NapCatConnectionProbe(launch.url, config_path=launch.config_path,
                                  preferred_ws_url='ws://127.0.0.1:3001', opener=opener)
    try:
        client._authenticate()
        parsed = urlsplit(launch.url)
        request = Request(urlunsplit((parsed.scheme, parsed.netloc, '/api/QQLogin/RestartNapCat', '', '')),
                          data=b'{}', method='POST', headers={
                              'Content-Type': 'application/json',
                              'Authorization': f'Bearer {client._credential}',
                          })
        with client._opener(request, timeout=client._timeout_seconds) as response:
            raw = response.read(4097)
        if len(raw) > 4096 or not isinstance(payload := json.loads(raw), dict) or payload.get('code') != 0:
            raise ValueError('restart not confirmed')
    except (_ProbeError, OSError, URLError, ValueError):
        raise NapCatSwitchError(
            '旧 NapCat WebUI 仍在运行，但无法确认它已重建 QQ 登录服务。'
            '为避免第二实例抢端口，未再启动另一份 NapCat。'
            '请在 NapCat 手动重启后点“重新连接”，并检查 WebUI Token / NapCat 版本。'
        ) from None
