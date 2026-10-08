"""Explicit user-requested QQ shutdown; never used during startup/reconnect."""
from __future__ import annotations

import csv
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


QQ_PROCESS_NAMES = ('QQ.exe', 'QQCrashReporter.exe')


class QQShutdownError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class QQShutdownResult:
    initial_count: int
    remaining_pids: tuple[int, ...] = ()
    forced: bool = False

    @property
    def success(self) -> bool:
        return not self.remaining_pids


def terminate_all_qq(*, runner=None, owned_launcher=None, grace_seconds=3.0,
                     force_seconds=2.0, clock=time.monotonic, sleep=time.sleep) -> QQShutdownResult:
    """Close all exact QQ image names, then verify. No files/configs are deleted.

    The optional launcher is only the Popen object created by this application;
    never find/kill unrelated cmd.exe, node.exe or Python processes by name.
    Tests must inject runner so no real process control is executed.
    """
    if os.name != 'nt':
        raise QQShutdownError('此 QQ 退出功能仅支持 Windows')
    runner = runner or subprocess.run
    system32 = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32'

    def run(tool, args):
        try:
            return runner([str(system32 / tool), *args], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=5.0,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise QQShutdownError('无法完成 QQ 进程检查/退出；请检查权限或用任务管理器核对') from exc

    def processes():
        pids = set()
        for name in QQ_PROCESS_NAMES:
            result = run('tasklist.exe', ['/FI', f'IMAGENAME eq {name}', '/FO', 'CSV', '/NH'])
            if result.returncode:
                raise QQShutdownError('无法读取 QQ 进程列表，不能确认已退出；请检查权限')
            for row in csv.reader(result.stdout.splitlines()):
                if row and row[0].casefold() in {value.casefold() for value in QQ_PROCESS_NAMES}:
                    if len(row) < 2 or not row[1].isdecimal() or int(row[1]) <= 0:
                        raise QQShutdownError('QQ 进程标识无效，已停止退出操作')
                    pids.add(int(row[1]))
        return tuple(sorted(pids))

    def kill(pids, *, force=False):
        for pid in pids:
            run('taskkill.exe', ['/PID', str(pid), '/T', *(['/F'] if force else [])])

    # Validate the process inventory before any process-control action.
    initial = processes()
    # Cancel an in-progress launcher owned by this app so it cannot start QQ
    # after the cleanup. This is not a blanket cmd/node process termination.
    if owned_launcher is not None and owned_launcher.poll() is None:
        pid = int(owned_launcher.pid)
        if pid <= 0:
            raise QQShutdownError('启动器进程标识无效，已停止退出操作')
        kill((pid,))
        try:
            owned_launcher.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            kill((pid,), force=True)
            try:
                owned_launcher.wait(timeout=2.0)
            except subprocess.TimeoutExpired as exc:
                raise QQShutdownError('本程序的 QQ 启动器仍未停止，请核对任务管理器') from exc

    current = processes() if owned_launcher is not None else initial
    initial_count = len(set(initial) | set(current))
    if not current:
        return QQShutdownResult(initial_count)
    kill(current)

    def wait_remaining(seconds):
        deadline = clock() + seconds
        remaining = processes()
        while remaining and clock() < deadline:
            sleep(min(0.2, max(0.0, deadline - clock())))
            remaining = processes()
        return remaining

    remaining = wait_remaining(grace_seconds)
    forced = bool(remaining)
    if remaining:
        kill(remaining, force=True)
        remaining = wait_remaining(force_seconds)
    return QQShutdownResult(initial_count, remaining, forced)


def shutdown_qq_session(runtime, *, cleanup=None, owned_launcher=None) -> QQShutdownResult:
    """Worker-thread orchestration: first stop chat/reconnect, then close QQ."""
    if runtime is not None:
        runtime.stop(timeout_seconds=5.0)
        if runtime.running:
            raise QQShutdownError('已禁止新发送，但聊天任务尚未完全退出；未执行 QQ 清理，请稍后重试')
    return (cleanup or terminate_all_qq)(owned_launcher=owned_launcher)
