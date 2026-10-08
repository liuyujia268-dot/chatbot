"""Small, comment-preserving .env editor used by the settings window."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Mapping
from app.config import Settings, ConfigurationError, _read_env_file


MODEL_SETTING_KEYS = (
    "MODEL_PROVIDER",
    "MODEL_BASE_URL",
    "MODEL_API_KEY",
    "MODEL_NAME",
    "MODEL_PROXY_MODE",
    "MODEL_PROXY_URL",
    "MODEL_TIMEOUT_SECONDS",
    "MODEL_TEMPERATURE",
    "MODEL_MAX_TOKENS",
    "CONTEXT_MAX_MESSAGES",
    "MODEL_MAX_OUTPUT_CHARS",
    "MESSAGE_DEBOUNCE_SECONDS",
)

QQ_SETTING_KEYS = (
    "QQ_WEBUI_URL",
    "QQ_WEBUI_TOKEN",
    "QQ_WEBUI_CONFIG_PATH",
    "QQ_WS_URL",
    "QQ_WS_TOKEN",
    "QQ_AUTO_REPLY_ENABLED",
    "QQ_DRY_RUN",
    "QQ_PROACTIVE_ENABLED",
    "QQ_FOLLOWUP_ENABLED",
    "DESKTOP_AUTO_SHOW_MEDIA",
)


def read_env_values(path: Path) -> dict[str, str]:
    return _read_env_file(path)


def validate_values(values: Mapping[str, str], updates: Mapping[str, str]) -> Settings:
    for key, value in updates.items():
        if '\n' in value or '\r' in value:
            raise ConfigurationError(f'{key} 不能包含换行')
    return Settings.from_values({**values, **updates})


def save_validated_env(path: Path, updates: Mapping[str, str]) -> Settings:
    settings = validate_values(read_env_values(path), updates)
    update_env_file(path, updates)
    return settings


def update_env_file(path: Path, updates: Mapping[str, str]) -> None:
    """Update selected keys without revealing or dropping unrelated values."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = (
        path.read_text(encoding="utf-8-sig").splitlines()
        if path.exists()
        else []
    )
    pending = {key: str(value) for key, value in updates.items()}
    changed_keys = set(pending)
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            result.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in changed_keys and key not in pending:
            continue  # eliminate stale duplicate definitions of updated fields
        if key not in pending:
            result.append(line)
            continue
        result.append(f"{key}={pending.pop(key)}")
    if pending and result and result[-1].strip():
        result.append("")
    for key, value in pending.items():
        result.append(f"{key}={value}")
    payload = "\n".join(result).rstrip() + "\n"
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
