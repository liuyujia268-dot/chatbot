"""Application entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from app.adapters.admin_commands import AdminCommandHandler
from app.adapters.console import ConsoleAdapter
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
    memory_prompt = load_system_prompt(settings.memory_prompt_path)

    sessions = SessionManager(max_messages=settings.context_max_messages)
    memory = InMemoryStore(max_messages=settings.context_max_messages)
    database = Database(settings.database_path)
    database.initialize()
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
    )
    await ConsoleAdapter(
        dispatcher=dispatcher,
        merger=merger,
        admin_commands=admin_commands,
    ).run()


def run() -> None:
    _configure_console_encoding()
    try:
        asyncio.run(async_main())
    except ConfigurationError as exc:
        print(f"[配置错误] {exc}")


if __name__ == "__main__":
    run()
