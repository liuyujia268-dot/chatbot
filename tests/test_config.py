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
    assert settings.qq_image_history_limit == 1
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


def test_outbound_dry_run_configuration_is_loaded(tmp_path: Path) -> None:
    settings = Settings.from_env(
        env_file=tmp_path / ".env",
        environ={"OUTBOUND_DRY_RUN": "true"},
    )

    assert settings.outbound_dry_run is True


def test_qq_and_image_configuration_is_loaded(tmp_path: Path) -> None:
    settings = Settings.from_env(
        env_file=tmp_path / ".env",
        environ={
            "QQ_ENABLED": "true",
            "QQ_WS_URL": "ws://127.0.0.1:3001",
            "QQ_WS_TOKEN": "test-token",
            "QQ_PRIMARY_CONTACT_ID": "20002",
            "QQ_ADMIN_CONTACT_IDS": "20002, 20003",
            "QQ_ADMIN_MEMORY_PREFIX": "#指令",
            "QQ_TRAINING_GROUP_IDS": "90001, 90002",
            "QQ_REPLY_GROUP_IDS": "80001, 80002",
            "QQ_DRY_RUN": "true",
            "QQ_PROACTIVE_ENABLED": "true",
            "QQ_PROACTIVE_CONTACT_IDS": "20002, 30003",
            "QQ_PROACTIVE_EXCLUDE_CONTACT_IDS": "40004",
            "QQ_PROACTIVE_MIN_SECONDS": "30",
            "QQ_PROACTIVE_MAX_SECONDS": "90",
            "QQ_PROACTIVE_DAILY_LIMIT": "2",
            "QQ_PROACTIVE_RECENT_DAYS": "5",
            "QQ_PROACTIVE_QUIET_START": "23:30",
            "QQ_PROACTIVE_QUIET_END": "07:15",
            "QQ_FOLLOWUP_ENABLED": "true",
            "QQ_FOLLOWUP_PROBABILITY": "0.15",
            "QQ_FOLLOWUP_MIN_DELAY_SECONDS": "2",
            "QQ_FOLLOWUP_MAX_DELAY_SECONDS": "7",
            "QQ_IMAGE_MAX_BYTES": "4096",
            "QQ_IMAGE_TIMEOUT_SECONDS": "2.5",
            "QQ_IMAGE_ALLOW_REMOTE_URL": "false",
            "QQ_IMAGE_HISTORY_LIMIT": "1",
        },
    )

    assert settings.qq_enabled is True
    assert settings.qq_primary_contact_id == "20002"
    assert settings.qq_admin_contact_ids == ("20002", "20003")
    assert settings.qq_admin_memory_prefix == "#指令"
    assert settings.qq_training_group_ids == ("90001", "90002")
    assert settings.qq_reply_group_ids == ("80001", "80002")
    assert settings.qq_dry_run is True
    assert settings.qq_proactive_enabled is True
    assert settings.qq_proactive_contact_ids == ("20002", "30003")
    assert settings.qq_proactive_exclude_contact_ids == ("40004",)
    assert settings.qq_proactive_min_seconds == 30
    assert settings.qq_proactive_max_seconds == 90
    assert settings.qq_proactive_daily_limit == 2
    assert settings.qq_proactive_recent_days == 5
    assert settings.qq_proactive_quiet_start == "23:30"
    assert settings.qq_proactive_quiet_end == "07:15"
    assert settings.qq_followup_enabled is True
    assert settings.qq_followup_probability == 0.15
    assert settings.qq_followup_min_delay_seconds == 2
    assert settings.qq_followup_max_delay_seconds == 7
    assert settings.qq_image_max_bytes == 4096
    assert settings.qq_image_timeout_seconds == 2.5
    assert settings.qq_image_allow_remote_url is False
    assert settings.qq_image_history_limit == 1
    assert "test-token" not in repr(settings)


def test_qq_real_sending_can_be_enabled_explicitly(tmp_path: Path) -> None:
    settings = Settings.from_env(
        env_file=tmp_path / ".env",
        environ={"QQ_ENABLED": "true", "QQ_DRY_RUN": "false"},
    )
    assert settings.qq_enabled is True
    assert settings.qq_dry_run is False


def test_training_and_reply_groups_cannot_overlap(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="不能重叠"):
        Settings.from_env(
            env_file=tmp_path / ".env",
            environ={
                "QQ_TRAINING_GROUP_IDS": "90001",
                "QQ_REPLY_GROUP_IDS": "90001",
            },
        )


def test_invalid_proactive_range_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="不能大于"):
        Settings.from_env(
            env_file=tmp_path / ".env",
            environ={
                "QQ_PROACTIVE_MIN_SECONDS": "91",
                "QQ_PROACTIVE_MAX_SECONDS": "90",
            },
        )


def test_invalid_proactive_quiet_time_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="有效的本地时间"):
        Settings.from_env(
            env_file=tmp_path / ".env",
            environ={"QQ_PROACTIVE_QUIET_START": "25:00"},
        )


def test_invalid_followup_configuration_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="不能大于"):
        Settings.from_env(
            env_file=tmp_path / ".env",
            environ={
                "QQ_FOLLOWUP_MIN_DELAY_SECONDS": "8",
                "QQ_FOLLOWUP_MAX_DELAY_SECONDS": "2",
            },
        )
    with pytest.raises(ConfigurationError, match="小于或等于 1"):
        Settings.from_env(
            env_file=tmp_path / ".env",
            environ={"QQ_FOLLOWUP_PROBABILITY": "1.2"},
        )
