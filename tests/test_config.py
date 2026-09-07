from __future__ import annotations

from pathlib import Path

import pytest

from app.config import ConfigurationError, Settings
from app.models.factory import build_model
from app.models.mock import MockModel
from app.models.openai import OpenAICompatibleModel


def test_provider_defaults_to_mock_without_external_configuration(tmp_path: Path) -> None:
    settings = Settings.from_env(env_file=tmp_path / ".env", environ={})

    assert settings.model_provider == "mock"
    assert isinstance(build_model(settings), MockModel)


def test_openai_compatible_provider_loads_from_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MODEL_PROVIDER=openai_compatible",
                "MODEL_BASE_URL=http://localhost:11434/v1",
                "MODEL_API_KEY=test-secret",
                "MODEL_NAME=qwen-test",
                "MODEL_TEMPERATURE=0.8",
                "MODEL_MAX_TOKENS=128",
                "CONTEXT_MAX_MESSAGES=8",
                "MESSAGE_DEBOUNCE_SECONDS=1.5",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings.from_env(env_file=env_file, environ={})

    assert settings.model_provider == "openai_compatible"
    assert settings.model_temperature == 0.8
    assert settings.model_max_tokens == 128
    assert settings.context_max_messages == 8
    assert settings.message_debounce_seconds == 1.5
    assert isinstance(build_model(settings), OpenAICompatibleModel)
    assert "test-secret" not in repr(settings)


def test_process_environment_overrides_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MODEL_PROVIDER=openai_compatible", encoding="utf-8")

    settings = Settings.from_env(
        env_file=env_file,
        environ={"MODEL_PROVIDER": "mock"},
    )

    assert settings.model_provider == "mock"


def test_openai_compatible_provider_requires_all_fields(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="MODEL_BASE_URL"):
        Settings.from_env(
            env_file=tmp_path / ".env",
            environ={"MODEL_PROVIDER": "openai_compatible"},
        )


def test_invalid_provider_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="MODEL_PROVIDER"):
        Settings.from_env(
            env_file=tmp_path / ".env",
            environ={"MODEL_PROVIDER": "unknown"},
        )


def test_legacy_limit_names_remain_compatible(tmp_path: Path) -> None:
    settings = Settings.from_env(
        env_file=tmp_path / ".env",
        environ={
            "MODEL_HISTORY_LIMIT": "7",
            "MODEL_MAX_OUTPUT_TOKENS": "99",
            "CHATBOT_MERGE_WINDOW": "0.25",
        },
    )

    assert settings.context_max_messages == 7
    assert settings.model_max_tokens == 99
    assert settings.message_debounce_seconds == 0.25
    assert settings.model_history_limit == 7
    assert settings.model_max_output_tokens == 99
    assert settings.merge_window_seconds == 0.25


def test_temperature_can_be_disabled_for_incompatible_providers(
    tmp_path: Path,
) -> None:
    settings = Settings.from_env(
        env_file=tmp_path / ".env",
        environ={"MODEL_TEMPERATURE": ""},
    )

    assert settings.model_temperature is None


def test_temperature_range_is_validated(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="MODEL_TEMPERATURE"):
        Settings.from_env(
            env_file=tmp_path / ".env",
            environ={"MODEL_TEMPERATURE": "2"},
        )


def test_memory_configuration_is_loaded(tmp_path: Path) -> None:
    settings = Settings.from_env(
        env_file=tmp_path / ".env",
        environ={
            "MEMORY_AUTO_PROCESS_ENABLED": "false",
            "MEMORY_EXTRACTION_INTERVAL_MESSAGES": "8",
            "SUMMARY_TRIGGER_MESSAGES": "18",
            "MEMORY_PROCESSING_BATCH_MESSAGES": "30",
            "MEMORY_CONFIDENCE_THRESHOLD": "0.9",
            "MEMORY_CONTEXT_LIMIT": "4",
            "MEMORY_CONTEXT_FALLBACK": "1",
            "MEMORY_DEBUG": "true",
            "MEMORY_PROMPT_PATH": "app/prompts/memory_maintenance.txt",
        },
    )

    assert settings.memory_auto_process_enabled is False
    assert settings.memory_extraction_interval_messages == 8
    assert settings.summary_trigger_messages == 18
    assert settings.memory_processing_batch_messages == 30
    assert settings.memory_confidence_threshold == 0.9
    assert settings.memory_context_limit == 4
    assert settings.memory_context_fallback == 1
    assert settings.memory_debug is True
    assert settings.memory_prompt_path.name == "memory_maintenance.txt"


def test_invalid_memory_configuration_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="MEMORY_DEBUG"):
        Settings.from_env(
            env_file=tmp_path / ".env",
            environ={"MEMORY_DEBUG": "sometimes"},
        )

    with pytest.raises(ConfigurationError, match="MEMORY_CONFIDENCE_THRESHOLD"):
        Settings.from_env(
            env_file=tmp_path / ".env",
            environ={"MEMORY_CONFIDENCE_THRESHOLD": "1.2"},
        )
