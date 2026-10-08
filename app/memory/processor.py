"""Batched summary and memory-candidate maintenance."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from app.core.identity import account_key_for

from app.core.session import Message
from app.memory.context import redact_private_information
from app.memory.service import MemoryService
from app.memory.types import CandidateProposal, MaintenanceReport, StoredMessage
from app.models.base import ChatModel


logger = logging.getLogger(__name__)


class MemoryProcessor:
    def __init__(
        self,
        service: MemoryService,
        model: ChatModel,
        prompt: str,
        *,
        extraction_interval_messages: int = 12,
        summary_trigger_messages: int = 24,
        batch_messages: int = 40,
    ) -> None:
        if extraction_interval_messages < 1:
            raise ValueError("extraction interval must be at least 1")
        if summary_trigger_messages < 1:
            raise ValueError("summary trigger must be at least 1")
        if batch_messages < 1:
            raise ValueError("batch size must be at least 1")
        self.service = service
        self._model = model
        self._prompt = prompt.strip()
        self.extraction_interval_messages = extraction_interval_messages
        self.summary_trigger_messages = summary_trigger_messages
        self.batch_messages = batch_messages

    def is_due(self, conversation_id: str) -> bool:
        repository = self.service.repository
        database = repository.database
        last_processed = repository.get_processing_state(conversation_id)
        extraction_due = (
            database.count_messages_after(conversation_id, last_processed)
            >= self.extraction_interval_messages
        )
        summary = repository.latest_summary(conversation_id)
        summary_end = summary.end_message_id if summary else 0
        summary_due = (
            database.count_messages_after(conversation_id, summary_end)
            >= self.summary_trigger_messages
        )
        return extraction_due or summary_due

    async def process(
        self,
        conversation_id: str,
        *,
        force: bool = False,
    ) -> MaintenanceReport:
        repository = self.service.repository
        database = repository.database
        last_processed = repository.get_processing_state(conversation_id)
        candidate_messages = database.list_stored_messages(
            conversation_id,
            after_id=last_processed,
            limit=self.batch_messages,
        )
        previous_summary = repository.latest_summary(conversation_id)
        summary_end = (
            previous_summary.end_message_id if previous_summary else 0
        )
        unsummarized_count = database.count_messages_after(
            conversation_id,
            summary_end,
        )
        summary_due = unsummarized_count >= self.summary_trigger_messages
        summary_messages = (
            database.list_stored_messages(
                conversation_id,
                after_id=summary_end,
                limit=self.batch_messages,
            )
            if summary_due
            else []
        )
        extraction_due = (
            len(candidate_messages) >= self.extraction_interval_messages
        )
        if not force and not extraction_due and not summary_due:
            return self._skipped(conversation_id, "threshold_not_reached")
        if not candidate_messages and not summary_messages:
            return self._skipped(conversation_id, "no_new_messages")

        payload = self._build_payload(
            conversation_id,
            candidate_messages,
            summary_messages,
            previous_summary.content if previous_summary else None,
            summary_due,
        )
        account_profile = repository.get_account_profile(account_key_for(conversation_id))
        private_information = (
            account_profile.private_information if account_profile else None
        )
        request_messages = (
            Message(
                "system",
                redact_private_information(
                    self._prompt + "\n输出只允许一个 JSON 对象，必须包含 summary 和 candidates。"
                    "输入 summary_required=true 时，summary 必须是非空中文字符串；"
                    "否则可以为 null。candidates 必须为数组，无可靠候选时返回 []。",
                    private_information,
                ),
            ),
            Message(
                "user",
                redact_private_information(
                    json.dumps(payload, ensure_ascii=False),
                    private_information,
                ),
            ),
        )
        raw = await self._model.generate(request_messages)
        output = self._parse_output(raw)

        summary_id: int | None = None
        summary_missing = False
        if summary_due:
            summary_content = output.get("summary")
            if not isinstance(summary_content, str) or not summary_content.strip():
                # Do not invent a summary, advance its range, or discard valid
                # candidates just because an optional background result failed.
                summary_missing = True
            else:
                assert summary_messages
                summary = repository.add_summary(
                    conversation_id=conversation_id,
                    content=summary_content,
                    start_message_id=(
                        previous_summary.start_message_id
                        if previous_summary
                        else summary_messages[0].id
                    ),
                    end_message_id=summary_messages[-1].id,
                    previous_summary_id=(
                        previous_summary.id if previous_summary else None
                    ),
                )
                summary_id = summary.id

        candidate_ids: list[int] = []
        accepted_ids: list[int] = []
        for item in output.get("candidates", []):
            try:
                proposal = self._proposal(conversation_id, item)
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "invalid_memory_candidate conversation=%s",
                    conversation_id,
                    exc_info=True,
                )
                continue
            candidate, memory = self.service.submit_candidate(proposal)
            candidate_ids.append(candidate.id)
            if memory is not None and candidate.status == "accepted":
                accepted_ids.append(memory.id)

        if candidate_messages:
            repository.update_processing_state(
                conversation_id,
                candidate_messages[-1].id,
            )
        processed = [*candidate_messages, *summary_messages]
        return MaintenanceReport(
            conversation_id=conversation_id,
            processed_start_message_id=(
                min(message.id for message in processed) if processed else None
            ),
            processed_end_message_id=(
                max(message.id for message in processed) if processed else None
            ),
            summary_id=summary_id,
            candidate_ids=tuple(candidate_ids),
            accepted_memory_ids=tuple(accepted_ids),
            skipped_reason="summary_missing" if summary_missing else None,
        )

    def _build_payload(
        self,
        conversation_id: str,
        candidate_messages: list[StoredMessage],
        summary_messages: list[StoredMessage],
        previous_summary: str | None,
        summary_required: bool,
    ) -> dict[str, Any]:
        active_memories = self.service.repository.list_memories(
            conversation_id,
            status="active",
        )[: max(10, self.service.context_limit * 2)]
        return {
            "conversation_id": conversation_id,
            "summary_required": summary_required,
            "previous_summary": previous_summary,
            "summary_messages": self._message_rows(summary_messages),
            "candidate_messages": self._message_rows(candidate_messages),
            "active_memories": [
                {
                    "id": memory.id,
                    "memory_type": memory.memory_type,
                    "memory_key": memory.memory_key,
                    "content": memory.content,
                }
                for memory in active_memories
            ],
        }

    @staticmethod
    def _message_rows(messages: list[StoredMessage]) -> list[dict[str, Any]]:
        return [
            {
                "message_id": message.id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at,
            }
            for message in messages
        ]

    @staticmethod
    def _parse_output(raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1]).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("maintenance model did not return valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("maintenance response must be a JSON object")
        candidates = payload.get("candidates", [])
        if not isinstance(candidates, list):
            raise ValueError("maintenance candidates must be a list")
        summary = payload.get("summary")
        if summary is not None and not isinstance(summary, str):
            raise ValueError("maintenance summary must be text or null")
        return payload

    @staticmethod
    def _proposal(
        conversation_id: str,
        item: Any,
    ) -> CandidateProposal:
        if not isinstance(item, dict):
            raise TypeError("candidate must be an object")
        source_ids = item.get("source_message_ids")
        if (
            not isinstance(source_ids, list)
            or not source_ids
            or any(
                not isinstance(message_id, int) or isinstance(message_id, bool)
                for message_id in source_ids
            )
        ):
            raise ValueError("candidate source ids are invalid")
        target = item.get("target_memory_id")
        if target is not None and (
            not isinstance(target, int) or isinstance(target, bool)
        ):
            raise ValueError("candidate target id is invalid")
        return CandidateProposal(
            conversation_id=conversation_id,
            memory_type=str(item["memory_type"]).strip(),
            memory_key=str(item["memory_key"]).strip().lower(),
            content=str(item["content"]).strip(),
            confidence=float(item["confidence"]),
            source_message_ids=tuple(source_ids),
            source_quote=str(item["source_quote"]).strip(),
            action=str(item["action"]).strip().lower(),
            target_memory_id=target,
        )

    @staticmethod
    def _skipped(
        conversation_id: str,
        reason: str,
    ) -> MaintenanceReport:
        return MaintenanceReport(
            conversation_id=conversation_id,
            processed_start_message_id=None,
            processed_end_message_id=None,
            summary_id=None,
            candidate_ids=(),
            accepted_memory_ids=(),
            skipped_reason=reason,
        )


class MemoryMaintenanceCoordinator:
    """Run at most one non-blocking maintenance task per conversation."""

    def __init__(
        self,
        processor: MemoryProcessor,
        *,
        enabled: bool = True,
        failure_retry_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.processor = processor
        self.enabled = enabled
        if failure_retry_seconds <= 0:
            raise ValueError("memory failure retry delay must be positive")
        self._failure_retry_seconds = failure_retry_seconds
        self._clock = clock
        self._retry_after: dict[str, float] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def schedule(self, conversation_id: str) -> None:
        if self._clock() < self._retry_after.get(conversation_id, 0):
            return
        if not self.enabled or not self.processor.is_due(conversation_id):
            return
        existing = self._tasks.get(conversation_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._run(conversation_id))
        self._tasks[conversation_id] = task

    async def _run(self, conversation_id: str) -> None:
        try:
            report = await self.processor.process(conversation_id)
            if report.skipped_reason == "summary_missing":
                self._retry_after[conversation_id] = self._clock() + self._failure_retry_seconds
                logger.warning(
                    "memory_summary_missing conversation=%s retry_seconds=%s",
                    conversation_id, self._failure_retry_seconds,
                )
            else:
                self._retry_after.pop(conversation_id, None)
            logger.info(
                "memory_maintenance_complete conversation=%s summary=%s "
                "candidates=%s accepted=%s",
                conversation_id,
                report.summary_id,
                len(report.candidate_ids),
                len(report.accepted_memory_ids),
            )
        except Exception:
            self._retry_after[conversation_id] = self._clock() + self._failure_retry_seconds
            logger.exception(
                "memory_maintenance_failed conversation=%s",
                conversation_id,
            )
        finally:
            current = asyncio.current_task()
            if self._tasks.get(conversation_id) is current:
                self._tasks.pop(conversation_id, None)

    async def drain(self) -> None:
        tasks = tuple(task for task in self._tasks.values() if not task.done())
        if tasks:
            await asyncio.gather(*tasks)
