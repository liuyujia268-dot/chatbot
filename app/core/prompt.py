"""System prompt loading."""

from __future__ import annotations

from pathlib import Path

from app.config import ConfigurationError


def load_system_prompt(path: Path) -> str:
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigurationError(f"无法读取 System Prompt：{path}") from exc
    if not prompt:
        raise ConfigurationError("System Prompt 不能为空")
    return prompt
