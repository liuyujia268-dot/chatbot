"""Shared construction of the chat core for console and desktop front ends."""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.admin_commands import AdminCommandHandler
from app.config import Settings
from app.core.dispatcher import ContinuousMessageMerger, MessageDispatcher
from app.core.memory import InMemoryStore
from app.core.prompt import load_system_prompt
from app.core.reply_engine import ReplyEngine
from app.core.session import SessionManager
from app.memory.context import ContextDebugger, MemoryContextProvider
from app.memory.processor import MemoryMaintenanceCoordinator, MemoryProcessor
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.storage.database import Database
from app.style.repository import StyleRepository
from app.models.factory import build_model


@dataclass(slots=True)
class CoreRuntime:
    """Objects shared by every local user interface."""

    settings: Settings
    database: Database
    style_repository: StyleRepository
    memory_service: MemoryService
    memory_processor: MemoryProcessor
    context_debugger: ContextDebugger
    dispatcher: MessageDispatcher
    merger: ContinuousMessageMerger
    admin_commands: AdminCommandHandler
    proactive_prompt: str
    followup_prompt: str


def build_core_runtime(settings: Settings) -> CoreRuntime:
    """Build the existing model, memory and dispatch chain exactly once."""

    model = build_model(settings)
    system_prompt = load_system_prompt(settings.system_prompt_path)
    reply_repair_prompt = load_system_prompt(settings.reply_repair_prompt_path)
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
        extraction_interval_messages=settings.memory_extraction_interval_messages,
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
    admin_commands = AdminCommandHandler(
        memory_service,
        memory_processor,
        context_debugger,
        style_repository=style_repository,
        style_export_path=settings.style_export_path,
    )
    return CoreRuntime(
        settings=settings,
        database=database,
        style_repository=style_repository,
        memory_service=memory_service,
        memory_processor=memory_processor,
        context_debugger=context_debugger,
        dispatcher=dispatcher,
        merger=merger,
        admin_commands=admin_commands,
        proactive_prompt=proactive_prompt,
        followup_prompt=followup_prompt,
    )
