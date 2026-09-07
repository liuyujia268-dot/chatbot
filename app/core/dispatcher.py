"""Incoming message dispatch."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.core.memory import InMemoryStore
from app.core.reply_engine import ReplyEngine
from app.core.session import Message, SessionManager
from app.storage.database import Database


class MessageDispatcher:
    def __init__(
        self,
        sessions: SessionManager,
        engine: ReplyEngine,
        memory: InMemoryStore,
        database: Database,
    ) -> None:
        self._sessions = sessions
        self._engine = engine
        self._memory = memory
        self._database = database

    async def dispatch(
        self,
        session_id: str,
        content: str,
        *,
        persist_incoming: bool = True,
    ) -> str | None:
        if not content.strip():
            raise ValueError("message content must not be empty")

        session = self._sessions.get_or_create(session_id)
        user_message = session.add_message("user", content)
        self._memory.append(session_id, user_message)
        if persist_incoming:
            self._database.save_message(session_id, user_message)

        if session.human_takeover:
            return None

        reply = await self._engine.generate(session)
        assistant_message = session.add_message("assistant", reply)
        self._memory.append(session_id, assistant_message)
        self._database.save_message(session_id, assistant_message)
        return reply

    def set_human_takeover(self, session_id: str, enabled: bool) -> None:
        self._sessions.set_human_takeover(session_id, enabled)

    def is_human_takeover(self, session_id: str) -> bool:
        return self._sessions.is_human_takeover(session_id)

    def record_incoming(self, session_id: str, content: str) -> None:
        self._database.save_message(session_id, Message(role="user", content=content))


@dataclass(frozen=True, slots=True)
class DispatchResult:
    session_id: str
    merged_content: str
    message_count: int
    reply: str | None


@dataclass(slots=True)
class _PendingBatch:
    messages: list[str] = field(default_factory=list)
    waiters: list[asyncio.Future[DispatchResult | None]] = field(default_factory=list)
    timer: asyncio.Task[None] | None = None


class ContinuousMessageMerger:
    """Debounce consecutive messages independently for each contact."""

    def __init__(
        self,
        dispatcher: MessageDispatcher,
        merge_window_seconds: float = 0.5,
        separator: str = "\n",
    ) -> None:
        if merge_window_seconds < 0:
            raise ValueError("merge_window_seconds must not be negative")
        self._dispatcher = dispatcher
        self._merge_window_seconds = merge_window_seconds
        self._separator = separator
        self._pending: dict[str, _PendingBatch] = {}

    async def submit(self, session_id: str, content: str) -> DispatchResult | None:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        if not content.strip():
            raise ValueError("message content must not be empty")

        self._dispatcher.record_incoming(session_id, content.strip())
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[DispatchResult | None] = loop.create_future()
        batch = self._pending.setdefault(session_id, _PendingBatch())
        batch.messages.append(content.strip())
        batch.waiters.append(waiter)
        if batch.timer and not batch.timer.done():
            batch.timer.cancel()
        batch.timer = asyncio.create_task(self._flush_after_delay(session_id))
        return await waiter

    async def _flush_after_delay(self, session_id: str) -> None:
        try:
            await asyncio.sleep(self._merge_window_seconds)
            await self.flush(session_id)
        except asyncio.CancelledError:
            return
        except Exception:
            # The submitter receives the original exception through its waiter.
            return

    async def flush(self, session_id: str) -> DispatchResult | None:
        batch = self._pending.pop(session_id, None)
        if batch is None:
            return None

        current_task = asyncio.current_task()
        if batch.timer and batch.timer is not current_task and not batch.timer.done():
            batch.timer.cancel()

        merged_content = self._separator.join(batch.messages)
        try:
            reply = await self._dispatcher.dispatch(
                session_id,
                merged_content,
                persist_incoming=False,
            )
            result = DispatchResult(
                session_id=session_id,
                merged_content=merged_content,
                message_count=len(batch.messages),
                reply=reply,
            )
        except Exception as exc:
            for waiter in batch.waiters:
                if not waiter.done():
                    waiter.set_exception(exc)
            raise

        for waiter in batch.waiters[:-1]:
            if not waiter.done():
                waiter.set_result(None)
        if batch.waiters and not batch.waiters[-1].done():
            batch.waiters[-1].set_result(result)
        return result

    async def flush_all(self) -> list[DispatchResult]:
        results = await asyncio.gather(
            *(self.flush(session_id) for session_id in tuple(self._pending)),
        )
        return [result for result in results if result is not None]
