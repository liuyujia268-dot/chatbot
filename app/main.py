"""Application entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from app.adapters.console import ConsoleAdapter
from app.adapters.image_resolver import ImageResolver
from app.adapters.outbound import DryRunMessageSender, NapCatMessageSender
from app.adapters.qq import QQAdapter
from app.adapters.websocket_client import OneBotWebSocketEvents
from app.config import ConfigurationError, Settings
from app.runtime import build_core_runtime


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
    core = build_core_runtime(settings)
    logger.info(
        "application_started provider=%s model=%s",
        settings.model_provider,
        settings.model_name or "mock",
    )
    if not settings.qq_enabled:
        console = ConsoleAdapter(
            dispatcher=core.dispatcher,
            merger=core.merger,
            admin_commands=core.admin_commands,
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
        dispatcher=core.dispatcher,
        merger=core.merger,
        admin_commands=core.admin_commands,
        dry_run=settings.outbound_dry_run,
        qq_mode=True,
        qq_sender=manual_sender,
        qq_dry_run=settings.qq_dry_run,
        qq_primary_contact_id=settings.qq_primary_contact_id,
        qq_primary_contact_name=settings.qq_primary_contact_name,
    )
    qq = QQAdapter(
        merger=core.merger,
        database=core.database,
        style_repository=core.style_repository,
        image_resolver=ImageResolver(
            max_bytes=settings.qq_image_max_bytes,
            timeout_seconds=settings.qq_image_timeout_seconds,
            allow_remote_url=settings.qq_image_allow_remote_url,
        ),
        primary_contact_id=settings.qq_primary_contact_id,
        primary_contact_name=settings.qq_primary_contact_name,
        admin_contact_ids=settings.qq_admin_contact_ids,
        admin_memory_prefix=settings.qq_admin_memory_prefix,
        memory_service=core.memory_service,
        training_group_ids=settings.qq_training_group_ids,
        reply_group_ids=settings.qq_reply_group_ids,
        dry_run=settings.qq_dry_run,
        auto_reply_enabled=settings.qq_auto_reply_enabled,
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
        proactive_prompt=core.proactive_prompt,
        followup_enabled=settings.qq_followup_enabled,
        followup_probability=settings.qq_followup_probability,
        followup_min_delay_seconds=(
            settings.qq_followup_min_delay_seconds
        ),
        followup_max_delay_seconds=(
            settings.qq_followup_max_delay_seconds
        ),
        followup_prompt=core.followup_prompt,
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
