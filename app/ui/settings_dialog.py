"""Settings window for model and QQ desktop options."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable
from urllib.parse import urlsplit

from app.config import Settings
from app.ui.settings_store import read_env_values, update_env_file


_SAVED_SECRET = "••••••••（已保存）"


class SettingsDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        *,
        env_path: Path,
        settings: Settings,
        on_saved: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("设置")
        self.geometry("650x590")
        self.minsize(560, 500)
        self.transient(master)
        self._env_path = env_path
        self._settings = settings
        self._on_saved = on_saved
        self._values = read_env_values(env_path)
        self._entries: dict[str, tk.StringVar] = {}
        self._booleans: dict[str, tk.BooleanVar] = {}
        self._build()

    def _build(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        model = ttk.Frame(notebook, padding=18)
        qq = ttk.Frame(notebook, padding=18)
        notebook.add(model, text="会话模型")
        notebook.add(qq, text="QQ 与界面")
        self._build_model_tab(model)
        self._build_qq_tab(qq)

        footer = ttk.Frame(self, padding=(16, 4, 16, 14))
        footer.pack(fill="x")
        ttk.Label(
            footer,
            text="模型和连接参数保存后，下次重新连接生效。",
            foreground="#6b7280",
        ).pack(side="left")
        ttk.Button(footer, text="取消", command=self.destroy).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(footer, text="保存", command=self._save).pack(side="right")

    def _build_model_tab(self, frame: ttk.Frame) -> None:
        fields = (
            ("Provider", "MODEL_PROVIDER", self._settings.model_provider, False),
            ("Base URL", "MODEL_BASE_URL", self._settings.model_base_url or "", False),
            ("API Key", "MODEL_API_KEY", "", True),
            ("模型名称", "MODEL_NAME", self._settings.model_name or "", False),
            ("请求超时（秒）", "MODEL_TIMEOUT_SECONDS", str(self._settings.model_timeout_seconds), False),
            ("Temperature", "MODEL_TEMPERATURE", "" if self._settings.model_temperature is None else str(self._settings.model_temperature), False),
            ("最大生成 Tokens", "MODEL_MAX_TOKENS", str(self._settings.model_max_tokens), False),
            ("最近上下文消息数", "CONTEXT_MAX_MESSAGES", str(self._settings.context_max_messages), False),
            ("最大回复字符数", "MODEL_MAX_OUTPUT_CHARS", str(self._settings.model_max_output_chars), False),
            ("连续消息等待（秒）", "MESSAGE_DEBOUNCE_SECONDS", str(self._settings.message_debounce_seconds), False),
        )
        for row, (label, key, fallback, secret) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=6)
            value = self._values.get(key, fallback)
            if secret and value:
                value = _SAVED_SECRET
            variable = tk.StringVar(value=value)
            self._entries[key] = variable
            if key == "MODEL_PROVIDER":
                widget = ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=("mock", "openai_compatible"),
                    state="readonly",
                )
            else:
                widget = ttk.Entry(
                    frame,
                    textvariable=variable,
                    show="•" if secret else "",
                )
            widget.grid(row=row, column=1, sticky="ew", padx=(18, 0), pady=6)
        frame.columnconfigure(1, weight=1)

    def _build_qq_tab(self, frame: ttk.Frame) -> None:
        fields = (
            (
                "NapCat WebUI",
                "QQ_WEBUI_URL",
                self._settings.qq_webui_url,
                False,
            ),
            (
                "WebUI Token",
                "QQ_WEBUI_TOKEN",
                self._settings.qq_webui_token or "",
                True,
            ),
            (
                "WebUI 配置文件",
                "QQ_WEBUI_CONFIG_PATH",
                (
                    str(self._settings.qq_webui_config_path)
                    if self._settings.qq_webui_config_path
                    else ""
                ),
                False,
            ),
            ("OneBot WebSocket", "QQ_WS_URL", self._settings.qq_ws_url, False),
            (
                "单次历史消息数",
                "DESKTOP_HISTORY_MESSAGE_LIMIT",
                str(self._settings.desktop_history_message_limit),
                False,
            ),
        )
        for row, (label, key, fallback, secret) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=7)
            value = self._values.get(key, fallback)
            if secret and value:
                value = _SAVED_SECRET
            variable = tk.StringVar(value=value)
            self._entries[key] = variable
            ttk.Entry(
                frame,
                textvariable=variable,
                show="•" if secret else "",
            ).grid(
                row=row, column=1, sticky="ew", padx=(18, 0), pady=7
            )
        checks = (
            ("自动回复", "QQ_AUTO_REPLY_ENABLED", self._settings.qq_auto_reply_enabled),
            ("Dry Run（不真实发送）", "QQ_DRY_RUN", self._settings.qq_dry_run),
            ("允许主动消息", "QQ_PROACTIVE_ENABLED", self._settings.qq_proactive_enabled),
            ("允许概率追加回复", "QQ_FOLLOWUP_ENABLED", self._settings.qq_followup_enabled),
            ("默认展开图片和表情", "DESKTOP_AUTO_SHOW_MEDIA", self._settings.desktop_auto_show_media),
        )
        start = len(fields) + 1
        for offset, (label, key, fallback) in enumerate(checks):
            raw = self._values.get(key)
            value = fallback if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}
            variable = tk.BooleanVar(value=value)
            self._booleans[key] = variable
            ttk.Checkbutton(frame, text=label, variable=variable).grid(
                row=start + offset,
                column=0,
                columnspan=2,
                sticky="w",
                pady=7,
            )
        ttk.Label(
            frame,
            text=(
                "会优先使用独立 WebUI Token；留空时自动读取本机 webui.json。"
                "它和 OneBot WebSocket Token 不是同一个配置。自动查找不到时，"
                "可填写 webui.json 的完整路径。"
            ),
            wraplength=500,
            foreground="#6b7280",
        ).grid(row=start + len(checks) + 1, column=0, columnspan=2, sticky="w", pady=14)
        frame.columnconfigure(1, weight=1)

    def _save(self) -> None:
        updates = {key: variable.get().strip() for key, variable in self._entries.items()}
        for key in ("MODEL_API_KEY", "QQ_WEBUI_TOKEN"):
            if updates.get(key) == _SAVED_SECRET:
                updates.pop(key, None)
        for key, variable in self._booleans.items():
            updates[key] = "true" if variable.get() else "false"
        try:
            self._validate(updates)
            update_env_file(self._env_path, updates)
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法保存设置", str(exc), parent=self)
            return
        if self._on_saved is not None:
            self._on_saved()
        messagebox.showinfo("设置已保存", "重新连接 QQ 后生效。", parent=self)
        self.destroy()

    def _validate(self, updates: dict[str, str]) -> None:
        provider = updates.get("MODEL_PROVIDER", "mock")
        if provider not in {"mock", "openai_compatible"}:
            raise ValueError("MODEL_PROVIDER 只能是 mock 或 openai_compatible")
        if provider == "openai_compatible":
            for key in ("MODEL_BASE_URL", "MODEL_NAME"):
                if not updates.get(key):
                    raise ValueError(f"{key} 不能为空")
            if "MODEL_API_KEY" in updates and not updates["MODEL_API_KEY"]:
                raise ValueError("MODEL_API_KEY 不能为空")
            if (
                "MODEL_API_KEY" not in updates
                and not self._values.get("MODEL_API_KEY")
            ):
                raise ValueError("MODEL_API_KEY 不能为空")
        for key, schemes in (
            ("QQ_WEBUI_URL", {"http", "https"}),
            ("QQ_WS_URL", {"ws", "wss"}),
        ):
            parsed = urlsplit(updates[key])
            if parsed.scheme not in schemes or not parsed.hostname:
                raise ValueError(f"{key} 地址格式无效")
        positive_float = ("MODEL_TIMEOUT_SECONDS", "MESSAGE_DEBOUNCE_SECONDS")
        for key in positive_float:
            if float(updates[key]) <= 0:
                raise ValueError(f"{key} 必须大于 0")
        if updates.get("MODEL_TEMPERATURE"):
            temperature = float(updates["MODEL_TEMPERATURE"])
            if not 0 <= temperature < 2:
                raise ValueError("MODEL_TEMPERATURE 必须在 0 到 2 之间")
        for key in (
            "MODEL_MAX_TOKENS",
            "CONTEXT_MAX_MESSAGES",
            "MODEL_MAX_OUTPUT_CHARS",
            "DESKTOP_HISTORY_MESSAGE_LIMIT",
        ):
            if int(updates[key]) < 1:
                raise ValueError(f"{key} 必须大于 0")
