from __future__ import annotations

import asyncio

import pytest

from app.adapters.websocket_client import OneBotWebSocketEvents


class FakeWriter:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def test_idle_connection_sends_ping_and_continues_after_pong() -> None:
    async def scenario() -> None:
        client = OneBotWebSocketEvents(
            "ws://127.0.0.1:3001",
            ping_interval_seconds=0.01,
            ping_timeout_seconds=0.01,
        )
        writer = FakeWriter()
        calls = 0

        async def read_frame(_reader):
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.Event().wait()
            if calls == 2:
                return True, 0xA, b"pong"
            return True, 0x1, b"{}"

        client._read_frame = read_frame  # type: ignore[method-assign]
        message = await anext(client._messages(object(), writer))

        assert message == "{}"
        assert writer.writes
        assert writer.writes[0][0] & 0x0F == 0x9

    asyncio.run(scenario())


def test_idle_connection_without_response_times_out() -> None:
    async def scenario() -> None:
        client = OneBotWebSocketEvents(
            "ws://127.0.0.1:3001",
            ping_interval_seconds=0.01,
            ping_timeout_seconds=0.01,
        )
        writer = FakeWriter()

        async def never_returns(_reader):
            await asyncio.Event().wait()

        client._read_frame = never_returns  # type: ignore[method-assign]
        with pytest.raises(ConnectionError, match="keepalive timed out"):
            await anext(client._messages(object(), writer))
        assert writer.writes
        assert writer.writes[0][0] & 0x0F == 0x9

    asyncio.run(scenario())


def test_normal_close_waits_before_reconnecting_and_fails_pending_requests():
    async def scenario():
        statuses = []
        client = OneBotWebSocketEvents('ws://localhost:3001', status_callback=lambda *args: statuses.append(args))
        writer = FakeWriter()
        pending = asyncio.get_running_loop().create_future()
        client._pending['fake'] = pending
        calls = []

        async def connect():
            calls.append('connect')
            return object(), writer

        async def messages(*args):
            if False:
                yield ''

        async def wait():
            calls.append('wait')
            raise asyncio.CancelledError

        client._connect, client._messages, client._wait_reconnect = connect, messages, wait
        with pytest.raises(asyncio.CancelledError):
            await anext(client.__aiter__())
        assert calls == ['connect', 'wait'] and writer.closed
        assert [status for status, detail in statuses] == ['connecting', 'connected', 'disconnected']
        assert isinstance(pending.exception(), ConnectionError)
    asyncio.run(scenario())


def test_endpoint_change_rejects_inflight_old_handshake():
    async def scenario():
        client = OneBotWebSocketEvents('ws://localhost:3001', token='A')
        started, release = asyncio.Event(), asyncio.Event()
        old, new = FakeWriter(), FakeWriter()
        attempts = []

        async def connect():
            attempts.append((client.url, client.token))
            if len(attempts) == 1:
                started.set()
                await release.wait()
                return object(), old
            return object(), new

        async def messages(*args):
            yield '{}'

        client._connect, client._messages = connect, messages
        events = client.__aiter__()
        task = asyncio.create_task(anext(events))
        await started.wait()
        client.configure_connection('ws://localhost:3002', 'B')
        release.set()
        assert await task == {}
        assert attempts == [('ws://localhost:3001', 'A'), ('ws://localhost:3002', 'B')]
        assert old.closed and client._writer is new
        await events.aclose()
    asyncio.run(scenario())


def test_disabled_endpoint_does_not_keep_opening_sockets():
    async def scenario():
        client = OneBotWebSocketEvents('ws://localhost:3001')
        client.configure_connection(client.url, None, enabled=False)

        async def connect():
            pytest.fail('logged-out/missing-server state must not open OneBot')

        async def wait():
            raise asyncio.CancelledError

        client._connect, client._wait_reconnect = connect, wait
        with pytest.raises(asyncio.CancelledError):
            await anext(client.__aiter__())
    asyncio.run(scenario())


@pytest.mark.parametrize('error,expected', [(ConnectionRefusedError('SECRET'), '端口未监听'),
                                          (TimeoutError('SECRET'), '连接超时')])
def test_connection_errors_have_specific_sanitized_hints(error, expected):
    async def scenario():
        statuses = []
        client = OneBotWebSocketEvents('ws://localhost:3001', token='SECRET', status_callback=lambda *args: statuses.append(args))

        async def connect():
            raise error

        async def wait():
            raise asyncio.CancelledError

        client._connect, client._wait_reconnect = connect, wait
        with pytest.raises(asyncio.CancelledError):
            await anext(client.__aiter__())
        assert expected in statuses[-1][1]
        assert 'SECRET' not in repr(statuses)
    asyncio.run(scenario())
