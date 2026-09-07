"""Reply generation orchestration."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.core.session import Message, Session
from app.memory.context import (
    ContextDebugger,
    MemoryContextProvider,
    ModelContextSnapshot,
)
from app.models.base import ChatModel, EmptyModelResponseError


logger = logging.getLogger(__name__)


class ReplyEngine:
    def __init__(
        self,
        model: ChatModel,
        system_prompt: str = "",
        context_max_messages: int = 12,
        max_output_chars: int = 1200,
        context_provider: MemoryContextProvider | None = None,
        context_debugger: ContextDebugger | None = None,
    ) -> None:
        if context_max_messages < 1:
            raise ValueError("context_max_messages must be at least 1")
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be at least 1")
        self._model = model
        self._system_prompt = system_prompt.strip()
        self._context_max_messages = context_max_messages
        self._max_output_chars = max_output_chars
        self._context_provider = context_provider
        self._context_debugger = context_debugger

    def build_model_messages(self, session: Session) -> tuple[Message, ...]:
        conversation = [
            message
            for message in session.messages
            if message.role in {"user", "assistant"}
        ]
        current = (
            conversation[-1]
            if conversation and conversation[-1].role == "user"
            else None
        )
        if current is not None:
            history_budget = self._context_max_messages - 1
            history = (
                conversation[:-1][-history_budget:]
                if history_budget > 0
                else []
            )
        else:
            history = conversation[-self._context_max_messages :]

        memory_context = None
        system_messages: list[Message] = []
        if self._system_prompt:
            system_messages.append(
                Message(role="system", content=self._system_prompt)
            )
        if self._context_provider is not None:
            memory_context = self._context_provider.load(
                session.session_id,
                current.content if current else "",
            )
            system_messages.extend(
                self._context_provider.system_messages(memory_context)
            )

        recent_history = tuple(history)
        model_messages = tuple(
            [
                *system_messages,
                *recent_history,
                *([current] if current is not None else []),
            ]
        )
        snapshot = ModelContextSnapshot(
            conversation_id=session.session_id,
            model_messages=model_messages,
            recent_history=recent_history,
            current_message=current,
            memory_context=memory_context,
        )
        if self._context_debugger is not None:
            self._context_debugger.emit(snapshot)
        return snapshot.model_messages

    async def generate(self, session: Session) -> str:
        return await self.generate_messages(self.build_model_messages(session))

    async def generate_messages(self, messages: Sequence[Message]) -> str:
        """Generate from an immutable context snapshot."""
        reply = (await self._model.generate(tuple(messages))).strip()
        if not reply:
            raise EmptyModelResponseError("模型返回了空回复，请重试。")
        if len(reply) > self._max_output_chars:
            logger.warning(
                "model_output_truncated original_chars=%s limit=%s",
                len(reply),
                self._max_output_chars,
            )
            if self._max_output_chars == 1:
                return "…"
            return reply[: self._max_output_chars - 1].rstrip() + "…"
        return reply

    @property
    def context_max_messages(self) -> int:
        return self._context_max_messages
