"""Reply generation orchestration."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import replace

from app.core.platform import MessagePart
from app.core.session import Message, MessageContent, Session
from app.memory.context import (
    ContextDebugger,
    MemoryContextProvider,
    ModelContextSnapshot,
    redact_private_information,
)
from app.models.base import ChatModel, EmptyModelResponseError, ModelError


logger = logging.getLogger(__name__)

_INFORMATION_REQUEST = re.compile(
    r"(为什么|怎么(?:办|做|回事)?|如何|是什么|原理|讲讲|解释|介绍|"
    r"具体说|详细说|能正常说话|能不能说清)"
)
_CORRECTION_OR_COMPLAINT = re.compile(
    r"(不对|错了|不是这个|理解错|说话不连贯|回复太短|话太少|"
    r"别只|没回答|说清楚|重新说)"
)
_ONLY_PUNCTUATION = re.compile(r"^[\s？?！!。.，,、…]+$")
_LOW_INFORMATION = re.compile(r"[\s\W_]+", re.UNICODE)


class ReplyEngine:
    def __init__(
        self,
        model: ChatModel,
        system_prompt: str = "",
        context_max_messages: int = 12,
        max_output_chars: int = 1200,
        context_provider: MemoryContextProvider | None = None,
        context_debugger: ContextDebugger | None = None,
        image_history_limit: int = 0,
        repair_prompt: str = "",
    ) -> None:
        if context_max_messages < 1:
            raise ValueError("context_max_messages must be at least 1")
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be at least 1")
        if image_history_limit < 0:
            raise ValueError("image_history_limit must not be negative")
        self._model = model
        self._system_prompt = system_prompt.strip()
        self._context_max_messages = context_max_messages
        self._max_output_chars = max_output_chars
        self._context_provider = context_provider
        self._context_debugger = context_debugger
        self._image_history_limit = image_history_limit
        self._repair_prompt = repair_prompt.strip()

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
        history = self._limit_historical_images(history)

        memory_context = None
        system_messages: list[Message] = []
        if self._system_prompt:
            system_messages.append(
                Message(role="system", content=self._system_prompt)
            )
        if self._context_provider is not None:
            memory_context = self._context_provider.load(
                session.session_id,
                self._content_as_text(current.content) if current else "",
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
        private_information = (
            memory_context.account_profile.private_information
            if memory_context and memory_context.account_profile
            else None
        )
        if private_information:
            model_messages = tuple(
                Message(
                    message.role,
                    self._redact_content(
                        message.content,
                        private_information,
                    ),
                )
                for message in model_messages
            )
            recent_history = tuple(
                Message(
                    message.role,
                    self._redact_content(
                        message.content,
                        private_information,
                    ),
                )
                for message in recent_history
            )
            if current is not None:
                current = Message(
                    current.role,
                    self._redact_content(
                        current.content,
                        private_information,
                    ),
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
        if self._repair_prompt and self._needs_repair(messages, reply):
            try:
                repaired = (
                    await self._model.generate(
                        self._with_repair_instruction(messages)
                    )
                ).strip()
                if repaired:
                    reply = repaired
            except ModelError as exc:
                logger.warning(
                    "reply_repair_failed error=%s",
                    type(exc).__name__,
                )
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

    def _with_repair_instruction(
        self,
        messages: Sequence[Message],
    ) -> tuple[Message, ...]:
        prepared = list(messages)
        current_user_index = next(
            (
                index
                for index in range(len(prepared) - 1, -1, -1)
                if prepared[index].role == "user"
            ),
            len(prepared),
        )
        prepared.insert(
            current_user_index,
            Message("system", self._repair_prompt),
        )
        return tuple(prepared)

    def _needs_repair(
        self,
        messages: Sequence[Message],
        reply: str,
    ) -> bool:
        core = _LOW_INFORMATION.sub("", reply)
        if not core:
            return True
        user_texts = [
            self._content_as_text(message.content).strip()
            for message in messages
            if message.role == "user"
        ]
        if not user_texts:
            return False
        current = user_texts[-1]
        intent_text = current
        if _ONLY_PUNCTUATION.fullmatch(current) and len(user_texts) > 1:
            intent_text = user_texts[-2] + current
        if _CORRECTION_OR_COMPLAINT.search(intent_text) and len(core) < 16:
            return True
        if _INFORMATION_REQUEST.search(intent_text) and len(core) < 12:
            return True
        recent_assistant = [
            _LOW_INFORMATION.sub("", self._content_as_text(message.content))
            for message in messages
            if message.role == "assistant"
        ][-3:]
        return len(core) <= 6 and core in recent_assistant

    @property
    def context_max_messages(self) -> int:
        return self._context_max_messages

    def _limit_historical_images(self, history: list[Message]) -> list[Message]:
        kept = 0
        prepared: list[Message] = []
        for message in reversed(history):
            has_image = (
                not isinstance(message.content, str)
                and any(part.part_type == "image" for part in message.content)
            )
            if has_image and kept >= self._image_history_limit:
                parts = tuple(
                    MessagePart.text_part(part.fallback_text)
                    if part.part_type == "image"
                    else part
                    for part in message.content
                )
                message = Message(message.role, parts)
            elif has_image:
                kept += 1
            prepared.append(message)
        prepared.reverse()
        return prepared

    @staticmethod
    def _content_as_text(content: MessageContent) -> str:
        if isinstance(content, str):
            return content
        return "\n".join(part.fallback_text for part in content)

    @staticmethod
    def _redact_content(
        content: MessageContent,
        private_information: str,
    ) -> MessageContent:
        if isinstance(content, str):
            return redact_private_information(content, private_information)

        def clean(value: str | None) -> str | None:
            return (
                redact_private_information(value, private_information)
                if value is not None
                else None
            )

        return tuple(
            replace(
                part,
                text=clean(part.text),
                file=clean(part.file),
                url=clean(part.url),
                summary=clean(part.summary),
                resolved_url=clean(part.resolved_url),
                description=clean(part.description),
            )
            for part in content
        )
