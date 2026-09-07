from __future__ import annotations

from pathlib import Path

import pytest

from app.config import ConfigurationError, PROJECT_ROOT
from app.core.prompt import load_system_prompt


def test_load_system_prompt_reads_utf8_text(tmp_path: Path) -> None:
    path = tmp_path / "chat.txt"
    path.write_text("  使用自然中文。  ", encoding="utf-8")

    assert load_system_prompt(path) == "使用自然中文。"


def test_load_system_prompt_rejects_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "chat.txt"
    path.write_text("  \n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="不能为空"):
        load_system_prompt(path)


def test_memory_maintenance_prompt_loads_from_independent_file() -> None:
    prompt = load_system_prompt(
        PROJECT_ROOT / "app" / "prompts" / "memory_maintenance.txt"
    )

    assert "source_message_ids" in prompt
    assert "JSON" in prompt
    assert "不要" in prompt
