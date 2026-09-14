"""Tkinter desktop client for the existing QQ chatbot runtime."""

from __future__ import annotations

import base64
import json
import logging
import queue
import threading
import tkinter as tk
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from app.adapters.onebot import ParsedQQEvent
from app.config import ConfigurationError, PROJECT_ROOT, Settings
from app.storage.database import Database
from app.ui.napcat_webui import (
    find_napcat_launcher,
    is_local_webui,
    resolve_webui_launch,
    should_launch_webui,
    start_napcat_launcher,
    wait_for_webui,
    webui_is_ready,
)
from app.ui.repository import ConversationItem, DesktopMessage, DesktopRepository
from app.ui.runtime import DesktopRuntime, UiEvent
from app.ui.settings_dialog import SettingsDialog


logger = logging.getLogger(__name__)


class Tooltip:
    def __init__(self, widget: tk.Misc, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add=True)
        widget.bind("<Leave>", self._hide, add=True)

    def _show(self, _event=None) -> None:
        if self.window is not None:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.window,
            text=self.text,
            background="#111827",
            foreground="white",
            padx=9,
            pady=5,
        ).pack()

    def _hide(self, _event=None) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None


class DesktopApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Chatbot")
        self.root.geometry("1160x760")
        self.root.minsize(900, 620)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._set_window_icon()
        self._style = ttk.Style(root)
        if "vista" in self._style.theme_names():
            self._style.theme_use("vista")
        self._style.configure("Conversation.Treeview", rowheight=54)

        self._env_path = PROJECT_ROOT / ".env"
        self.settings, self._configuration_error = self._load_settings()
        database = Database(self.settings.database_path)
        database.initialize()
        self.repository = DesktopRepository(database)
        self.repository.initialize()
        self.runtime: DesktopRuntime | None = None
        self._account_id = ""
        self._login_name = ""
        self._current: ConversationItem | None = None
        self._items: dict[str, ConversationItem] = {}
        self._drag_item: str | None = None
        self._suppress_selection = False
        self._photo_refs: dict[str, tk.PhotoImage] = {}
        self._avatar_attempted: set[str] = set()
        self._default_avatars = {
            "private": self._solid_icon("#bfdbfe"),
            "group": self._solid_icon("#bbf7d0"),
        }
        self._media_results: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._toolbar_widgets: list[tk.Widget] = []
        self._takeover_conversations: set[str] = set()
        self._history_loaded: set[tuple[str, str]] = set()
        self._history_inflight: set[tuple[str, str]] = set()
        self._webui_starting = False
        self._webui_opened = False
        self._main = ttk.Frame(root)
        self._main.pack(fill="both", expand=True)
        self._show_home()
        self.root.after(120, self._poll_events)

    def _set_window_icon(self) -> None:
        icon = tk.PhotoImage(width=32, height=32)
        icon.put("#2563eb", to=(0, 0, 32, 32))
        icon.put("#ffffff", to=(7, 8, 25, 23))
        icon.put("#2563eb", to=(11, 12, 14, 15))
        icon.put("#2563eb", to=(18, 12, 21, 15))
        self.root.iconphoto(True, icon)
        self._window_icon = icon

    @staticmethod
    def _solid_icon(color: str) -> tk.PhotoImage:
        image = tk.PhotoImage(width=38, height=38)
        image.put(color, to=(2, 2, 36, 36))
        image.put("#ffffff", to=(11, 9, 27, 24))
        image.put("#ffffff", to=(7, 25, 31, 33))
        return image

    def _load_settings(self) -> tuple[Settings, str | None]:
        try:
            return Settings.from_env(), None
        except ConfigurationError as exc:
            return Settings(), str(exc)

    def _clear(self) -> None:
        for child in self._main.winfo_children():
            child.destroy()

    def _show_home(self) -> None:
        self._clear()
        header = ttk.Frame(self._main, padding=(28, 22))
        header.pack(fill="x")
        ttk.Label(header, text="Chatbot", font=("Microsoft YaHei UI", 20, "bold")).pack(side="left")
        ttk.Button(header, text="设置", command=self._open_settings).pack(side="right")

        body = ttk.Frame(self._main, padding=50)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="选择要连接的平台",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(pady=(55, 28))
        cards = ttk.Frame(body)
        cards.pack()
        qq = ttk.Button(cards, text="QQ\n通过 NapCat / OneBot 连接", command=self._open_qq, width=32)
        qq.grid(row=0, column=0, padx=16, ipadx=15, ipady=34)
        wechat = ttk.Button(cards, text="微信\n暂未实现", command=self._wechat_unavailable, width=32)
        wechat.grid(row=0, column=1, padx=16, ipadx=15, ipady=34)
        if self._configuration_error:
            ttk.Label(
                body,
                text=f"当前配置需要处理：{self._configuration_error}",
                foreground="#b91c1c",
                wraplength=700,
            ).pack(pady=28)

    def _wechat_unavailable(self) -> None:
        messagebox.showinfo("微信", "微信接入尚未实现。", parent=self.root)

    def _open_qq(self) -> None:
        if self._configuration_error:
            messagebox.showwarning(
                "配置尚未完成",
                f"请先在设置中修正配置：\n{self._configuration_error}",
                parent=self.root,
            )
            self._open_settings()
            return
        should_start = self.runtime is None or not self.runtime.running
        self._show_qq()
        if should_start:
            self.runtime = DesktopRuntime(self.settings)
            self.runtime.start()
        if should_launch_webui(
            starting=self._webui_starting,
            opened=self._webui_opened,
            account_id=self._account_id,
        ):
            self._begin_webui_launch()

    def _retry_qq_login(self) -> None:
        if self._webui_starting:
            messagebox.showinfo(
                "NapCat WebUI",
                "NapCat 正在启动，请稍候。",
                parent=self.root,
            )
            return
        self._webui_opened = False
        self._begin_webui_launch()

    def _begin_webui_launch(self) -> None:
        launch = resolve_webui_launch(
            self.settings.qq_webui_url,
            explicit_token=self.settings.qq_webui_token,
            explicit_config_path=self.settings.qq_webui_config_path,
        )
        self._webui_starting = True

        def worker() -> None:
            if is_local_webui(launch.url) and not webui_is_ready(launch.url):
                launcher = find_napcat_launcher(launch.config_path)
                if launcher is None:
                    self._media_results.put(
                        (
                            self._webui_launch_failed,
                            "没有找到 NapCat 启动脚本，请在设置中填写 webui.json 的完整路径。",
                        )
                    )
                    return
                try:
                    start_napcat_launcher(launcher)
                except OSError as exc:
                    logger.error(
                        "napcat_start_failed error_type=%s",
                        type(exc).__name__,
                    )
                    self._media_results.put(
                        (self._webui_launch_failed, "NapCat 启动失败，请检查安装目录。")
                    )
                    return
                if not wait_for_webui(launch.url):
                    self._media_results.put(
                        (
                            self._webui_launch_failed,
                            "NapCat 启动后 45 秒内 WebUI 仍未就绪，请检查 QQ 登录窗口。",
                        )
                    )
                    return
            self._media_results.put((self._open_resolved_webui, launch))

        threading.Thread(
            target=worker,
            name="chatbot-napcat-startup",
            daemon=True,
        ).start()

    def _open_resolved_webui(self, launch) -> None:
        self._webui_starting = False
        try:
            opened = webbrowser.open(launch.url, new=2)
        except Exception as exc:
            logger.error(
                "napcat_webui_open_failed error_type=%s",
                type(exc).__name__,
            )
            opened = False
        self._webui_opened = bool(opened)
        if not opened:
            self._webui_launch_failed("无法打开系统浏览器中的 NapCat WebUI。")
            return
        if not launch.authenticated:
            messagebox.showwarning(
                "NapCat WebUI Token 未找到",
                (
                    "WebUI 已就绪，但没有找到登录 Token。\n\n"
                    "请在“设置 → QQ 与界面”填写 WebUI Token，"
                    "或填写 webui.json 的完整路径。\n"
                    "注意：它和 OneBot WebSocket Token 不是同一个值。"
                ),
                parent=self.root,
            )
        self.root.after(15000, self._show_account_connection_hint)

    def _webui_launch_failed(self, message: str) -> None:
        self._webui_starting = False
        self._webui_opened = False
        messagebox.showerror("NapCat WebUI", message, parent=self.root)

    def _show_account_connection_hint(self) -> None:
        if self._account_id:
            return
        if hasattr(self, "_status") and self._status.winfo_exists():
            self._status.configure(
                text="等待 OneBot；换号后需为该账号单独启用 WebSocket",
                foreground="#b45309",
            )

    def _show_qq(self) -> None:
        self._clear()
        self._build_toolbar()
        paned = ttk.Panedwindow(self._main, orient="horizontal")
        paned.pack(fill="both", expand=True)
        left = ttk.Frame(paned, padding=(10, 8))
        right = ttk.Frame(paned, padding=(14, 8))
        paned.add(left, weight=1)
        paned.add(right, weight=3)
        self._build_conversation_list(left)
        self._build_chat(right)
        connected = bool(self._account_id)
        self._set_connected_controls(connected)
        if connected:
            self._status.configure(
                text=f"已登录：{self._login_name or self._account_id}",
                foreground="#15803d",
            )
            self._refresh_conversations()

    def _build_toolbar(self) -> None:
        self._toolbar_widgets = []
        bar = ttk.Frame(self._main, padding=(12, 9))
        bar.pack(fill="x")
        ttk.Button(bar, text="首页", command=self._show_home).pack(side="left", padx=(0, 10))
        self._status = ttk.Label(bar, text="QQ 登录中…", foreground="#b45309")
        self._status.pack(side="left", padx=(0, 18))

        self._auto_var = tk.BooleanVar(value=self.settings.qq_auto_reply_enabled)
        self._proactive_var = tk.BooleanVar(value=self.settings.qq_proactive_enabled)
        self._followup_var = tk.BooleanVar(value=self.settings.qq_followup_enabled)
        self._dry_var = tk.BooleanVar(value=self.settings.qq_dry_run)
        switches = (
            ("自动回复", self._auto_var, self._toggle_auto, "关闭后仍保存消息，但不调用聊天模型。"),
            ("主动聊天", self._proactive_var, self._toggle_proactive, "控制随机主动私聊和白名单群主动消息。"),
            ("追加回复", self._followup_var, self._toggle_followup, "控制首条回复后概率追加第二条。"),
            ("Dry Run", self._dry_var, self._toggle_dry, "开启时只记录候选发送，绝不调用 QQ 发送接口。"),
        )
        for text, variable, command, help_text in switches:
            widget = ttk.Checkbutton(bar, text=text, variable=variable, command=command)
            widget.pack(side="left", padx=7)
            Tooltip(widget, help_text)
            self._toolbar_widgets.append(widget)
        self._takeover_var = tk.BooleanVar(value=False)
        self._takeover_widget = ttk.Checkbutton(
            bar,
            text="人工接管",
            variable=self._takeover_var,
            command=self._toggle_takeover,
            state="disabled",
        )
        self._takeover_widget.pack(side="left", padx=7)
        Tooltip(
            self._takeover_widget,
            "持续接管当前会话；未勾选时，人工消息只取消当轮回复。",
        )
        settings_button = ttk.Button(bar, text="设置", command=self._open_settings)
        settings_button.pack(side="right")
        Tooltip(settings_button, "设置始终可用；模型与连接参数重连后生效。")
        retry_button = ttk.Button(
            bar,
            text="重新登录",
            command=self._retry_qq_login,
        )
        retry_button.pack(side="right", padx=(0, 10))
        Tooltip(
            retry_button,
            "重新打开带 Token 的 NapCat 登录页；登录失败或换 QQ 账号时使用。",
        )

    def _build_conversation_list(self, parent: ttk.Frame) -> None:
        title = ttk.Frame(parent)
        title.pack(fill="x", pady=(0, 7))
        ttk.Label(title, text="会话", font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        self._pin_button = ttk.Button(title, text="置顶", command=self._toggle_pin, state="disabled")
        self._pin_button.pack(side="right")
        self._tree = ttk.Treeview(
            parent,
            columns=("unread", "drag"),
            show="tree headings",
            selectmode="browse",
            style="Conversation.Treeview",
        )
        self._tree.heading("#0", text="联系人 / 群聊")
        self._tree.heading("unread", text="未读")
        self._tree.heading("drag", text="排序")
        self._tree.column("#0", width=255, stretch=True)
        self._tree.column("unread", width=52, anchor="center", stretch=False)
        self._tree.column("drag", width=44, anchor="center", stretch=False)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        self._tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._tree.insert("", "end", iid="category:private", text="联系人", open=True)
        self._tree.insert("", "end", iid="category:group", text="群聊", open=True)
        self._tree.bind("<<TreeviewSelect>>", self._select_conversation)
        self._tree.bind("<ButtonPress-1>", self._drag_start, add=True)
        self._tree.bind("<ButtonRelease-1>", self._drag_end, add=True)

    def _build_chat(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.pack(fill="x", pady=(2, 9))
        self._chat_title = ttk.Label(
            header,
            text="选择左侧会话",
            font=("Microsoft YaHei UI", 13, "bold"),
        )
        self._chat_title.pack(side="left", anchor="w")
        self._history_status = ttk.Label(
            header,
            text="",
            foreground="#6b7280",
        )
        self._history_status.pack(side="left", padx=(10, 0))
        navigation = ttk.Frame(header)
        navigation.pack(side="right")
        self._navigation_buttons: dict[str, ttk.Button] = {}
        navigation_items = (
            ("previous_group", "<<", lambda: self._navigate_group(-1), "切换到上一分组"),
            ("previous", "<", lambda: self._navigate_conversation(-1), "当前分组的上一个会话"),
            ("next", ">", lambda: self._navigate_conversation(1), "当前分组的下一个会话"),
            ("next_group", ">>", lambda: self._navigate_group(1), "切换到下一分组"),
        )
        for key, label, command, help_text in navigation_items:
            button = ttk.Button(
                navigation,
                text=label,
                width=3,
                command=command,
                state="disabled",
            )
            button.pack(side="left", padx=2)
            Tooltip(button, help_text)
            self._navigation_buttons[key] = button
        self._chat_canvas = tk.Canvas(parent, highlightthickness=0, background="#f6f7f9")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self._chat_canvas.yview)
        self._message_frame = tk.Frame(self._chat_canvas, background="#f6f7f9")
        self._message_window = self._chat_canvas.create_window((0, 0), window=self._message_frame, anchor="nw")
        self._chat_canvas.configure(yscrollcommand=scrollbar.set)
        self._message_frame.bind("<Configure>", self._sync_scroll_region)
        self._chat_canvas.bind("<Configure>", self._resize_message_frame)
        self._chat_canvas.pack(side="top", fill="both", expand=True)
        scrollbar.place(in_=self._chat_canvas, relx=1.0, rely=0, relheight=1.0, anchor="ne")

        composer = ttk.Frame(parent, padding=(0, 10, 0, 0))
        composer.pack(fill="x")
        self._input = tk.Text(composer, height=3, wrap="word", font=("Microsoft YaHei UI", 10))
        self._input.pack(side="left", fill="x", expand=True)
        self._input.bind("<Control-Return>", self._send_from_event)
        self._send_button = ttk.Button(composer, text="发送\nCtrl+Enter", command=self._send, state="disabled")
        self._send_button.pack(side="right", padx=(10, 0), fill="y")

    def _sync_scroll_region(self, _event=None) -> None:
        self._chat_canvas.configure(scrollregion=self._chat_canvas.bbox("all"))

    def _resize_message_frame(self, event) -> None:
        self._chat_canvas.itemconfigure(self._message_window, width=event.width)

    def _poll_events(self) -> None:
        runtime = self.runtime
        if runtime is not None:
            while True:
                try:
                    event = runtime.events.get_nowait()
                except queue.Empty:
                    break
                self._handle_event(event)
        while True:
            try:
                callback, *args = self._media_results.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)
            except tk.TclError:
                pass
        self.root.after(120, self._poll_events)

    def _handle_event(self, event: UiEvent) -> None:
        if event.kind == "status":
            status, detail = event.payload
            labels = {
                "connecting": ("QQ 登录中…", "#b45309"),
                "connected": ("NapCat 已连接，正在确认账号…", "#2563eb"),
                "disconnected": ("连接已断开，正在重连…", "#b91c1c"),
                "error": ("连接失败，正在重试…", "#b91c1c"),
                "stopped": ("QQ 已断开", "#6b7280"),
            }
            text, color = labels.get(status, (str(status), "#6b7280"))
            if hasattr(self, "_status") and self._status.winfo_exists():
                self._status.configure(text=text, foreground=color)
            if status in {"disconnected", "error", "stopped"}:
                self._set_connected_controls(False)
            return
        if event.kind == "login":
            account_id = event.payload["account_id"]
            nickname = event.payload.get("nickname") or account_id
            self._activate_account(account_id, nickname)
            self._set_connected_controls(True)
            self._refresh_conversations()
            return
        if event.kind == "directory":
            self._load_directory(event.payload)
            return
        if event.kind == "message":
            self._record_inbound(event.payload)
            return
        if event.kind == "outbound":
            self._record_outbound(event.payload)
            return
        if event.kind == "takeover":
            self._set_takeover_state(
                str(event.payload["conversation_id"]),
                bool(event.payload["enabled"]),
            )
            return
        if event.kind == "history":
            self._record_history(event.payload)
            return
        if event.kind == "history_error":
            self._history_load_failed(event.payload)
            return
        if event.kind in {"fatal", "directory_error", "send_error"}:
            messagebox.showerror("QQ", str(event.payload), parent=self.root)

    def _activate_account(self, account_id: str, nickname: str | None) -> None:
        if self._account_id and self._account_id != account_id:
            self._current = None
            self._items.clear()
            self._takeover_conversations.clear()
            self._history_loaded.clear()
            self._history_inflight.clear()
            if hasattr(self, "_message_frame") and self._message_frame.winfo_exists():
                for child in self._message_frame.winfo_children():
                    child.destroy()
            if hasattr(self, "_chat_title") and self._chat_title.winfo_exists():
                self._chat_title.configure(text="选择左侧会话")
            if hasattr(self, "_send_button") and self._send_button.winfo_exists():
                self._send_button.configure(state="disabled")
            if hasattr(self, "_takeover_widget") and self._takeover_widget.winfo_exists():
                self._takeover_var.set(False)
                self._takeover_widget.configure(state="disabled")
        self._account_id = account_id
        self._login_name = nickname or account_id
        self._webui_opened = True
        if hasattr(self, "_status") and self._status.winfo_exists():
            self._status.configure(
                text=f"已登录：{self._login_name}（QQ：{account_id}）",
                foreground="#15803d",
            )

    def _load_directory(self, payload: dict[str, Any]) -> None:
        account_id = payload["account_id"]
        for friend in payload.get("friends", []):
            user_id = str(friend.get("user_id", "")).strip()
            if not user_id:
                continue
            nickname = str(friend.get("nickname", "")).strip() or None
            remark = str(friend.get("remark", "")).strip()
            self.repository.upsert_conversation(
                platform="qq",
                account_id=account_id,
                conversation_id=f"qq:private:{user_id}",
                conversation_type="private",
                peer_id=user_id,
                display_name=remark or nickname or user_id,
                nickname=nickname,
                avatar_url=f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=100",
            )
        for group in payload.get("groups", []):
            group_id = str(group.get("group_id", "")).strip()
            if not group_id:
                continue
            name = str(group.get("group_name", "")).strip() or f"群 {group_id}"
            self.repository.upsert_conversation(
                platform="qq",
                account_id=account_id,
                conversation_id=f"qq:group:{group_id}",
                conversation_type="group",
                peer_id=group_id,
                display_name=name,
                avatar_url=f"https://p.qlogo.cn/gh/{group_id}/{group_id}/100/",
            )
        self._refresh_conversations()

    def _record_inbound(self, payload: Any) -> None:
        source_type = "other_user"
        parsed = payload
        if isinstance(payload, dict):
            parsed = payload.get("parsed")
            source_type = str(payload.get("source_type", source_type))
        if not isinstance(parsed, ParsedQQEvent):
            return
        message = parsed.message
        if message.account_id and message.account_id != self._account_id:
            self._activate_account(message.account_id, None)
        if message.is_self and source_type == "ai_generated":
            return
        existing = self._items.get(message.conversation_id)
        display_name = existing.display_name if existing else (
            parsed.nickname or (f"群 {parsed.group_id}" if parsed.group_id else parsed.peer_id)
        )
        conversation_type = "group" if parsed.group_id else "private"
        self.repository.upsert_conversation(
            platform="qq",
            account_id=message.account_id,
            conversation_id=message.conversation_id,
            conversation_type=conversation_type,
            peer_id=parsed.group_id or parsed.peer_id,
            display_name=display_name,
            nickname=parsed.nickname if conversation_type == "private" else None,
            last_event_at=message.timestamp.isoformat(),
        )
        sender_name = "我" if message.is_self else (parsed.nickname or message.sender_id)
        current_id = self._current.conversation_id if self._current else None
        inserted = self.repository.record_message(
            platform="qq",
            account_id=message.account_id,
            conversation_id=message.conversation_id,
            event_key=f"platform:{message.message_id}",
            sender_id=message.sender_id,
            sender_name=sender_name,
            role="assistant" if message.is_self else "user",
            source_type=("human_self" if message.is_self else source_type),
            text=message.storage_text,
            parts_json=Database.serialize_message_parts(message),
            created_at=message.timestamp.isoformat(),
            increment_unread=(not message.is_self and current_id != message.conversation_id),
        )
        if inserted:
            self._refresh_conversations()
            if current_id == message.conversation_id:
                self._refresh_messages()

    def _record_outbound(self, payload: dict[str, Any]) -> None:
        message = payload["message"]
        source_type = str(message.metadata.get("source_type", "ai_generated"))
        existing = self._items.get(message.conversation_id)
        target_id = message.conversation_id.rpartition(":")[2]
        conversation_type = "group" if ":group:" in message.conversation_id else "private"
        self.repository.upsert_conversation(
            platform="qq",
            account_id=message.account_id,
            conversation_id=message.conversation_id,
            conversation_type=conversation_type,
            peer_id=target_id,
            display_name=existing.display_name if existing else target_id,
            last_event_at=payload["created_at"],
        )
        current_id = self._current.conversation_id if self._current else None
        inserted = self.repository.record_message(
            platform="qq",
            account_id=message.account_id,
            conversation_id=message.conversation_id,
            event_key=payload["event_key"],
            sender_id=message.account_id,
            sender_name=str(message.metadata.get("sender_name", "机器人")),
            role="assistant",
            source_type=source_type,
            text=message.text,
            parts_json=json.dumps([{"type": "text", "text": message.text}], ensure_ascii=False),
            created_at=payload["created_at"],
            increment_unread=(source_type == "ai_generated" and current_id != message.conversation_id),
        )
        if inserted:
            self._refresh_conversations()
            if current_id == message.conversation_id:
                self._refresh_messages()

    def _refresh_conversations(self) -> None:
        if not self._account_id or not hasattr(self, "_tree") or not self._tree.winfo_exists():
            return
        selected = self._current.conversation_id if self._current else None
        self._suppress_selection = True
        for category in ("private", "group"):
            parent = f"category:{category}"
            for item in self._tree.get_children(parent):
                self._tree.delete(item)
            conversations = self.repository.list_conversations(
                platform="qq",
                account_id=self._account_id,
                conversation_type=category,
            )
            for conversation in conversations:
                self._items[conversation.conversation_id] = conversation
                nickname = (
                    f"（{conversation.nickname}）"
                    if conversation.nickname and conversation.nickname != conversation.display_name
                    else ""
                )
                pin = "📌 " if conversation.pinned else ""
                identifier_label = (
                    "群号"
                    if conversation.conversation_type == "group"
                    else "QQ"
                )
                text = (
                    f"{pin}{conversation.display_name}{nickname}\n"
                    f"{identifier_label}：{conversation.peer_id}"
                )
                unread = str(conversation.unread_count) if conversation.unread_count else ""
                avatar_key = f"avatar:{conversation.conversation_id}"
                avatar = self._photo_refs.get(
                    avatar_key,
                    self._default_avatars[conversation.conversation_type],
                )
                self._tree.insert(
                    parent,
                    "end",
                    iid=conversation.conversation_id,
                    text=text,
                    image=avatar,
                    values=(unread, "☰"),
                )
                if conversation.avatar_url and avatar_key not in self._photo_refs:
                    self._request_avatar(conversation.conversation_id, conversation.avatar_url)
        if selected and self._tree.exists(selected):
            self._tree.selection_set(selected)
        self.root.after_idle(self._enable_selection_events)

    def _enable_selection_events(self) -> None:
        self._suppress_selection = False
        if (
            not hasattr(self, "_tree")
            or not self._tree.winfo_exists()
        ):
            return
        selection = self._tree.selection()
        if not selection or selection[0].startswith("category:"):
            self._update_navigation_controls()
            return
        current_id = self._current.conversation_id if self._current else None
        if selection[0] != current_id:
            self._select_conversation()
        else:
            self._update_navigation_controls()

    def _select_conversation(self, _event=None) -> None:
        if self._suppress_selection:
            return
        selection = self._tree.selection()
        if not selection or selection[0].startswith("category:"):
            return
        conversation = self._items.get(selection[0])
        if conversation is None:
            return
        self._current = conversation
        self.repository.mark_read(
            platform="qq",
            account_id=conversation.account_id,
            conversation_id=conversation.conversation_id,
        )
        self._chat_title.configure(text=f"{conversation.display_name}  ·  {conversation.peer_id}")
        self._pin_button.configure(text="取消置顶" if conversation.pinned else "置顶", state="normal")
        self._send_button.configure(state="normal")
        self._takeover_var.set(
            conversation.conversation_id in self._takeover_conversations
        )
        self._takeover_widget.configure(state="normal")
        self._tree.set(conversation.conversation_id, "unread", "")
        self._refresh_messages()
        self._request_history(conversation)
        self._update_navigation_controls()

    def _select_tree_conversation(self, conversation_id: str) -> None:
        if not self._tree.exists(conversation_id):
            return
        self._tree.selection_set(conversation_id)
        self._tree.focus(conversation_id)
        self._tree.see(conversation_id)
        self._select_conversation()

    def _navigate_conversation(self, direction: int) -> None:
        if self._current is None or direction not in {-1, 1}:
            return
        parent = self._tree.parent(self._current.conversation_id)
        siblings = list(self._tree.get_children(parent))
        try:
            target_index = siblings.index(self._current.conversation_id) + direction
        except ValueError:
            return
        if 0 <= target_index < len(siblings):
            self._select_tree_conversation(siblings[target_index])

    def _navigate_group(self, direction: int) -> None:
        if self._current is None or direction not in {-1, 1}:
            return
        groups = ("category:private", "category:group")
        current_group = self._tree.parent(self._current.conversation_id)
        try:
            group_index = groups.index(current_group)
        except ValueError:
            return
        next_index = group_index + direction
        while 0 <= next_index < len(groups):
            conversations = self._tree.get_children(groups[next_index])
            if conversations:
                self._select_tree_conversation(conversations[0])
                return
            next_index += direction

    def _update_navigation_controls(self) -> None:
        if not hasattr(self, "_navigation_buttons"):
            return
        states = {
            "previous_group": False,
            "previous": False,
            "next": False,
            "next_group": False,
        }
        if (
            self._current is not None
            and hasattr(self, "_tree")
            and self._tree.winfo_exists()
            and self._tree.exists(self._current.conversation_id)
        ):
            groups = ("category:private", "category:group")
            current_id = self._current.conversation_id
            current_group = self._tree.parent(current_id)
            siblings = list(self._tree.get_children(current_group))
            if current_id in siblings:
                index = siblings.index(current_id)
                states["previous"] = index > 0
                states["next"] = index + 1 < len(siblings)
            if current_group in groups:
                group_index = groups.index(current_group)
                states["previous_group"] = any(
                    self._tree.get_children(group)
                    for group in groups[:group_index]
                )
                states["next_group"] = any(
                    self._tree.get_children(group)
                    for group in groups[group_index + 1:]
                )
        for key, button in self._navigation_buttons.items():
            button.configure(state="normal" if states[key] else "disabled")

    def _request_history(self, conversation: ConversationItem) -> None:
        key = (conversation.account_id, conversation.conversation_id)
        if key in self._history_loaded:
            if hasattr(self, "_history_status") and self._history_status.winfo_exists():
                self._history_status.configure(text="")
            return
        if key in self._history_inflight:
            if hasattr(self, "_history_status") and self._history_status.winfo_exists():
                self._history_status.configure(
                    text="正在同步最近历史…",
                    foreground="#6b7280",
                )
            return
        if self.runtime is None or not self.runtime.running:
            return
        self._history_inflight.add(key)
        if hasattr(self, "_history_status") and self._history_status.winfo_exists():
            self._history_status.configure(
                text="正在同步最近历史…",
                foreground="#6b7280",
            )
        self.runtime.load_history(conversation.conversation_id)

    def _record_history(self, payload: dict[str, Any]) -> None:
        account_id = str(payload.get("account_id", ""))
        conversation_id = str(payload.get("conversation_id", ""))
        key = (account_id, conversation_id)
        self._history_inflight.discard(key)
        if not account_id or account_id != self._account_id:
            return
        messages = payload.get("messages", ())
        for parsed in messages:
            if not isinstance(parsed, ParsedQQEvent):
                continue
            message = parsed.message
            if message.conversation_id != conversation_id:
                continue
            self.repository.record_message(
                platform="qq",
                account_id=account_id,
                conversation_id=conversation_id,
                event_key=f"platform:{message.message_id}",
                sender_id=message.sender_id,
                sender_name=(
                    "我"
                    if message.is_self
                    else (parsed.nickname or message.sender_id)
                ),
                role="assistant" if message.is_self else "user",
                source_type="human_self" if message.is_self else "other_user",
                text=message.storage_text,
                parts_json=Database.serialize_message_parts(message),
                created_at=message.timestamp.isoformat(),
                increment_unread=False,
            )
        self._history_loaded.add(key)
        if (
            self._current is not None
            and self._current.account_id == account_id
            and self._current.conversation_id == conversation_id
        ):
            self._refresh_messages()
            if hasattr(self, "_history_status") and self._history_status.winfo_exists():
                self._history_status.configure(
                    text="最近历史已同步",
                    foreground="#15803d",
                )
                self.root.after(
                    1800,
                    lambda: self._clear_history_status(conversation_id),
                )

    def _history_load_failed(self, payload: dict[str, Any]) -> None:
        account_id = str(payload.get("account_id", ""))
        conversation_id = str(payload.get("conversation_id", ""))
        self._history_inflight.discard((account_id, conversation_id))
        if (
            self._current is not None
            and self._current.account_id == account_id
            and self._current.conversation_id == conversation_id
            and hasattr(self, "_history_status")
            and self._history_status.winfo_exists()
        ):
            self._history_status.configure(
                text="历史同步失败，点击会话可重试",
                foreground="#b45309",
            )

    def _clear_history_status(self, conversation_id: str) -> None:
        if (
            self._current is not None
            and self._current.conversation_id == conversation_id
            and hasattr(self, "_history_status")
            and self._history_status.winfo_exists()
        ):
            self._history_status.configure(text="")

    def _refresh_messages(self) -> None:
        if self._current is None:
            return
        conversation = self._current
        try:
            messages = self.repository.list_messages(
                platform="qq",
                account_id=conversation.account_id,
                conversation_id=conversation.conversation_id,
            )
        except Exception:
            logger.exception(
                "desktop_messages_load_failed conversation=%s",
                conversation.conversation_id,
            )
            if hasattr(self, "_history_status") and self._history_status.winfo_exists():
                self._history_status.configure(
                    text="消息加载失败，重新选择可重试",
                    foreground="#b91c1c",
                )
            return
        if (
            self._current is None
            or self._current.conversation_id != conversation.conversation_id
        ):
            return
        for child in self._message_frame.winfo_children():
            child.destroy()
        if not messages:
            history_key = (
                conversation.account_id,
                conversation.conversation_id,
            )
            tk.Label(
                self._message_frame,
                text=(
                    "暂无消息记录"
                    if history_key in self._history_loaded
                    else "暂无本地消息，正在尝试同步最近历史…"
                ),
                background="#f6f7f9",
                foreground="#6b7280",
                font=("Microsoft YaHei UI", 10),
            ).pack(pady=28)
        for message in messages:
            try:
                self._render_message(message)
            except (tk.TclError, TypeError, ValueError):
                logger.exception(
                    "desktop_message_render_failed message_id=%s",
                    message.id,
                )
        self.root.after(20, lambda: self._chat_canvas.yview_moveto(1.0))

    def _render_message(self, message: DesktopMessage) -> None:
        row = tk.Frame(self._message_frame, background="#f6f7f9")
        row.pack(fill="x", padx=12, pady=5)
        side = "right" if message.role == "assistant" else "left"
        bubble_color = "#dbeafe" if message.role == "assistant" else "#ffffff"
        bubble = tk.Frame(row, background=bubble_color, padx=11, pady=8)
        bubble.pack(side=side, anchor="e" if side == "right" else "w")
        tk.Label(
            bubble,
            text=message.sender_name,
            background=bubble_color,
            foreground="#6b7280",
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="e" if side == "right" else "w", pady=(0, 3))
        try:
            parts = json.loads(message.parts_json)
        except (json.JSONDecodeError, TypeError):
            parts = []
        if not isinstance(parts, list) or not parts:
            parts = [{"type": "text", "text": message.text}]
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image":
                self._render_image_part(bubble, bubble_color, part)
            else:
                text = str(part.get("text") or "")
                if self._is_expression(text):
                    self._render_expression_part(bubble, bubble_color, text)
                elif text:
                    tk.Label(
                        bubble,
                        text=text,
                        background=bubble_color,
                        justify="left",
                        wraplength=610,
                        font=("Microsoft YaHei UI", 10),
                    ).pack(anchor="w")

    @staticmethod
    def _is_expression(text: str) -> bool:
        stripped = text.strip()
        return stripped.startswith("[QQ表情") or stripped.startswith("[QQ商城表情")

    def _render_expression_part(self, parent: tk.Frame, color: str, text: str) -> None:
        button = tk.Button(
            parent,
            text="[表情，点击查看]",
            relief="flat",
            background=color,
            foreground="#2563eb",
            cursor="hand2",
        )
        button.configure(command=lambda: button.configure(text=text, foreground="#111827"))
        button.pack(anchor="w")
        if self.settings.desktop_auto_show_media:
            button.invoke()

    def _render_image_part(self, parent: tk.Frame, color: str, part: dict[str, Any]) -> None:
        summary = str(part.get("description") or part.get("summary") or "图片")
        url = str(part.get("resolved_url") or part.get("url") or "").strip()
        button = tk.Button(
            parent,
            text=f"[{summary}，点击查看]",
            relief="flat",
            background=color,
            foreground="#2563eb",
            cursor="hand2",
        )
        button.configure(command=lambda: self._load_inline_image(url, parent, button, summary))
        button.pack(anchor="w")
        if self.settings.desktop_auto_show_media:
            button.invoke()

    def _load_inline_image(self, url: str, parent: tk.Frame, button: tk.Button, summary: str) -> None:
        if not url:
            button.configure(text=f"[{summary}：没有可用图片地址]", state="disabled")
            return
        button.configure(text="[图片加载中…]", state="disabled")

        def worker() -> None:
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "ChatbotDesktop/0.1"})
                with urllib.request.urlopen(request, timeout=self.settings.qq_image_timeout_seconds) as response:
                    content_type = response.headers.get_content_type()
                    if not content_type.startswith("image/"):
                        raise ValueError("地址返回的不是图片")
                    data = response.read(self.settings.qq_image_max_bytes + 1)
                    if len(data) > self.settings.qq_image_max_bytes:
                        raise ValueError("图片超过大小限制")
                self._media_results.put((self._show_loaded_image, parent, button, data, url))
            except Exception as exc:
                self._media_results.put((self._show_image_fallback, button, url, str(exc)))

        threading.Thread(target=worker, name="chatbot-image-preview", daemon=True).start()

    def _request_avatar(self, conversation_id: str, url: str) -> None:
        if conversation_id in self._avatar_attempted:
            return
        self._avatar_attempted.add(conversation_id)

        def worker() -> None:
            data: bytes | None = None
            try:
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": "ChatbotDesktop/0.1"},
                )
                with urllib.request.urlopen(request, timeout=5.0) as response:
                    if response.headers.get_content_type().startswith("image/"):
                        data = response.read(1024 * 1024)
            except Exception:
                logger.debug(
                    "avatar_load_failed conversation=%s",
                    conversation_id,
                    exc_info=True,
                )
            self._media_results.put(
                (self._set_avatar, conversation_id, data)
            )

        threading.Thread(
            target=worker,
            name="chatbot-avatar-preview",
            daemon=True,
        ).start()

    def _set_avatar(self, conversation_id: str, data: bytes | None) -> None:
        if not data:
            return
        try:
            photo = tk.PhotoImage(
                data=base64.b64encode(data).decode("ascii")
            )
            factor = max(1, (max(photo.width(), photo.height()) + 37) // 38)
            if factor > 1:
                photo = photo.subsample(factor, factor)
            self._photo_refs[f"avatar:{conversation_id}"] = photo
            if hasattr(self, "_tree") and self._tree.exists(conversation_id):
                self._tree.item(conversation_id, image=photo)
        except tk.TclError:
            return

    def _show_loaded_image(self, parent: tk.Frame, button: tk.Button, data: bytes, url: str) -> None:
        try:
            encoded = base64.b64encode(data).decode("ascii")
            photo = tk.PhotoImage(data=encoded)
            factor = max(1, (max(photo.width(), photo.height()) + 379) // 380)
            if factor > 1:
                photo = photo.subsample(factor, factor)
            key = f"media:{id(button)}"
            self._photo_refs[key] = photo
            button.configure(image=photo, text="", state="normal", command=lambda: webbrowser.open(url, new=2))
        except tk.TclError:
            self._show_image_fallback(button, url, "当前 Tk 无法内嵌这种图片格式")

    @staticmethod
    def _show_image_fallback(button: tk.Button, url: str, detail: str) -> None:
        button.configure(
            text="[无法内嵌预览，点击在浏览器查看]",
            state="normal",
            command=lambda: webbrowser.open(url, new=2),
        )
        Tooltip(button, detail)

    def _send_from_event(self, _event=None):
        self._send()
        return "break"

    def _send(self) -> None:
        if self._current is None or self.runtime is None:
            return
        text = self._input.get("1.0", "end-1c").strip()
        if not text:
            return
        self.runtime.send_message(self._current.conversation_id, text)
        self._input.delete("1.0", "end")

    def _toggle_pin(self) -> None:
        if self._current is None:
            return
        self.repository.set_pinned(
            platform="qq",
            account_id=self._current.account_id,
            conversation_id=self._current.conversation_id,
            pinned=not self._current.pinned,
        )
        self._current = None
        self._pin_button.configure(state="disabled")
        self._refresh_conversations()

    def _drag_start(self, event):
        item = self._tree.identify_row(event.y)
        if self._tree.identify_column(event.x) != "#2":
            self._drag_item = None
            if item and not item.startswith("category:"):
                self._select_tree_conversation(item)
                return "break"
            return None
        self._drag_item = item if item and not item.startswith("category:") else None
        return "break" if self._drag_item else None

    def _drag_end(self, event):
        dragged = self._drag_item
        self._drag_item = None
        if not dragged:
            return None
        target = self._tree.identify_row(event.y)
        if (
            not target
            or dragged == target
            or target.startswith("category:")
        ):
            return "break"
        parent = self._tree.parent(dragged)
        if self._tree.parent(target) != parent:
            return "break"
        target_index = self._tree.index(target)
        self._tree.move(dragged, parent, target_index)
        ordered = list(self._tree.get_children(parent))
        self.repository.reorder(
            platform="qq",
            account_id=self._account_id,
            conversation_ids=ordered,
        )
        self._refresh_conversations()
        return "break"

    def _set_connected_controls(self, connected: bool) -> None:
        state = "normal" if connected else "disabled"
        for widget in self._toolbar_widgets:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        if hasattr(self, "_send_button") and self._send_button.winfo_exists():
            self._send_button.configure(state="normal" if connected and self._current else "disabled")
        if hasattr(self, "_takeover_widget") and self._takeover_widget.winfo_exists():
            self._takeover_widget.configure(
                state="normal" if connected and self._current else "disabled"
            )
        if connected:
            self._update_navigation_controls()
        elif hasattr(self, "_navigation_buttons"):
            for button in self._navigation_buttons.values():
                button.configure(state="disabled")

    def _set_takeover_state(self, conversation_id: str, enabled: bool) -> None:
        if enabled:
            self._takeover_conversations.add(conversation_id)
        else:
            self._takeover_conversations.discard(conversation_id)
        if self._current and self._current.conversation_id == conversation_id:
            self._takeover_var.set(enabled)

    def _toggle_takeover(self) -> None:
        if self._current is None or self.runtime is None:
            self._takeover_var.set(False)
            return
        enabled = self._takeover_var.get()
        self._set_takeover_state(self._current.conversation_id, enabled)
        self.runtime.set_human_takeover(self._current.conversation_id, enabled)

    def _toggle_auto(self) -> None:
        if self.runtime:
            self.runtime.set_auto_reply(self._auto_var.get())

    def _toggle_proactive(self) -> None:
        if self.runtime:
            self.runtime.set_proactive(self._proactive_var.get())

    def _toggle_followup(self) -> None:
        if self.runtime:
            self.runtime.set_followup(self._followup_var.get())

    def _toggle_dry(self) -> None:
        if not self._dry_var.get():
            confirmed = messagebox.askyesno(
                "关闭 Dry Run",
                "关闭后机器人和人工输入都可能真实发送到 QQ。确定继续吗？",
                parent=self.root,
            )
            if not confirmed:
                self._dry_var.set(True)
                return
        if self.runtime:
            self.runtime.set_dry_run(self._dry_var.get())

    def _open_settings(self) -> None:
        SettingsDialog(
            self.root,
            env_path=self._env_path,
            settings=self.settings,
            on_saved=self._settings_saved,
        )

    def _settings_saved(self) -> None:
        settings, error = self._load_settings()
        self.settings = settings
        self._configuration_error = error

    def _close(self) -> None:
        if self.runtime is not None:
            self.runtime.stop()
        self.root.destroy()


def run() -> None:
    log_path = PROJECT_ROOT / "data" / "chatbot.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )
    root = tk.Tk()
    DesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    run()
