"""Settings window for model and QQ desktop options."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable
from urllib.parse import urlsplit

from app.config import Settings
from app.ui.settings_store import read_env_values, save_validated_env, validate_values


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
        self.geometry("690x740")
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
            text="模型/连接参数需重连；运行开关以顶部实际状态为准。",
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
            ("模型网络模式", "MODEL_PROXY_MODE", self._settings.model_proxy_mode, False),
            ("代理地址（仅 custom）", "MODEL_PROXY_URL", "", True),
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
            if key in {"MODEL_PROVIDER", "MODEL_PROXY_MODE"}:
                widget = ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=(("mock", "openai_compatible") if key == "MODEL_PROVIDER"
                            else ("system", "direct", "custom")),
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
        ttk.Label(
            frame,
            text="system：跟随电脑代理；direct：忽略电脑代理直连；custom：使用指定代理。\n"
                 "代理地址支持 http(s)://、socks5://、socks5h://；不修改系统代理，保存后重连生效。",
            wraplength=560, foreground="#6b7280",
        ).grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=12)

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
            ("OneBot Token", "QQ_WS_TOKEN", self._settings.qq_ws_token or "", True),
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
                "本机换号时会只读获取当前账号已启用的 WebSocket 地址/Token，"
                "仅在运行内存中使用；新账号未启用服务时，请在 NapCat 网络配置开启。"
            ),
            wraplength=500,
            foreground="#6b7280",
        ).grid(row=start + len(checks) + 1, column=0, columnspan=2, sticky="w", pady=14)
        frame.columnconfigure(1, weight=1)

    def _save(self) -> None:
        updates = {key: variable.get().strip() for key, variable in self._entries.items()}
        for key in ("MODEL_API_KEY", "MODEL_PROXY_URL", "QQ_WEBUI_TOKEN", "QQ_WS_TOKEN"):
            if updates.get(key) == _SAVED_SECRET:
                updates.pop(key, None)
        for key, variable in self._booleans.items():
            updates[key] = "true" if variable.get() else "false"
        try:
            self._validate(updates)
            save_validated_env(self._env_path, updates)
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法保存设置", str(exc), parent=self)
            return
        if self._on_saved is not None:
            self._on_saved()
        messagebox.showinfo("设置已保存", "配置已保存，尚未应用到当前连接。模型/连接参数重新连接QQ后生效；运行开关仍以顶部实际状态为准，保存的默认开关在下次启动使用。", parent=self)
        self.destroy()

    def _validate(self, updates: dict[str, str]) -> None:
        validate_values(self._values, updates)
