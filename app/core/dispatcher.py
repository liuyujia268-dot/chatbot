"""Incoming message dispatch."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from itertools import count
from typing import Protocol

from app.core.memory import InMemoryStore
from app.core.reply_engine import ReplyEngine
from app.core.session import Message, Session, SessionManager
from app.storage.database import Database


class BackgroundMaintenance(Protocol):
    def schedule(self, conversation_id: str) -> None: ...

    async def drain(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _ActiveGeneration:
    generation_id: int
    conversation_version: int
    task: asyncio.Task[str]


class MessageDispatcher:
    def __init__(
        self,
        sessions: SessionManager,
        engine: ReplyEngine,
        memory: InMemoryStore,
        database: Database,
        maintenance: BackgroundMaintenance | None = None,
    ) -> None:
        self._sessions = sessions
        self._engine = engine
        self._memory = memory
        self._database = database
        self._maintenance = maintenance
        self._active_generations: dict[str, _ActiveGeneration] = {}
        self._generation_ids = count(1)
        self._hydrated_sessions: set[str] = set()

    async def dispatch(
        self,
        session_id: str,
        content: str,
        *,
        persist_incoming: bool = True,
        expected_version: int | None = None,
    ) -> str | None:
        if not content.strip():
            raise ValueError("message content must not be empty")

        if persist_incoming:
            expected_version = self.receive_incoming(session_id, content)
        elif expected_version is None:
            raise ValueError(
                "expected_version is required when incoming is already persisted"
            )

        session = self._ensure_session_loaded(session_id)
        if (
            session.human_takeover
            or session.conversation_version != expected_version
        ):
            return None

        model_messages = self._engine.build_model_messages(session)
        generation = _ActiveGeneration(
            generation_id=next(self._generation_ids),
            conversation_version=expected_version,
            task=asyncio.create_task(
                self._engine.generate_messages(model_messages)
            ),
        )
        self._active_generations[session_id] = generation
        try:
            reply = await generation.task
            if not self._can_publish(session_id, generation):
                return None
            assistant_message = Message(role="assistant", content=reply)
            self._database.save_message(session_id, assistant_message)
            session.add_message(assistant_message.role, assistant_message.content)
            self._memory.append(session_id, assistant_message)
            if self._maintenance is not None:
                self._maintenance.schedule(session_id)
            return reply
        except asyncio.CancelledError:
            if not self._can_publish(session_id, generation):
                return None
            raise
        finally:
            if self._active_generations.get(session_id) == generation:
                self._active_generations.pop(session_id, None)

    def set_human_takeover(self, session_id: str, enabled: bool) -> None:
        self._ensure_session_loaded(session_id)
        was_enabled = self._sessions.is_human_takeover(session_id)
        self._sessions.set_human_takeover(session_id, enabled)
        if enabled:
            if not was_enabled:
                self._sessions.advance_version(session_id)
            self._cancel_active_generation(session_id)

    def is_human_takeover(self, session_id: str) -> bool:
        return self._sessions.is_human_takeover(session_id)

    async def finish_background_tasks(self) -> None:
        if self._maintenance is not None:
            await self._maintenance.drain()

    def receive_incoming(self, session_id: str, content: str) -> int:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        content = content.strip()
        if not content:
            raise ValueError("message content must not be empty")

        session = self._ensure_session_loaded(session_id)
        message = Message(role="user", content=content)
        self._database.save_message(session_id, message)
        session.add_message(message.role, message.content)
        self._memory.append(session_id, message)
        return self._advance_conversation(session_id)

    def _ensure_session_loaded(self, session_id: str) -> Session:
        session = self._sessions.get_or_create(session_id)
        if session_id in self._hydrated_sessions:
            return session
        if not session.messages:
            recent = self._database.list_messages(
                session_id,
                limit=self._engine.context_max_messages,
            )
            for message in recent:
                session.add_message(message.role, message.content)
                self._memory.append(session_id, message)
        self._hydrated_sessions.add(session_id)
        return session

    def _advance_conversation(self, session_id: str) -> int:
        version = self._sessions.advance_version(session_id)
        self._cancel_active_generation(session_id)
        return version

    def _can_publish(
        self,
        session_id: str,
        generation: _ActiveGeneration,
    ) -> bool:
        session = self._sessions.get_or_create(session_id)
        active = self._active_generations.get(session_id)
        return (
            not session.human_takeover
            and session.conversation_version == generation.conversation_version
            and active is not None
            and active.generation_id == generation.generation_id
        )

    def _cancel_active_generation(self, session_id: str) -> None:
        generation = self._active_generations.get(session_id)
        if generation and not generation.task.done():
            generation.task.cancel()


@dataclass(frozen=True, slots=True)
class DispatchResult:
    session_id: str
    merged_content: str
    message_count: int
    conversation_version: int
    reply: str | None
    skip_reason: str | None = None


def format_message_batch(messages: list[str] | tuple[str, ...]) -> str:
    """Keep message boundaries explicit when one user sends a burst."""
    cleaned = [message.strip() for message in messages if message.strip()]
    if not cleaned:
        raise ValueError("message batch must not be empty")
    if len(cleaned) == 1:
        return cleaned[0]
    total = len(cleaned)
    return "\n".join(
        f"【连续消息 {index}/{total}】\n{message}"
        for index, message in enumerate(cleaned, start=1)
    )


@dataclass(slots=True)
class _PendingBatch:
    messages: list[str] = field(default_factory=list)
    waiters: list[asyncio.Future[DispatchResult | None]] = field(default_factory=list)
    timer: asyncio.Task[None] | None = None
    conversation_version: int = 0
    auto_reply_allowed: bool = True


class ContinuousMessageMerger:
    """Debounce consecutive messages independently for each contact."""

    def __init__(
        self,
        dispatcher: MessageDispatcher,
        debounce_seconds: float | None = None,
        *,
        merge_window_seconds: float | None = None,
    ) -> None:
        if debounce_seconds is not None and merge_window_seconds is not None:
            raise ValueError(
                "use debounce_seconds or merge_window_seconds, not both"
            )
        if debounce_seconds is None:
            debounce_seconds = (
                1.2 if merge_window_seconds is None else merge_window_seconds
            )
        if debounce_seconds < 0:
            raise ValueError("debounce_seconds must not be negative")
        self._dispatcher = dispatcher
        self._debounce_seconds = debounce_seconds
        self._pending: dict[str, _PendingBatch] = {}

    async def submit(self, session_id: str, content: str) -> DispatchResult | None:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        if not content.strip():
            raise ValueError("message content must not be empty")

        version = self._dispatcher.receive_incoming(session_id, content.strip())
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[DispatchResult | None] = loop.create_future()
        batch = self._pending.setdefault(session_id, _PendingBatch())
        batch.messages.append(content.strip())
        batch.waiters.append(waiter)
        batch.conversation_version = version
        if self._dispatcher.is_human_takeover(session_id):
            batch.auto_reply_allowed = False
        if batch.timer and not batch.timer.done():
            batch.timer.cancel()
        batch.timer = asyncio.create_task(self._flush_after_delay(session_id))
        return await waiter

    async def _flush_after_delay(self, session_id: str) -> None:
        try:
            await asyncio.sleep(self._debounce_seconds)
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

        merged_content = format_message_batch(batch.messages)
        try:
            if batch.auto_reply_allowed:
                reply = await self._dispatcher.dispatch(
                    session_id,
                    merged_content,
                    persist_incoming=False,
                    expected_version=batch.conversation_version,
                )
                skip_reason = None
            else:
                reply = None
                skip_reason = "human_takeover"
            if reply is None and skip_reason is None:
                skip_reason = (
                    "human_takeover"
                    if self._dispatcher.is_human_takeover(session_id)
                    else "stale"
                )
            result = DispatchResult(
                session_id=session_id,
                merged_content=merged_content,
                message_count=len(batch.messages),
                conversation_version=batch.conversation_version,
                reply=reply,
                skip_reason=skip_reason,
            )
        except Exception as exc:
            for waiter in batch.waiters[:-1]:
                if not waiter.done():
                    waiter.set_result(None)
            if batch.waiters and not batch.waiters[-1].done():
                batch.waiters[-1].set_exception(exc)
            return None

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
