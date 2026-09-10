"""Application entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from app.adapters.admin_commands import AdminCommandHandler
from app.adapters.console import ConsoleAdapter
from app.adapters.image_resolver import ImageResolver
from app.adapters.outbound import DryRunMessageSender, NapCatMessageSender
from app.adapters.qq import QQAdapter
from app.adapters.websocket_client import OneBotWebSocketEvents
from app.config import ConfigurationError, Settings
from app.core.dispatcher import ContinuousMessageMerger, MessageDispatcher
from app.core.memory import InMemoryStore
from app.core.prompt import load_system_prompt
from app.core.reply_engine import ReplyEngine
from app.core.session import SessionManager
from app.memory.context import ContextDebugger, MemoryContextProvider
from app.memory.processor import MemoryMaintenanceCoordinator, MemoryProcessor
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.models.factory import build_model
from app.storage.database import Database
from app.style.repository import StyleRepository


logger = logging.getLogger(__name__)


def _configure_console_encoding() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _configure_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(path, encoding="utf-8")],
        force=True,
    )


async def async_main() -> None:
    settings = Settings.from_env()
    _configure_logging(settings.log_path)
    model = build_model(settings)
    system_prompt = load_system_prompt(settings.system_prompt_path)
    reply_repair_prompt = load_system_prompt(
        settings.reply_repair_prompt_path
    )
    memory_prompt = load_system_prompt(settings.memory_prompt_path)
    proactive_prompt = load_system_prompt(settings.proactive_prompt_path)
    followup_prompt = load_system_prompt(settings.followup_prompt_path)

    sessions = SessionManager(max_messages=settings.context_max_messages)
    memory = InMemoryStore(max_messages=settings.context_max_messages)
    database = Database(settings.database_path)
    database.initialize()
    style_repository = StyleRepository(
        database,
        context_min_messages=settings.style_context_min_messages,
        context_max_messages=settings.style_context_max_messages,
    )
    memory_repository = MemoryRepository(database)
    memory_service = MemoryService(
        memory_repository,
        confidence_threshold=settings.memory_confidence_threshold,
        context_limit=settings.memory_context_limit,
        context_fallback=settings.memory_context_fallback,
    )
    context_debugger = ContextDebugger(
        enabled=settings.memory_debug,
        redacted_values=(settings.model_api_key or "",),
    )
    memory_processor = MemoryProcessor(
        memory_service,
        model,
        memory_prompt,
        extraction_interval_messages=(
            settings.memory_extraction_interval_messages
        ),
        summary_trigger_messages=settings.summary_trigger_messages,
        batch_messages=settings.memory_processing_batch_messages,
    )
    maintenance = MemoryMaintenanceCoordinator(
        memory_processor,
        enabled=(
            settings.memory_auto_process_enabled
            and settings.model_provider != "mock"
        ),
    )
    dispatcher = MessageDispatcher(
        sessions=sessions,
        engine=ReplyEngine(
            model=model,
            system_prompt=system_prompt,
            context_max_messages=settings.context_max_messages,
            max_output_chars=settings.model_max_output_chars,
            context_provider=MemoryContextProvider(memory_service),
            context_debugger=context_debugger,
            image_history_limit=settings.qq_image_history_limit,
            repair_prompt=reply_repair_prompt,
        ),
        memory=memory,
        database=database,
        maintenance=maintenance,
    )
    merger = ContinuousMessageMerger(
        dispatcher=dispatcher,
        debounce_seconds=settings.message_debounce_seconds,
    )
    logger.info(
        "application_started provider=%s model=%s",
        settings.model_provider,
        settings.model_name or "mock",
    )
    admin_commands = AdminCommandHandler(
        memory_service,
        memory_processor,
        context_debugger,
        style_repository=style_repository,
        style_export_path=settings.style_export_path,
    )
    if not settings.qq_enabled:
        console = ConsoleAdapter(
            dispatcher=dispatcher,
            merger=merger,
            admin_commands=admin_commands,
            dry_run=settings.outbound_dry_run,
        )
        await console.run()
        return
    onebot = OneBotWebSocketEvents(
        settings.qq_ws_url,
        settings.qq_ws_token,
    )
    ai_generated_ids: set[tuple[str, str, str]] = set()
    sender = NapCatMessageSender(
        onebot,
        on_sent=lambda account_id, message_id: ai_generated_ids.add(
            ("qq", account_id, message_id)
        ),
    )
    manual_transport = NapCatMessageSender(onebot)
    manual_sender = (
        DryRunMessageSender(manual_transport)
        if settings.qq_dry_run
        else manual_transport
    )
    console = ConsoleAdapter(
        dispatcher=dispatcher,
        merger=merger,
        admin_commands=admin_commands,
        dry_run=settings.outbound_dry_run,
        qq_mode=True,
        qq_sender=manual_sender,
        qq_dry_run=settings.qq_dry_run,
        qq_primary_contact_id=settings.qq_primary_contact_id,
        qq_primary_contact_name=settings.qq_primary_contact_name,
    )
    qq = QQAdapter(
        merger=merger,
        database=database,
        style_repository=style_repository,
        image_resolver=ImageResolver(
            max_bytes=settings.qq_image_max_bytes,
            timeout_seconds=settings.qq_image_timeout_seconds,
            allow_remote_url=settings.qq_image_allow_remote_url,
        ),
        primary_contact_id=settings.qq_primary_contact_id,
        primary_contact_name=settings.qq_primary_contact_name,
        admin_contact_ids=settings.qq_admin_contact_ids,
        admin_memory_prefix=settings.qq_admin_memory_prefix,
        memory_service=memory_service,
        training_group_ids=settings.qq_training_group_ids,
        reply_group_ids=settings.qq_reply_group_ids,
        dry_run=settings.qq_dry_run,
        proactive_enabled=settings.qq_proactive_enabled,
        proactive_contact_ids=settings.qq_proactive_contact_ids,
        proactive_exclude_contact_ids=(
            settings.qq_proactive_exclude_contact_ids
        ),
        proactive_min_seconds=settings.qq_proactive_min_seconds,
        proactive_max_seconds=settings.qq_proactive_max_seconds,
        proactive_daily_limit=settings.qq_proactive_daily_limit,
        proactive_recent_days=settings.qq_proactive_recent_days,
        proactive_quiet_start=settings.qq_proactive_quiet_start,
        proactive_quiet_end=settings.qq_proactive_quiet_end,
        proactive_prompt=proactive_prompt,
        followup_enabled=settings.qq_followup_enabled,
        followup_probability=settings.qq_followup_probability,
        followup_min_delay_seconds=(
            settings.qq_followup_min_delay_seconds
        ),
        followup_max_delay_seconds=(
            settings.qq_followup_max_delay_seconds
        ),
        followup_prompt=followup_prompt,
        event_source=onebot,
        sender=sender,
        ai_generated_message_ids=ai_generated_ids,
    )
    qq_task = asyncio.create_task(qq.run())
    try:
        await console.run()
    finally:
        qq_task.cancel()
        await asyncio.gather(qq_task, return_exceptions=True)


def run() -> None:
    _configure_console_encoding()
    try:
        asyncio.run(async_main())
    except ConfigurationError as exc:
        print(f"[配置错误] {exc}")


if __name__ == "__main__":
    run()
