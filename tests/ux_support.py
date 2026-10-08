from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

from app.adapters.qq import QQAdapter
from app.core.dispatcher import ContinuousMessageMerger, MessageDispatcher
from app.core.memory import InMemoryStore
from app.core.reply_engine import ReplyEngine
from app.core.session import SessionManager
from app.memory.context import MemoryContextProvider
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.storage.database import Database
from app.style.repository import StyleRepository


class FakeModel:
    def __init__(self, *replies):
        self.calls = []
        self.replies = list(replies)
        self.block = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, messages):
        self.calls.append(tuple(messages))
        if self.block:
            self.started.set()
            await self.release.wait()
        return self.replies.pop(0) if self.replies else "合成回复"


class FakeSender:
    def __init__(self):
        self.calls = []
        self.error = None
        self.on_send = None

    async def send(self, message):
        if self.error:
            raise self.error
        self.calls.append(message)
        if self.on_send:
            self.on_send(message)
        return f"fake-out-{len(self.calls)}"


class FakeImages:
    async def resolve(self, part):
        return replace(part, resolved_url=part.url)


def system(path, *, database=None, model=None, **options):
    database = database or Database(path)
    database.initialize()
    model = model or FakeModel()
    sender = FakeSender()
    service = MemoryService(MemoryRepository(database))
    dispatcher = MessageDispatcher(SessionManager(), ReplyEngine(model, context_provider=MemoryContextProvider(service)), InMemoryStore(), database)
    merger = ContinuousMessageMerger(dispatcher, debounce_seconds=0)
    adapter = QQAdapter(merger=merger, database=database, style_repository=StyleRepository(database),
        image_resolver=FakeImages(), primary_contact_id=None, primary_contact_name="合成联系人", training_group_ids=(),
        memory_service=service, dry_run=False, sender=sender, sink=lambda _: None,
        proactive_prompt="合成主动指令", followup_prompt="合成追加指令",
        wall_clock=lambda: datetime(2026, 10, 7, 12, 0), **options)
    return SimpleNamespace(db=database, model=model, sender=sender, service=service, dispatcher=dispatcher, merger=merger, adapter=adapter)


def event(mid, text="合成消息", *, account="10001", group=None, sender="20002", mention=False, image=False):
    parts = []
    if mention:
        parts.append({"type": "at", "data": {"qq": account}})
    if image:
        parts.append({"type": "image", "data": {"file": "fake.jpg", "url": "https://invalid.test/fake.jpg"}})
    if text:
        parts.append({"type": "text", "data": {"text": text}})
    result = {"post_type": "message", "message_type": "group" if group else "private", "self_id": account,
              "user_id": sender, "message_id": mid, "time": 1791331200,
              "sender": {"nickname": "合成联系人", "user_id": sender}, "message": parts}
    if group:
        result["group_id"] = group
    return result
