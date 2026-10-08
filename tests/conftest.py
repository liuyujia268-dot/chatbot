"""Hard safety boundary for the offline suite, including future regressions."""
from pathlib import Path
import socket
import sqlite3
import subprocess
import threading

import pytest


@pytest.fixture(autouse=True)
def offline_and_isolated(monkeypatch):
    project = Path(__file__).resolve().parents[1]
    original_read = Path.read_text
    original_connect = sqlite3.connect
    original_socket_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    original_run = subprocess.run
    original_popen = subprocess.Popen
    infrastructure = threading.local()

    def read(path, *args, **kwargs):
        resolved = path.resolve()
        if resolved == project / '.env' or (resolved.is_relative_to(project / 'data') and resolved.suffix in {'.db', '.log', '.jsonl'}):
            raise AssertionError('测试禁止读取现用配置或私人聊天数据；请使用tmp_path')
        return original_read(path, *args, **kwargs)

    def connect(path, *args, **kwargs):
        if str(path) != ':memory:' and Path(path).resolve().is_relative_to(project / 'data'):
            raise AssertionError('测试禁止操作正式数据库；请使用tmp_path')
        return original_connect(path, *args, **kwargs)

    def network(*args, **kwargs):
        raise AssertionError('测试禁止真实网络；请使用Mock/Fake')

    def socket_connect(sock, address):
        # Windows implements asyncio's internal self-pipe via socketpair/TCP.
        # Permit only that synchronous infrastructure operation, not application
        # connections (including NapCat/Ollama on localhost).
        if getattr(infrastructure, 'socketpair', False):
            return original_socket_connect(sock, address)
        return network(sock, address)

    def socketpair(*args, **kwargs):
        infrastructure.socketpair = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            infrastructure.socketpair = False

    def process_guard(args):
        command = args[0] if isinstance(args, (list, tuple)) else str(args).split()[0]
        name = Path(str(command).strip('"')).name.casefold()
        if name in {'taskkill', 'taskkill.exe', 'tasklist', 'tasklist.exe'}:
            raise AssertionError('测试禁止真实 QQ 进程操作；请注入 Fake runner')
        values = args if isinstance(args, (list, tuple)) else str(args).split()
        if any(Path(str(value).strip('"')).name.casefold() in {'launcher-user.bat', 'launcher-win10-user.bat', 'chatbot-launcher.cmd'} for value in values):
            raise AssertionError('测试禁止启动真实 NapCat；请注入 Fake launcher')

    def run(args, *rest, **kwargs):
        process_guard(args)
        return original_run(args, *rest, **kwargs)

    def popen(args, *rest, **kwargs):
        process_guard(args)
        return original_popen(args, *rest, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    monkeypatch.setattr(socket.socket, 'connect', socket_connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', network)
    monkeypatch.setattr(socket, 'socketpair', socketpair)
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(subprocess, 'Popen', popen)
