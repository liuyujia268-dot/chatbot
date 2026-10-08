"""Incoming message dispatch."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import count
from typing import Protocol

from app.core.memory import InMemoryStore
from app.core.platform import PlatformMessage, OutboundMessage
from app.core.reply_engine import ReplyEngine
from app.core.session import Message, Session, SessionManager
from app.storage.database import Database


def _reply_fingerprint(value: str) -> str:
    """Compare visible reply text while ignoring spacing and punctuation."""
    alphanumeric = "".join(
        character
        for character in value.casefold()
        if character.isalnum()
    )
    return alphanumeric or "".join(value.casefold().split())


def _is_followup_skip(value: str) -> bool:
    """Recognize the model's explicit decision that no second message is useful."""
    return "nofollowup" in _reply_fingerprint(value)


def _replies_too_similar(first: str, second: str) -> bool:
    """Reject exact, contained, or strongly overlapping follow-up replies."""
    first_key = _reply_fingerprint(first)
    second_key = _reply_fingerprint(second)
    if not first_key or not second_key:
        return False
    if first_key == second_key:
        return True

    shorter, longer = sorted((first_key, second_key), key=len)
    if len(shorter) >= 4 and shorter in longer:
        return True
    if SequenceMatcher(None, first_key, second_key).ratio() >= 0.76:
        return True

    if len(shorter) < 4:
        return False
    first_pairs = {
        first_key[index:index + 2]
        for index in range(len(first_key) - 1)
    }
    second_pairs = {
        second_key[index:index + 2]
        for index in range(len(second_key) - 1)
    }
    smaller_pair_count = min(len(first_pairs), len(second_pairs))
    return (
        smaller_pair_count > 0
        and len(first_pairs & second_pairs) / smaller_pair_count >= 0.75
    )


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
        self._simulation_mode = False

    @property
    def database(self) -> Database:
        return self._database

    def set_simulation_mode(self, enabled: bool) -> None:
        if self._simulation_mode == bool(enabled):
            return
        self._simulation_mode = bool(enabled)
        for session_id in tuple(self._hydrated_sessions):
            self._advance_conversation(session_id)
            self._sessions.get_or_create(session_id).messages.clear()
            self._memory.clear(session_id)
        self._hydrated_sessions.clear()

    def accept_delivered_history(self, message: OutboundMessage, simulated: bool) -> None:
        if simulated and not self._simulation_mode:
            return
        session = self._ensure_session_loaded(message.session_id)
        # DB was committed before this callback; refresh instead of double append.
        session.messages.clear()
        self._memory.clear(message.session_id)
        self._hydrated_sessions.discard(message.session_id)
        self._ensure_session_loaded(message.session_id)
        if not simulated and self._maintenance is not None:
            self._maintenance.schedule(message.session_id)

    async def dispatch(
        self,
        session_id: str,
        content: str,
        *,
        persist_incoming: bool = True,
        expected_version: int | None = None,
        defer_commit: bool = False,
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
            self._cache_current_image_descriptions(session, reply)
            if defer_commit:
                return reply
            assistant_message = Message(role="assistant", content=reply)
            self._database.save_message(session_id, assistant_message, history_kind="simulated")
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

    async def generate_proactive(
        self,
        session_id: str,
        instruction: str,
        *, defer_commit: bool = False,
    ) -> str | None:
        """Generate one unsolicited message without fabricating an inbound turn."""
        if not instruction.strip():
            raise ValueError("proactive instruction must not be empty")
        session = self._ensure_session_loaded(session_id)
        if (
            session.human_takeover
            or not session.messages
            or session_id in self._active_generations
        ):
            return None
        version = session.conversation_version
        messages = list(self._engine.build_model_messages(session))
        insert_at = 0
        while insert_at < len(messages) and messages[insert_at].role == "system":
            insert_at += 1
        messages.insert(insert_at, Message("system", instruction.strip()))
        generation = _ActiveGeneration(
            generation_id=next(self._generation_ids),
            conversation_version=version,
            task=asyncio.create_task(self._engine.generate_messages(messages)),
        )
        self._active_generations[session_id] = generation
        try:
            reply = await generation.task
            if not self._can_publish(session_id, generation):
                return None
            if defer_commit:
                return reply
            assistant_message = Message(role="assistant", content=reply)
            self._database.save_message(session_id, assistant_message, history_kind="simulated")
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

    async def generate_followup(
        self,
        session_id: str,
        expected_version: int,
        instruction: str,
        *, defer_commit: bool = False,
    ) -> str | None:
        """Generate a second message only while the first reply is still current."""
        if not instruction.strip():
            raise ValueError("follow-up instruction must not be empty")
        session = self._ensure_session_loaded(session_id)
        if (
            session.human_takeover
            or session.conversation_version != expected_version
            or session_id in self._active_generations
        ):
            return None
        messages = list(self._engine.build_model_messages(session))
        insert_at = 0
        while insert_at < len(messages) and messages[insert_at].role == "system":
            insert_at += 1
        messages.insert(insert_at, Message("system", instruction.strip()))
        generation = _ActiveGeneration(
            generation_id=next(self._generation_ids),
            conversation_version=expected_version,
            task=asyncio.create_task(self._engine.generate_messages(messages)),
        )
        self._active_generations[session_id] = generation
        try:
            reply = await generation.task
            if not self._can_publish(session_id, generation):
                return None
            if _is_followup_skip(reply):
                return None
            previous_reply = next(
                (
                    str(message.content)
                    for message in reversed(session.messages)
                    if message.role == "assistant"
                    and isinstance(message.content, str)
                ),
                None,
            )
            if (
                previous_reply is not None
                and _replies_too_similar(reply, previous_reply)
            ):
                return None
            if defer_commit:
                return reply
            assistant_message = Message(role="assistant", content=reply)
            self._database.save_message(session_id, assistant_message, history_kind="simulated")
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
        self._database.set_human_takeover(session_id, enabled)
        was_enabled = self._sessions.is_human_takeover(session_id)
        self._sessions.set_human_takeover(session_id, enabled)
        if enabled:
            if not was_enabled:
                self._sessions.advance_version(session_id)
            self._cancel_active_generation(session_id)

    def is_human_takeover(self, session_id: str) -> bool:
        self._ensure_session_loaded(session_id)
        return self._sessions.is_human_takeover(session_id)

    def conversation_version(self, session_id: str) -> int:
        return self._ensure_session_loaded(session_id).conversation_version

    def is_version_current(self, session_id: str, expected_version: int) -> bool:
        """Return whether another outbound bubble may still be published."""
        session = self._ensure_session_loaded(session_id)
        return (
            not session.human_takeover
            and session.conversation_version == expected_version
        )

    def interrupt_current_reply(self, session_id: str) -> int:
        """Invalidate in-flight work without enabling persistent takeover."""
        self._ensure_session_loaded(session_id)
        return self._advance_conversation(session_id)

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

    def receive_platform_message(
        self,
        message: PlatformMessage,
        *, invalidate: bool = True, background_to_context: bool = False,
    ) -> int | None:
        """Persist a platform text once; never interpret its text as a command."""
        if message.is_self:
            return None
        session = self._ensure_session_loaded(message.session_id)
        if not self._database.save_inbound_platform_message(message):
            return None
        if not invalidate and not background_to_context:
            # Background group chatter is durable but is not a new request.
            # Keep the currently pending request at the tail of the live snapshot.
            return session.conversation_version
        content = message.parts if message.has_images else message.storage_text
        incoming = Message(role="user", content=content)
        session.add_message(incoming.role, incoming.content)
        self._memory.append(message.session_id, incoming)
        return self._advance_conversation(message.session_id) if invalidate else session.conversation_version

    def resolve_platform_message(self, message: PlatformMessage) -> None:
        self._database.update_platform_content(message)
        session = self._ensure_session_loaded(message.session_id)
        for index, item in enumerate(session.messages):
            if isinstance(item.content, str):
                continue
            if any(part.source_message_id == message.message_id and part.source_account_id == message.account_id
                   for part in item.content):
                session.messages[index] = Message('user', message.parts if message.has_images else message.storage_text)
                break

    def receive_human_self_platform_message(
        self,
        message: PlatformMessage,
    ) -> bool:
        """Record an operator message and invalidate only the current reply."""
        if not message.is_self:
            raise ValueError("human self message must have is_self=true")
        session = self._ensure_session_loaded(message.session_id)
        if not self._database.save_human_self_platform_message(message):
            return False
        outgoing = Message(role="assistant", content=message.storage_text)
        session.add_message(outgoing.role, outgoing.content)
        self._memory.append(message.session_id, outgoing)
        self._advance_conversation(message.session_id)
        return True

    def _cache_current_image_descriptions(
        self,
        session: Session,
        reply: str,
    ) -> None:
        for message in reversed(session.messages):
            if message.role == "assistant":
                break
            if isinstance(message.content, str):
                continue
            for part in message.content:
                if (
                    part.part_type != "image"
                    or not part.source_platform
                    or not part.source_account_id
                    or not part.source_message_id
                    or part.part_index is None
                ):
                    continue
                self._database.save_image_description(
                    platform=part.source_platform,
                    account_id=part.source_account_id,
                    message_id=part.source_message_id,
                    part_index=part.part_index,
                    description=reply,
                )
                session.cache_image_description(
                    part.source_message_id,
                    part.part_index,
                    reply,
                )

    def _ensure_session_loaded(self, session_id: str) -> Session:
        session = self._sessions.get_or_create(session_id)
        if session_id in self._hydrated_sessions:
            return session
        session.human_takeover = self._database.get_human_takeover(session_id)
        if not session.messages:
            recent = self._database.list_messages(
                session_id,
                limit=self._engine.context_max_messages,
                include_simulated=self._simulation_mode or session_id.startswith('scope:console:'),
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
    defer_commit: bool = False
    preparations: list[asyncio.Task] = field(default_factory=list)
    replyable: list[bool] = field(default_factory=list)


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
        return await self._enqueue(session_id, content.strip(), version)

    async def submit_platform(
        self,
        message: PlatformMessage,
        *, defer_commit: bool = False,
        prepare=None,
    ) -> DispatchResult | None:
        """Ordinary external-input path; slash-prefixed text stays chat text."""
        version = self._dispatcher.receive_platform_message(message)
        if version is None:
            return None
        return await self._enqueue(
            message.session_id,
            message.storage_text,
            version,
            defer_commit=defer_commit,
            platform_message=message, prepare=prepare,
        )

    def record_platform(self, message: PlatformMessage, *, invalidate: bool = True, background_to_context: bool = False) -> int | None:
        """Record and version an external message without scheduling a reply."""
        return self._dispatcher.receive_platform_message(message, invalidate=invalidate, background_to_context=background_to_context)

    async def generate_proactive(
        self,
        session_id: str,
        instruction: str,
        *, defer_commit: bool = False,
    ) -> str | None:
        if session_id in self._pending:
            return None
        return await self._dispatcher.generate_proactive(session_id, instruction, defer_commit=defer_commit)

    async def generate_followup(
        self,
        session_id: str,
        expected_version: int,
        instruction: str,
        *, defer_commit: bool = False,
    ) -> str | None:
        if session_id in self._pending:
            return None
        return await self._dispatcher.generate_followup(
            session_id,
            expected_version,
            instruction,
            defer_commit=defer_commit,
        )

    def record_human_self_platform(self, message: PlatformMessage) -> bool:
        return self._dispatcher.receive_human_self_platform_message(message)

    def interrupt_current_reply(self, session_id: str) -> int:
        return self._dispatcher.interrupt_current_reply(session_id)

    def is_human_takeover(self, session_id: str) -> bool:
        return self._dispatcher.is_human_takeover(session_id)

    def is_version_current(self, session_id: str, expected_version: int) -> bool:
        return self._dispatcher.is_version_current(session_id, expected_version)

    def conversation_version(self, session_id: str) -> int:
        return self._dispatcher.conversation_version(session_id)

    def set_human_takeover(self, session_id: str, enabled: bool) -> None:
        self._dispatcher.set_human_takeover(session_id, enabled)

    def set_simulation_mode(self, enabled: bool) -> None:
        self._dispatcher.set_simulation_mode(enabled)

    def accept_delivered_history(self, message: OutboundMessage, simulated: bool) -> None:
        self._dispatcher.accept_delivered_history(message, simulated)

    async def _enqueue(
        self,
        session_id: str,
        content: str,
        version: int,
        *, defer_commit: bool = False,
        platform_message: PlatformMessage | None = None,
        prepare=None,
    ) -> DispatchResult | None:
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[DispatchResult | None] = loop.create_future()
        batch = self._pending.setdefault(session_id, _PendingBatch())
        batch.messages.append(content)
        position = len(batch.messages) - 1
        batch.replyable.append(True)
        if prepare is not None and platform_message is not None:
            async def resolve():
                resolved = await prepare(platform_message)
                self._dispatcher.resolve_platform_message(resolved)
                batch.messages[position] = resolved.storage_text
                batch.replyable[position] = any(part.part_type == 'text' or part.model_image_url for part in resolved.parts)
            batch.preparations.append(asyncio.create_task(resolve()))
        batch.waiters.append(waiter)
        batch.conversation_version = version
        batch.defer_commit = defer_commit
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
        batch = self._pending.get(session_id)
        if batch is None:
            return None

        if batch.preparations:
            await asyncio.shield(asyncio.gather(*batch.preparations))
        if self._pending.get(session_id) is not batch:
            return None
        self._pending.pop(session_id)

        current_task = asyncio.current_task()
        if batch.timer and batch.timer is not current_task and not batch.timer.done():
            batch.timer.cancel()

        merged_content = format_message_batch(batch.messages)
        try:
            if batch.auto_reply_allowed and any(batch.replyable):
                reply = await self._dispatcher.dispatch(
                    session_id,
                    merged_content,
                    persist_incoming=False,
                    expected_version=batch.conversation_version,
                    defer_commit=batch.defer_commit,
                )
                skip_reason = None
            else:
                reply = None
                skip_reason = "human_takeover" if not batch.auto_reply_allowed else "unsupported_media"
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
