"""Application entry point."""

from __future__ import annotations

import asyncio
import sys

from app.adapters.console import ConsoleAdapter
from app.config import Settings
from app.core.dispatcher import ContinuousMessageMerger, MessageDispatcher
from app.core.memory import InMemoryStore
from app.core.reply_engine import ReplyEngine
from app.core.session import SessionManager
from app.models.mock import MockModel
from app.storage.database import Database


def _configure_console_encoding() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


async def async_main() -> None:
    settings = Settings.from_env()
    sessions = SessionManager()
    memory = InMemoryStore()
    database = Database(settings.database_path)
    database.initialize()
    dispatcher = MessageDispatcher(
        sessions=sessions,
        engine=ReplyEngine(model=MockModel()),
        memory=memory,
        database=database,
    )
    merger = ContinuousMessageMerger(
        dispatcher=dispatcher,
        merge_window_seconds=settings.merge_window_seconds,
    )
    await ConsoleAdapter(dispatcher=dispatcher, merger=merger).run()


def run() -> None:
    _configure_console_encoding()
    asyncio.run(async_main())


if __name__ == "__main__":
    run()
