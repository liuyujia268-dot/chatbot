"""Async QQ runtime hosted behind the Tkinter event queue."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.adapters.image_resolver import ImageResolver
from app.adapters.onebot import ParsedQQEvent, parse_onebot_event
from app.adapters.outbound import MessageSender, NapCatMessageSender
from app.adapters.qq import QQAdapter
from app.adapters.websocket_client import OneBotWebSocketEvents
from app.config import PROJECT_ROOT, Settings
from app.core.platform import OutboundMessage, PlatformMessage
from app.core.identity import conversation_key
from app.runtime import CoreRuntime, build_core_runtime
from app.ui.settings_store import update_env_file
from app.ui.napcat_connection import NapCatConnectionProbe, NapCatConnectionState
from app.ui.napcat_webui import is_local_webui
from app.adapters.delivery import DeliveryService
from app.core.send_guard import submission_guard, check_submission, OutboundNotSubmittedError


logger = logging.getLogger(__name__)
_LOGIN_UNSET = object()


@dataclass(frozen=True, slots=True)
class UiEvent:
    kind: str
    payload: Any = None
    runtime_id: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeControls:
    dry_run: bool = True
    auto_reply: bool = True
    proactive: bool = False
    followup: bool = True

    @classmethod
    def from_settings(cls, settings: Settings) -> "RuntimeControls":
        return cls(settings.qq_dry_run, settings.qq_auto_reply_enabled,
                   settings.qq_proactive_enabled, settings.qq_followup_enabled)


def parse_onebot_history_events(
    events: list[Any],
    *,
    account_id: str,
    conversation_id: str,
) -> tuple[ParsedQQEvent, ...]:
    """Normalize NapCat history records for the desktop projection only."""
    if conversation_id.startswith("qq:private:"):
        conversation_type = "private"
        peer_id = conversation_id.removeprefix("qq:private:")
    elif conversation_id.startswith("qq:group:"):
        conversation_type = "group"
        peer_id = conversation_id.removeprefix("qq:group:")
    else:
        return ()
    parsed_events: list[ParsedQQEvent] = []
    for raw in events:
        if not isinstance(raw, dict):
            continue
        event = dict(raw)
        event.setdefault("self_id", account_id)
        event.setdefault("message_type", conversation_type)
        if conversation_type == "private":
            if event.get("post_type") == "message_sent":
                event["target_id"] = peer_id
        else:
            event.setdefault("group_id", peer_id)
        parsed = parse_onebot_event(event)
        if (
            parsed is not None
            and parsed.message.account_id == account_id
            and parsed.message.conversation_id == conversation_id
        ):
            parsed_events.append(parsed)
    parsed_events.sort(
        key=lambda item: (
            item.message.timestamp,
            item.message.message_id,
        )
    )
    return tuple(parsed_events)


class _ObservableEventSource:
    def __init__(self, source, callback) -> None:
        self._source = source
        self._callback = callback

    async def __aiter__(self):
        async for event in self._source:
            if self._callback(event) is not False:
                yield event


class _SwitchableSender:
    """Runtime Dry Run gate with a UI notification after every safe send."""

    def __init__(
        self,
        live_sender: MessageSender,
        callback,
        *,
        dry_run: bool,
    ) -> None:
        self._live_sender = live_sender
        self._callback = callback
        self.dry_run = dry_run
        self.allowed = lambda message: True
        self.records: list[OutboundMessage] = []

    async def send(self, message: OutboundMessage) -> str | None:
        if not self.allowed(message):
            raise OutboundNotSubmittedError("当前账号或连接已变更；消息未提交")
        message_id: str | None = None
        dry_run = self.dry_run
        outer_guard = submission_guard.get()
        token = submission_guard.set(lambda: self.allowed(message) and self.dry_run == dry_run
                                     and (outer_guard is None or outer_guard()))
        try:
            check_submission()
            if dry_run:
                self.records.append(message)
            else:
                message_id = await self._live_sender.send(message)
        finally:
            submission_guard.reset(token)
        self._callback(message, dry_run, message_id)
        return message_id


class DesktopRuntime:
    """Own an asyncio loop in a worker thread while Tk owns the main thread."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._controls = RuntimeControls.from_settings(settings)
        self._retired = False
        self.events: queue.Queue[UiEvent] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown: asyncio.Event | None = None
        self._core: CoreRuntime | None = None
        self._qq: QQAdapter | None = None
        self._ai_sender: _SwitchableSender | None = None
        self._manual_sender: _SwitchableSender | None = None
        self._onebot: OneBotWebSocketEvents | None = None
        self._ai_generated_ids: set[tuple[str, str, str]] = set()
        self._account_id: str | None = None
        self._last_confirmed_account_id: str | None = None
        self._switch_away_from_account: str | None = None
        self._directory_task: asyncio.Task[None] | None = None
        self._directory_account: tuple[int, str] | None = None
        self._account_watch_task: asyncio.Task[None] | None = None
        self._connection_probe_task: asyncio.Task[None] | None = None
        self._connection_probe = None
        self._webui_account_id: str | None = None
        self._webui_login_ready: bool | None = None
        self._account_check_wakeup = asyncio.Event()
        self._account_check_seconds = 10.0
        self._pending_account_events: deque[dict[str, Any]] = deque(maxlen=100)
        self._replay_tasks: set[asyncio.Task[None]] = set()
        self._history_inflight: set[str] = set()
        self._qq_restart_seconds = 3.0
        self._delivery: DeliveryService | None = None
        self._connection_epoch = 0
        self._login_confirmed_epoch = -1
        self._directory_retry_seconds = 3.0
        self._connection_state = 'stopped'
        self.runtime_id = uuid4().hex

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def account_id(self) -> str | None:
        return self._account_id

    @property
    def last_confirmed_account_id(self) -> str | None:
        return self._last_confirmed_account_id

    def require_account_change(self, previous_account_id: str | None) -> None:
        if self.running:
            raise RuntimeError('cannot change account requirement on a running runtime')
        self._switch_away_from_account = previous_account_id or None

    @property
    def connected(self) -> bool:
        return (not self._retired and self._connection_state == 'connected' and bool(self._account_id)
                and self._login_confirmed_epoch == self._connection_epoch
                and self._webui_login_ready is not False
                and (self._webui_account_id is None or self._webui_account_id == self._account_id))

    def _outbound_allowed(self, message: OutboundMessage) -> bool:
        return (not self._retired and message.account_id == self._account_id
                and (self._onebot is None or self.connected))

    @property
    def controls(self) -> RuntimeControls:
        return self._controls

    def restore_controls(self, controls: RuntimeControls) -> None:
        if self.running:
            raise RuntimeError("cannot restore controls on a running runtime")
        self._controls = controls

    def start(self) -> None:
        if self.running:
            return
        self._thread = threading.Thread(
            target=self._thread_main,
            name="chatbot-qq-runtime",
            daemon=True,
        )
        self._thread.start()

    def request_stop(self) -> None:
        """Invalidate sends immediately, before waiting for the worker to finish."""
        self._retired = True
        loop = self._loop
        shutdown = self._shutdown
        if loop is not None and shutdown is not None:
            try:
                loop.call_soon_threadsafe(shutdown.set)
            except RuntimeError:
                pass

    def stop(self, timeout_seconds: float = 3.0) -> bool:
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout=timeout_seconds)
        return not self.running

    def send_message(self, conversation_id: str, text: str, *, account_id: str | None = "") -> bool:
        clean = text.strip()
        if not clean:
            return False
        if (self._retired or self._loop is None or self._core is None or self._manual_sender is None
                or not self._account_id or (self._onebot is not None and not self.connected)):
            self._emit('send_error', 'QQ 连接尚未就绪，正文已保留在当前草稿中')
            return False
        account_id = self._account_id if account_id == '' else account_id
        if account_id != self._account_id:
            self._emit('send_error', '账号已变化，请重新选择当前账号会话；正文保留在原会话草稿中')
            return False
        return self._submit(self._send_manual(conversation_id, clean, account_id=account_id))

    def retry_delivery(self, record_id: str) -> None:
        self._submit(self._retry_delivery(record_id, self._account_id))

    def load_history(self, conversation_id: str, *, account_id: str | None = "") -> None:
        account_id = self._account_id if account_id == '' else account_id
        self._submit(self._load_history(conversation_id, account_id=account_id))

    def set_auto_reply(self, enabled: bool) -> None:
        self._call_qq("set_auto_reply_enabled", enabled)

    def set_proactive(self, enabled: bool) -> None:
        self._call_qq("set_proactive_enabled", enabled)

    def set_followup(self, enabled: bool) -> None:
        self._call_qq("set_followup_enabled", enabled)

    def set_dry_run(self, enabled: bool) -> None:
        def apply() -> None:
            if self._ai_sender is not None:
                self._ai_sender.dry_run = bool(enabled)
            if self._manual_sender is not None:
                self._manual_sender.dry_run = bool(enabled)
            self._controls = replace(self._controls, dry_run=bool(enabled))
            if self._core is not None:
                self._core.merger.set_simulation_mode(bool(enabled))
            self._emit("dry_run", bool(enabled))
            self._emit("controls", self._controls)

        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(apply)

    def set_human_takeover(self, conversation_id: str, enabled: bool, *, account_id: str | None = "") -> None:
        account_id = self._account_id if account_id == '' else account_id
        def apply() -> None:
            if self._core is None:
                return
            if not account_id or account_id != self._account_id:
                return
            self._core.merger.set_human_takeover(
                conversation_key("qq", account_id, conversation_id), bool(enabled)
            )
            self._emit(
                "takeover",
                {"account_id": self._account_id, "conversation_id": conversation_id,
                 "enabled": bool(enabled)},
            )

        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(apply)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception as exc:
            logger.exception("desktop_runtime_failed")
            self._emit("fatal", str(exc))

    async def _async_main(self) -> None:
        if self._retired:
            return  # exit may have been requested before the worker even started
        self._loop = asyncio.get_running_loop()
        self._shutdown = asyncio.Event()
        self._core = build_core_runtime(self.settings)
        self._core.admin_commands.account_provider = lambda: f'qq:{self._account_id}' if self._account_id else None
        self._delivery = DeliveryService(self._core.database, callback=lambda record: self._emit('delivery', record),
                                          on_history=self._core.merger.accept_delivered_history)
        self._delivery.recover_pending()
        onebot = OneBotWebSocketEvents(
            self.settings.qq_ws_url,
            self.settings.qq_ws_token,
            status_callback=self._on_status,
        )
        self._onebot = onebot
        if is_local_webui(self.settings.qq_webui_url) and is_local_webui(self.settings.qq_ws_url):
            self._connection_probe = NapCatConnectionProbe(
                self.settings.qq_webui_url, token=self.settings.qq_webui_token,
                config_path=self.settings.qq_webui_config_path,
                preferred_ws_url=self.settings.qq_ws_url,
            )
            self._connection_probe_task = asyncio.create_task(self._watch_connection_config())
        ai_live = NapCatMessageSender(
            onebot,
            on_sent=lambda account_id, message_id: self._ai_generated_ids.add(
                ("qq", account_id, message_id)
            ),
        )
        manual_live = NapCatMessageSender(onebot)
        self._ai_sender = _SwitchableSender(
            ai_live,
            lambda *args: None,
            dry_run=self._controls.dry_run,
        )
        self._manual_sender = _SwitchableSender(
            manual_live,
            lambda *args: None,
            dry_run=self._controls.dry_run,
        )
        for sender in (self._ai_sender, self._manual_sender):
            sender.allowed = self._outbound_allowed
        event_source = _ObservableEventSource(onebot, self._on_raw_event)
        self._qq = QQAdapter(
            merger=self._core.merger,
            database=self._core.database,
            style_repository=self._core.style_repository,
            image_resolver=ImageResolver(
                max_bytes=self.settings.qq_image_max_bytes,
                timeout_seconds=self.settings.qq_image_timeout_seconds,
                allow_remote_url=self.settings.qq_image_allow_remote_url,
            ),
            primary_contact_id=self.settings.qq_primary_contact_id,
            primary_contact_name=self.settings.qq_primary_contact_name,
            admin_contact_ids=self.settings.qq_admin_contact_ids,
            admin_memory_prefix=self.settings.qq_admin_memory_prefix,
            admin_config_prefix=self.settings.qq_admin_config_prefix,
            admin_config_writer=lambda updates: update_env_file(
                PROJECT_ROOT / ".env",
                updates,
            ),
            memory_service=self._core.memory_service,
            profile_asset_root=PROJECT_ROOT,
            training_group_ids=self.settings.qq_training_group_ids,
            reply_group_ids=self.settings.qq_reply_group_ids,
            dry_run=False,
            auto_reply_enabled=self._controls.auto_reply,
            proactive_enabled=self._controls.proactive,
            proactive_contact_ids=self.settings.qq_proactive_contact_ids,
            proactive_exclude_contact_ids=(
                self.settings.qq_proactive_exclude_contact_ids
            ),
            proactive_min_seconds=self.settings.qq_proactive_min_seconds,
            proactive_max_seconds=self.settings.qq_proactive_max_seconds,
            proactive_daily_limit=self.settings.qq_proactive_daily_limit,
            proactive_recent_days=self.settings.qq_proactive_recent_days,
            proactive_quiet_start=self.settings.qq_proactive_quiet_start,
            proactive_quiet_end=self.settings.qq_proactive_quiet_end,
            proactive_prompt=self._core.proactive_prompt,
            followup_enabled=self._controls.followup,
            followup_probability=self.settings.qq_followup_probability,
            followup_min_delay_seconds=(
                self.settings.qq_followup_min_delay_seconds
            ),
            followup_max_delay_seconds=(
                self.settings.qq_followup_max_delay_seconds
            ),
            followup_prompt=self._core.followup_prompt,
            event_source=event_source,
            sender=self._ai_sender,
            sink=lambda text: logger.info("qq_ui_event %s", text.splitlines()[0]),
            ai_generated_message_ids=self._ai_generated_ids,
            event_callback=self._on_adapter_event,
            image_wait_seconds=self.settings.qq_image_timeout_seconds,
        )
        self._emit("runtime_ready")
        self._emit("controls", self._controls)
        if self._retired:
            self._shutdown.set()
        try:
            await self._supervise_qq()
        finally:
            pending = [task for task in (self._directory_task, self._account_watch_task, self._connection_probe_task)
                       if task is not None] + list(self._replay_tasks)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._pending_account_events.clear()
            try:
                await asyncio.wait_for(self._core.dispatcher.finish_background_tasks(), timeout=2.0)
            except TimeoutError:
                logger.info('desktop_shutdown_cancelled_unfinished_maintenance')
            self._emit("status", ("stopped", None))

    async def _supervise_qq(self) -> None:
        """Keep the QQ event task alive until the desktop runtime stops."""
        assert self._qq is not None
        assert self._shutdown is not None
        while not self._shutdown.is_set():
            qq_task = asyncio.create_task(self._qq.run())
            shutdown_task = asyncio.create_task(self._shutdown.wait())
            done, _ = await asyncio.wait(
                (qq_task, shutdown_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if shutdown_task in done:
                qq_task.cancel()
                await asyncio.gather(qq_task, return_exceptions=True)
                return
            shutdown_task.cancel()
            await asyncio.gather(shutdown_task, return_exceptions=True)
            try:
                qq_task.result()
            except asyncio.CancelledError:
                if self._shutdown.is_set():
                    return
                logger.warning("qq_runtime_task_cancelled_unexpectedly")
            except Exception:
                logger.exception("qq_runtime_task_failed")
            else:
                logger.warning("qq_runtime_task_stopped_unexpectedly")
            self._on_status("error", "QQ 连接任务已退出，正在自动重建")
            try:
                await asyncio.wait_for(
                    self._shutdown.wait(),
                    timeout=self._qq_restart_seconds,
                )
            except TimeoutError:
                continue
            return

    def _on_status(self, status: str, detail: str | None) -> None:
        if self._retired:
            return
        self._connection_state = status
        self._emit("status", (status, detail))
        if status in {'connecting', 'connected', 'disconnected', 'error', 'stopped'}:
            self._login_confirmed_epoch = -1
            self._connection_epoch += 1
            if self._directory_task is not None:
                self._directory_task.cancel()
                self._directory_task = None
            if self._account_watch_task is not None:
                self._account_watch_task.cancel()
                self._account_watch_task = None
            self._pending_account_events.clear()
            self._suspend_account('等待重新确认 QQ 登录账号')
        if status == "connected":
            self._directory_task = asyncio.create_task(self._load_directory_with_retry(self._connection_epoch))

    def _suspend_account(self, hint: str) -> None:
        had_account = self._account_id is not None
        self._login_confirmed_epoch = -1
        self._account_id = None
        self._directory_account = None
        if self._qq is not None:
            self._qq.activate_account(None)  # invalidate old generations and proactive targets
        if had_account:
            self._emit('account_waiting', {'message': hint, 'connection_epoch': self._connection_epoch})

    async def _watch_connection_config(self) -> None:
        """Works while OneBot is unavailable, including manual NapCat switches."""
        while not self._retired:
            try:
                snapshot = await asyncio.to_thread(self._connection_probe.inspect)
                self._apply_connection_snapshot(snapshot)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('napcat_connection_probe_failed error_type=%s', type(exc).__name__)
            delay = self._account_check_seconds if self.connected else self._directory_retry_seconds
            if self._shutdown is None:
                await asyncio.sleep(delay)
            else:
                try:
                    await asyncio.wait_for(self._shutdown.wait(), timeout=delay)
                    return
                except TimeoutError:
                    pass

    def _apply_connection_snapshot(self, state: NapCatConnectionState) -> None:
        if self._retired or self._onebot is None:
            return
        if not state.available:
            # Older/remote WebUI versions can still use explicitly configured
            # OneBot. A failed HTTP probe must not overwrite a healthy socket.
            if not self.connected:
                self._emit('directory_error', state.hint)
            return
        previous = self._webui_account_id
        self._webui_account_id = state.account_id
        self._webui_login_ready = state.ready
        if not state.ready or state.account_id != self._account_id:
            self._suspend_account(state.hint or '检测到 NapCat 换号，重新连接并确认新账号')
        endpoint = state.endpoint
        self._onebot.configure_connection(
            endpoint.url if endpoint is not None else self._onebot.url,
            endpoint.token if endpoint is not None else self._onebot.token,
            enabled=state.ready,
            force=state.ready and previous is not None and previous != state.account_id,
        )
        if state.hint:
            self._emit('directory_error', state.hint)

    async def _watch_account(self, epoch: int) -> None:
        """Account changes are possible even without a WebSocket reconnect."""
        while not self._retired and epoch == self._connection_epoch:
            directory_ready = self._directory_account == (epoch, self._account_id)
            delay = self._account_check_seconds if self.connected and directory_ready else self._directory_retry_seconds
            try:
                await asyncio.wait_for(self._account_check_wakeup.wait(), timeout=delay)
            except TimeoutError:
                pass
            self._account_check_wakeup.clear()
            if self._retired or epoch != self._connection_epoch:
                return
            if self._directory_task is not None and not self._directory_task.done():
                continue
            await self._check_account_once(epoch)

    async def _check_account_once(self, epoch: int) -> bool:
        if self._onebot is None or self._retired or epoch != self._connection_epoch:
            return False
        try:
            login = await self._onebot.call_action('get_login_info', {})
            if self._retired or epoch != self._connection_epoch:
                return False
            account_id = str(login.get('user_id', '')).strip() if isinstance(login, dict) else ''
            if (self.connected and account_id == self._account_id
                    and self._directory_account == (epoch, account_id)):
                return True  # no repeated friend/group reload or UI rebuilding
            return await self._load_directory(epoch, login=login)
        except Exception as exc:
            if not self._retired and epoch == self._connection_epoch:
                self._suspend_account('QQ 登录已失效，等待登录后重新确认账号')
                logger.warning('qq_account_check_failed error_type=%s', type(exc).__name__)
                self._emit('directory_error', '账号确认暂时失败，已暂停回复；请检查 QQ 登录及 OneBot 配置')
            return False

    async def _load_directory_with_retry(self, epoch: int) -> None:
        """QQ may not be logged in yet even when the WebSocket is connected."""
        while not self._retired and epoch == self._connection_epoch:
            if await self._load_directory(epoch):
                return
            await asyncio.sleep(self._directory_retry_seconds)

    def _on_adapter_event(self, kind: str, payload) -> None:
        if kind == 'adapter_controls':
            self._controls = RuntimeControls(**payload)
            self._emit('controls', self._controls)
        else:
            self._emit(kind, payload)

    async def _load_directory(self, epoch: int | None = None, *, login=_LOGIN_UNSET) -> bool:
        client = self._onebot
        if client is None:
            return False
        confirmed = False
        try:
            if login is _LOGIN_UNSET:
                login = await client.call_action("get_login_info", {})
            if not isinstance(login, dict):
                raise RuntimeError("NapCat 未返回登录账号信息")
            account_id = str(login.get("user_id", "")).strip()
            if not account_id.isdecimal() or int(account_id) <= 0:
                raise RuntimeError("NapCat 当前没有已登录 QQ 账号")
            if self._retired or (epoch is not None and epoch != self._connection_epoch):
                return False
            if self._webui_login_ready is False or (self._webui_account_id and self._webui_account_id != account_id):
                self._suspend_account('OneBot 账号与 NapCat 当前登录不一致，等待新账号连接')
                self._emit('directory_error', '正在确认换号后的 OneBot 服务，旧账号已停止处理和发送')
                return False
            if account_id == self._switch_away_from_account:
                self._suspend_account('等待登录其他 QQ 账号')
                self._emit('directory_error', '仍检测到原 QQ 账号，请在 NapCat 登录其他账号；想恢复原账号可点击“重新连接”')
                return False  # the launcher may have quick-logged the cached old QQ account
            assert self._core is not None
            # A legacy qq:private:<self> record may belong to another account.
            # Hide self from this directory; never delete unassigned records.
            self._account_id = account_id
            self._last_confirmed_account_id = account_id
            self._switch_away_from_account = None
            self._login_confirmed_epoch = self._connection_epoch
            self._directory_account = None
            confirmed = True
            if self._qq is not None:
                self._qq.activate_account(account_id)
            self._emit(
                "login",
                {
                    "account_id": account_id,
                    "nickname": str(login.get("nickname", "")).strip(),
                    "takeovers": self._takeovers_for(account_id),
                    "connection_epoch": self._connection_epoch,
                },
            )
            self._replay_account_events(account_id)
            friends = await client.call_action("get_friend_list", {})
            groups = await client.call_action("get_group_list", {})
            if self._retired or account_id != self._account_id or (epoch is not None and epoch != self._connection_epoch):
                return False
            friend_items = friends if isinstance(friends, list) else []
            friend_items = [
                item
                for item in friend_items
                if not isinstance(item, dict)
                or str(item.get("user_id", "")).strip() != account_id
            ]
            self._emit(
                "directory",
                {
                    "account_id": account_id,
                    "connection_epoch": self._connection_epoch,
                    "friends": friend_items,
                    "groups": groups if isinstance(groups, list) else [],
                },
            )
            self._directory_account = (self._connection_epoch, account_id)
            if self._connection_state == 'connected' and (self._account_watch_task is None or self._account_watch_task.done()):
                self._account_check_wakeup.clear()
                self._account_watch_task = asyncio.create_task(self._watch_account(self._connection_epoch))
            return True
        except Exception as exc:
            if self._retired or (epoch is not None and epoch != self._connection_epoch):
                return False
            if not confirmed:
                self._suspend_account('QQ 登录尚未就绪，已暂停旧账号回复')
            logger.warning('qq_directory_load_failed error_type=%s', type(exc).__name__)
            hint = ('NapCat 连接暂时中断，正在重试；请确认 QQ 已重新登录且 OneBot 已启用'
                    if isinstance(exc, (ConnectionError, OSError, TimeoutError))
                    else 'QQ 登录/联系人尚未就绪，正在重试；可在登录后点击“重新连接”')
            self._emit('directory_error', hint)
            return False

    async def _load_history(self, conversation_id: str, *, account_id: str | None = "") -> None:
        client = self._onebot
        account_id = self._account_id if account_id == "" else account_id
        if (
            client is None
            or not account_id
            or (account_id, conversation_id) in self._history_inflight
            or account_id != self._account_id or self._retired
        ):
            return
        if conversation_id == f"qq:private:{account_id}":
            self._emit(
                "history_error",
                {
                    "account_id": account_id,
                    "conversation_id": conversation_id,
                },
            )
            return
        if conversation_id.startswith("qq:private:"):
            action = "get_friend_msg_history"
            identifier_name = "user_id"
            peer_id = conversation_id.removeprefix("qq:private:")
        elif conversation_id.startswith("qq:group:"):
            action = "get_group_msg_history"
            identifier_name = "group_id"
            peer_id = conversation_id.removeprefix("qq:group:")
        else:
            return
        self._history_inflight.add((account_id, conversation_id))
        try:
            data = await client.call_action(
                action,
                {
                    identifier_name: peer_id,
                    "message_seq": "0",
                    "count": self.settings.desktop_history_message_limit,
                    "reverse_order": False,
                    "disable_get_url": False,
                },
            )
            raw_messages = (
                data.get("messages", [])
                if isinstance(data, dict)
                else []
            )
            messages = parse_onebot_history_events(
                raw_messages if isinstance(raw_messages, list) else [],
                account_id=account_id,
                conversation_id=conversation_id,
            )
            if self._retired or account_id != self._account_id:
                return  # history from the old login is not a new account's history
            self._emit(
                "history",
                {
                    "account_id": account_id,
                    "conversation_id": conversation_id,
                    "messages": messages,
                },
            )
        except Exception as exc:
            logger.warning(
                "qq_history_load_failed conversation=%s error_type=%s",
                conversation_id,
                type(exc).__name__,
            )
            self._emit(
                "history_error",
                {
                    "account_id": account_id,
                    "conversation_id": conversation_id,
                },
            )
        finally:
            self._history_inflight.discard((account_id, conversation_id))

    def _on_raw_event(self, event: dict[str, Any]) -> bool | None:
        if self._retired:
            return False
        parsed = parse_onebot_event(event)
        event_account = str(event.get('self_id', '')).strip()
        if self._onebot is not None and (not self.connected or (event_account and event_account != self._account_id)):
            if self.connected:
                self._suspend_account('检测到账号变化，正在确认新账号；暂不处理聊天或发送')
            if parsed is not None:
                if len(self._pending_account_events) == self._pending_account_events.maxlen:
                    logger.warning('qq_account_pending_overflow oldest_event_discarded')
                self._pending_account_events.append(event)
            self._account_check_wakeup.set()
            return False  # also block heartbeat/lifecycle events from activating an unconfirmed QQ account
        if parsed is not None:
            if self._account_id and self._account_id != parsed.message.account_id:
                # The confirmed login, not a delayed message, owns account switching.
                return False
            self._account_id = parsed.message.account_id
            if self._qq is not None:
                self._qq.activate_account(self._account_id)
            key = parsed.message.deduplication_key
            source_type = (
                "ai_generated"
                if key in self._ai_generated_ids
                else ("human_self" if parsed.message.is_self else "other_user")
            )
            self._emit(
                "message",
                {"parsed": parsed, "source_type": source_type},
            )

    def _replay_account_events(self, account_id: str) -> None:
        if self._qq is None:
            return
        pending = tuple(self._pending_account_events)
        self._pending_account_events.clear()
        for event in pending:
            if str(event.get('self_id', '')).strip() != account_id:
                continue  # never guess ownership or replay another account's event
            if self._on_raw_event(event) is not False:
                task = asyncio.create_task(self._qq._handle_event_safely(event))
                self._replay_tasks.add(task)
                task.add_done_callback(self._replay_tasks.discard)

    def _on_outbound(
        self,
        message: OutboundMessage,
        dry_run: bool,
        message_id: str | None,
    ) -> None:
        self._emit(
            "outbound",
            {
                "message": message,
                "dry_run": dry_run,
                "event_key": (
                    f"platform:{message_id}"
                    if message_id
                    else f"out:{uuid4().hex}"
                ),
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    async def _send_manual(self, conversation_id: str, text: str, *, account_id: str | None = "") -> None:
        sender = self._manual_sender
        account_id = self._account_id if account_id == "" else account_id
        if sender is None or not account_id:
            self._emit("send_error", "QQ 尚未登录或 WebSocket 未连接")
            return
        assert self._core is not None
        self._core.merger.interrupt_current_reply(
            conversation_key("qq", account_id, conversation_id)
        )
        message = OutboundMessage(
            platform="qq",
            account_id=account_id,
            conversation_id=conversation_id,
            text=text,
            metadata={"source_type": "human_self", "sender_name": "我"},
        )
        if self._delivery is None:
            self._delivery = DeliveryService(self._core.database, callback=lambda record: self._emit('delivery', record),
                                              on_history=self._core.merger.accept_delivered_history)
        record_id = self._delivery.prepare(message)
        if conversation_id == f"qq:private:{account_id}":
            self._delivery._state(record_id, 'failed', 'invalid_target', '不能向当前登录的 QQ 账号自己发送私聊；可复制正文')
            return
        await self._delivery.send(record_id, sender, guard=self._outbound_allowed)

    async def _retry_delivery(self, record_id: str, account_id: str | None) -> None:
        if self._delivery is None or self._manual_sender is None:
            return
        record = self._delivery.get(record_id)
        if record['account_id'] != account_id or record['metadata_json'].find('human_self') < 0:
            return
        if record['status'] != 'failed':
            return
        await self._delivery.send(record_id, self._manual_sender, guard=self._outbound_allowed)

    def _call_qq(self, method: str, enabled: bool) -> None:
        def apply() -> None:
            if self._qq is not None:
                getattr(self._qq, method)(bool(enabled))
                field = {"set_auto_reply_enabled": "auto_reply",
                         "set_proactive_enabled": "proactive",
                         "set_followup_enabled": "followup"}[method]
                self._controls = replace(self._controls, **{field: bool(enabled)})
                self._emit("controls", self._controls)

        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(apply)

    def _submit(self, coroutine) -> bool:
        loop = self._loop
        if loop is None:
            coroutine.close()
            self._emit("send_error", "QQ 连接尚未初始化")
            return False
        try:
            asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
            self._emit('send_error', 'QQ 运行连接已结束，正文已保留在当前草稿中')
            return False
        return True

    def _emit(self, kind: str, payload: Any = None) -> None:
        self.events.put(UiEvent(kind=kind, payload=payload, runtime_id=self.runtime_id))

    def _takeovers_for(self, account_id: str) -> tuple[str, ...]:
        if self._core is None:
            return ()
        prefix = f"scope:qq:{account_id}:"
        with self._core.database.connect() as connection:
            rows = connection.execute(
                "SELECT session_id FROM conversation_controls WHERE human_takeover = 1 "
                "AND substr(session_id, 1, ?) = ?", (len(prefix), prefix)
            ).fetchall()
        return tuple(str(row[0])[len(prefix):] for row in rows)
