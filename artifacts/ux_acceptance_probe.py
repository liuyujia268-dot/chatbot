"""Disposable UX acceptance probes: synthetic data, no live transports."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import contextmanager, ExitStack
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.adapters.admin_commands import AdminCommandHandler
from app.adapters.qq import QQAdapter
from app.config import ConfigurationError, Settings
from app.core.dispatcher import ContinuousMessageMerger, MessageDispatcher
from app.core.memory import InMemoryStore
from app.core.platform import OutboundMessage
from app.core.reply_engine import ReplyEngine
from app.core.session import Message, SessionManager
from app.memory.context import ContextDebugger, MemoryContextProvider
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.models.base import ModelTimeoutError
from app.storage.database import Database
from app.storage.schema import SCHEMA_SQL
from app.style.repository import StyleRepository
from app.ui.desktop import DesktopApp
from app.ui.runtime import DesktopRuntime, _SwitchableSender
from app.ui.settings_dialog import SettingsDialog
from app.ui.settings_store import read_env_values
import app.ui.desktop as desktop_module


class MemoryDatabase(Database):
    def __init__(self):
        super().__init__(Path("unused-ux-audit-memory.db"))
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def connect(self):
        yield self.connection
        self.connection.commit()

    def initialize(self):
        self.connection.executescript(SCHEMA_SQL)


class FakeModel:
    def __init__(self):
        self.calls = []
        self.block = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, messages):
        self.calls.append(tuple(messages))
        if self.block:
            self.started.set()
            await self.release.wait()
        return "合成测试回复。"


class FakeSender:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def send(self, message):
        if self.fail:
            raise RuntimeError("synthetic send failure")
        self.calls.append(message)
        return "fake-out-" + str(len(self.calls))


class FakeImages:
    def __init__(self):
        self.block = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def resolve(self, part):
        if self.block:
            self.started.set()
            await self.release.wait()
        return replace(part, resolved_url=part.url)


def system(*, proactive=False, fail=False, groups=(), database=None):
    db = database or MemoryDatabase()
    db.initialize()
    model, sender, images = FakeModel(), FakeSender(fail), FakeImages()
    service = MemoryService(MemoryRepository(db))
    dispatcher = MessageDispatcher(
        SessionManager(),
        ReplyEngine(model, context_provider=MemoryContextProvider(service)),
        InMemoryStore(), db,
    )
    merger = ContinuousMessageMerger(dispatcher, debounce_seconds=0)
    adapter = QQAdapter(
        merger=merger, database=db, style_repository=StyleRepository(db),
        image_resolver=images, primary_contact_id=None,
        primary_contact_name="合成联系人", training_group_ids=(),
        reply_group_ids=groups, memory_service=service,
        dry_run=False, sender=sender, sink=lambda _: None,
        proactive_enabled=proactive, proactive_contact_ids=("20002",),
        proactive_prompt="合成主动聊天指令",
        wall_clock=lambda: datetime(2026, 10, 7, 12, 0),
    )
    return SimpleNamespace(db=db, model=model, sender=sender, images=images,
                           service=service, dispatcher=dispatcher,
                           merger=merger, adapter=adapter)


def event(mid, text="合成消息", *, account="10001", group=None,
          mention=False, image=False, sender="20002"):
    parts = []
    if mention:
        parts.append({"type": "at", "data": {"qq": account}})
    if image:
        parts.append({"type": "image", "data": {
            "file": "audit.jpg", "url": "https://audit.invalid/image.jpg"}})
    if text:
        parts.append({"type": "text", "data": {"text": text}})
    result = {
        "post_type": "message", "message_type": "group" if group else "private",
        "self_id": account, "user_id": sender, "message_id": mid,
        "time": 1791331200, "sender": {"nickname": "合成联系人"},
        "message": parts,
    }
    if group:
        result["group_id"] = group
    return result


class Value:
    def __init__(self, value=None, **kwargs):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Widget:
    def __init__(self, *args, **kwargs):
        self.options = kwargs
        self.selected = ("qq:private:20002",)
        self.callbacks = []
        self.scrolls = []

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def winfo_exists(self):
        return True

    def winfo_children(self):
        return []

    def destroy(self):
        pass

    def pack(self, *args, **kwargs):
        pass

    def grid(self, *args, **kwargs):
        pass

    def bind(self, *args, **kwargs):
        pass

    def add(self, *args, **kwargs):
        pass

    def selection(self):
        return self.selected

    def set(self, *args):
        pass

    def after(self, delay, callback):
        self.callbacks.append(callback)

    def yview_moveto(self, value):
        self.scrolls.append(value)


class Input:
    def __init__(self, text=""):
        self.text = text

    def get(self, *args):
        return self.text

    def delete(self, *args):
        self.text = ""


@contextmanager
def fake_widgets():
    with ExitStack() as stack:
        for name in ("Frame", "Label", "Button", "Checkbutton", "Panedwindow"):
            stack.enter_context(patch.object(desktop_module.ttk, name, Widget))
        stack.enter_context(patch.object(desktop_module.tk, "BooleanVar", Value))
        stack.enter_context(patch.object(desktop_module, "Tooltip", lambda *a: None))
        yield


def view(runtime=None):
    app = DesktopApp.__new__(DesktopApp)
    app.root = Widget()
    app._main = Widget()
    app.settings = Settings()
    app._configuration_error = None
    app.runtime = runtime
    app._account_id = "10001"
    app._login_name = "合成账号"
    app._current = SimpleNamespace(account_id="10001", conversation_id="qq:private:20002",
                                   display_name="联系人 A", peer_id="20002", pinned=False)
    app._items = {app._current.conversation_id: app._current,
                  "qq:private:20003": SimpleNamespace(account_id="10001",
                      conversation_id="qq:private:20003", display_name="联系人 B",
                      peer_id="20003", pinned=False)}
    app._history_loaded, app._history_inflight = set(), set()
    app._takeover_conversations = set()
    app._webui_opened = False
    app._toolbar_widgets = []
    app._takeover_var = Value(False)
    app._takeover_widget = Widget()
    app._status, app._tree, app._chat_title = Widget(), Widget(), Widget()
    app._pin_button, app._send_button, app._input = Widget(), Widget(), Input()
    app._message_frame, app._chat_canvas = Widget(), Widget()
    app._suppress_selection = False
    app.repository = SimpleNamespace(mark_read=lambda **kw: None,
                                     list_messages=lambda **kw: [])
    app._refresh_conversations = lambda: None
    app._refresh_messages = lambda: None
    app._request_history = lambda _: None
    app._update_navigation_controls = lambda: None
    app._build_conversation_list = lambda _: None
    app._build_chat = lambda _: None
    return app


def test_U01_dry_run_display_can_disagree_after_home_roundtrip():
    async def scenario():
        live = FakeSender()
        runtime = DesktopRuntime(Settings())
        runtime._loop = SimpleNamespace(call_soon_threadsafe=lambda f: f())
        runtime._ai_sender = _SwitchableSender(live, lambda *a: None, dry_run=True)
        runtime._manual_sender = _SwitchableSender(live, lambda *a: None, dry_run=True)
        app = view(runtime)
        with fake_widgets(), patch.object(desktop_module.messagebox, "askyesno", return_value=True):
            app._build_toolbar()
            app._dry_var.set(False)
            app._toggle_dry()
            assert runtime._ai_sender.dry_run is False
            app._show_home()
            app._show_qq()
            assert app._dry_var.get() is True
        await runtime._ai_sender.send(OutboundMessage("qq", "10001", "qq:private:20002", "合成回复"))
        assert len(live.calls) == 1
    asyncio.run(scenario())


def test_U02_account_switch_leaks_prior_account_into_model_request():
    async def scenario():
        s = system()
        await s.adapter.handle_event(event("a", "ACCOUNT_A_TEST_SECRET"))
        restarted = system(database=s.db)
        await restarted.adapter.handle_event(event("b", "账号 B 消息", account="10002"))
        assert "ACCOUNT_A_TEST_SECRET" in repr(restarted.model.calls[-1])
    asyncio.run(scenario())


def test_U03_reconnect_retains_checked_takeover_but_rebuilds_unprotected_runtime():
    class Runtime:
        def __init__(self, settings):
            self.settings = settings
            self.core = system()
        def start(self):
            pass
        def stop(self):
            pass
    app = view(Runtime(Settings()))
    app.runtime.core.merger.set_human_takeover("qq:private:20002", True)
    app._takeover_conversations.add("qq:private:20002")
    with patch.object(desktop_module, "DesktopRuntime", Runtime):
        app._replace_qq_runtime()
    app._activate_account("10001", "合成账号")
    item = SimpleNamespace(account_id="10001", conversation_id="qq:private:20002",
                           display_name="联系人 A", peer_id="20002", pinned=False)
    app._items[item.conversation_id] = item
    app._select_conversation()
    assert app._takeover_var.get() is True
    assert app.runtime.core.merger.is_human_takeover(item.conversation_id) is False


def test_U04_failed_send_is_saved_as_assistant_and_duplicate_is_not_retried():
    async def scenario():
        s = system(fail=True)
        incoming = event("failed-send")
        try:
            await s.adapter.handle_event(incoming)
        except RuntimeError:
            pass
        history = s.db.list_messages("qq:private:20002")
        assert any(m.role == "assistant" for m in history)
        assert not s.sender.calls
        count = len(s.model.calls)
        await s.adapter.handle_event(incoming)
        assert len(s.model.calls) == count
    asyncio.run(scenario())


def test_U05_proactive_switch_off_during_generation_still_sends():
    async def scenario():
        s = system(proactive=True)
        await s.adapter.handle_event(event("seed"))
        s.sender.calls.clear()
        s.model.block = True
        s.adapter._next_proactive_at["qq:private:20002"] = 0
        task = asyncio.create_task(s.adapter.trigger_proactive_once())
        await asyncio.wait_for(s.model.started.wait(), 1)
        s.adapter.set_proactive_enabled(False)
        s.model.release.set()
        await asyncio.wait_for(task, 1)
        assert not s.adapter.proactive_enabled
        assert len(s.sender.calls) == 1
    asyncio.run(scenario())


def test_U06_unrelated_group_chatter_cancels_mentioned_reply():
    async def scenario():
        s = system(groups=("90001",))
        s.model.block = True
        task = asyncio.create_task(s.adapter.handle_event(event("mention", group="90001", mention=True)))
        await asyncio.wait_for(s.model.started.wait(), 1)
        await s.adapter.handle_event(event("chatter", "群友无关消息", group="90001", sender="20003"))
        result = await asyncio.wait_for(task, 1)
        assert result.reply is None
        assert not s.sender.calls
    asyncio.run(scenario())


def test_U07_slow_image_allows_later_text_to_overtake_and_reply_twice():
    async def scenario():
        s = system()
        s.merger = ContinuousMessageMerger(s.dispatcher, debounce_seconds=1.2)
        s.adapter._merger = s.merger
        s.images.block = True
        task = asyncio.create_task(s.adapter.handle_event(event("image", text="", image=True)))
        await asyncio.wait_for(s.images.started.wait(), 1)
        await s.adapter.handle_event(event("caption", "这是什么"))
        s.images.release.set()
        await asyncio.wait_for(task, 3)
        user_texts = [m.content for m in s.db.list_messages("qq:private:20002") if m.role == "user"]
        assert user_texts == ["这是什么", "[图片]"]
        assert len(s.model.calls) == len(s.sender.calls) == 2
    asyncio.run(scenario())


def test_U08_switching_contact_keeps_another_contacts_draft():
    sends = []
    app = view(SimpleNamespace(send_message=lambda c, t: sends.append((c, t))))
    app._input.text = "仅准备发给联系人 A 的合成草稿"
    app._tree.selected = ("qq:private:20003",)
    app._select_conversation()
    app._send()
    assert sends == [("qq:private:20003", "仅准备发给联系人 A 的合成草稿")]


def test_U09_manual_send_failure_clears_input_without_restore():
    class DeferredRuntime(DesktopRuntime):
        def _submit(self, coroutine):
            self.pending = coroutine
    async def scenario():
        s = system()
        runtime = DeferredRuntime(Settings())
        runtime._account_id = "10001"
        runtime._core = SimpleNamespace(database=s.db, merger=s.merger)
        runtime._manual_sender = FakeSender(fail=True)
        app = view(runtime)
        app._input.text = "需要保留的合成输入"
        app._send()
        assert app._input.text == ""
        await runtime.pending
        assert runtime.events.get_nowait().kind == "send_error"
        assert app._input.text == ""
    asyncio.run(scenario())


def test_U10_desktop_model_timeout_has_no_ui_error_event():
    async def fail(messages):
        raise ModelTimeoutError("合成模型超时")
    async def scenario():
        s = system()
        s.model.generate = fail
        runtime = DesktopRuntime(Settings())
        s.adapter._sink = lambda text: logging.getLogger("ux-probe").info(text.splitlines()[0])
        incoming = event("timeout")
        runtime._on_raw_event(incoming)
        await s.adapter._handle_event_safely(incoming)
        kinds = []
        while not runtime.events.empty():
            kinds.append(runtime.events.get_nowait().kind)
        assert kinds == ["message"]
    asyncio.run(scenario())


def test_U11_invalid_model_url_is_written_before_success_dialog(tmp_path):
    values = {
        "MODEL_PROVIDER": "openai_compatible", "MODEL_BASE_URL": "not-a-url",
        "MODEL_API_KEY": "synthetic-not-a-real-key", "MODEL_NAME": "fake-model",
        "MODEL_TIMEOUT_SECONDS": "1", "MESSAGE_DEBOUNCE_SECONDS": "1",
        "MODEL_TEMPERATURE": "0.7", "MODEL_MAX_TOKENS": "20",
        "CONTEXT_MAX_MESSAGES": "12", "MODEL_MAX_OUTPUT_CHARS": "100",
        "QQ_WEBUI_URL": "http://127.0.0.1:1", "QQ_WS_URL": "ws://127.0.0.1:1",
        "DESKTOP_HISTORY_MESSAGE_LIMIT": "10",
    }
    errors = []
    target = tmp_path / "isolated.env"
    def reload():
        try:
            Settings.from_env(env_file=target, environ={})
        except ConfigurationError as exc:
            errors.append(type(exc).__name__)
    dialog = SimpleNamespace(_entries={k: Value(v) for k, v in values.items()},
                             _booleans={}, _values={}, _env_path=target,
                             _on_saved=reload, destroy=lambda: None)
    dialog._validate = lambda updates: SettingsDialog._validate(dialog, updates)
    with patch("app.ui.settings_dialog.messagebox.showinfo") as success:
        SettingsDialog._save(dialog)
        assert success.call_count == 1
    assert errors == ["ConfigurationError"]
    assert read_env_values(target)["MODEL_BASE_URL"] == "not-a-url"


def test_U12_message_refresh_forces_scroll_to_bottom():
    app = view()
    app.repository = SimpleNamespace(list_messages=lambda **kw: [object()])
    app._render_message = lambda _: None
    DesktopApp._refresh_messages(app)
    for callback in app.root.callbacks:
        callback()
    assert app._chat_canvas.scrolls == [1.0]


def test_U13_qq_memory_name_and_bare_id_do_not_resolve_to_qq_session():
    async def scenario():
        s = system()
        s.db.save_message("qq:private:20002", Message("user", "合成原始记录"))
        output = []
        handler = AdminCommandHandler(s.service, None, ContextDebugger(), sink=output.append)
        await handler.handle("/memory messages 合成联系人")
        await handler.handle("/memory messages 20002")
        assert output == ["[原始消息] 暂无", "[原始消息] 暂无"]
        await handler.handle("/memory messages qq:private:20002")
        assert "合成原始记录" in output[-1]
    asyncio.run(scenario())


def test_U14_dry_run_outbound_projection_discards_dry_run_flag():
    records = []
    app = view()
    app.repository = SimpleNamespace(upsert_conversation=lambda **kw: None,
                                     record_message=lambda **kw: records.append(kw))
    app._record_outbound({
        "message": OutboundMessage("qq", "10001", "qq:private:20002", "未发送候选"),
        "dry_run": True, "event_key": "out:synthetic",
        "created_at": "2026-10-07T12:00:00+00:00",
    })
    assert records[0]["source_type"] == "ai_generated"
    assert "dry_run" not in records[0]
    assert records[0]["parts_json"] == '[{"type": "text", "text": "未发送候选"}]'


def test_positive_private_contact_isolation_still_holds():
    async def scenario():
        s = system()
        await s.adapter.handle_event(event("a", "CONTACT_A_MARKER"))
        await s.adapter.handle_event(event("b", "联系人 B", sender="20003"))
        assert "CONTACT_A_MARKER" not in repr(s.model.calls[-1])
    asyncio.run(scenario())


def test_positive_switchable_dry_run_never_calls_live_sender():
    async def scenario():
        live = FakeSender()
        sender = _SwitchableSender(live, lambda *a: None, dry_run=True)
        await sender.send(OutboundMessage("qq", "10001", "qq:private:20002", "候选"))
        assert not live.calls and len(sender.records) == 1
    asyncio.run(scenario())
