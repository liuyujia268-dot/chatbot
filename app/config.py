"""Application configuration loaded from process variables and an optional .env."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigurationError(ValueError):
    """Raised when application configuration is invalid."""


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ConfigurationError(f"无法读取配置文件：{path}") from exc

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not _ENV_KEY.fullmatch(key):
            raise ConfigurationError(f".env 第 {line_number} 行格式无效")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def _parse_int(name: str, value: str, *, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是整数") from exc
    if parsed < minimum:
        raise ConfigurationError(f"{name} 必须大于或等于 {minimum}")
    return parsed


def _parse_float(
    name: str,
    value: str,
    *,
    minimum: float = 0,
    maximum_exclusive: float | None = None,
) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是数字") from exc
    if parsed < minimum:
        raise ConfigurationError(f"{name} 必须大于或等于 {minimum}")
    if maximum_exclusive is not None and parsed >= maximum_exclusive:
        raise ConfigurationError(f"{name} 必须小于 {maximum_exclusive}")
    return parsed


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} 必须是 true 或 false")


def _parse_confidence(name: str, value: str) -> float:
    parsed = _parse_float(name, value)
    if parsed > 1:
        raise ConfigurationError(f"{name} 必须小于或等于 1")
    return parsed


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path = PROJECT_ROOT / "data" / "chatbot.db"
    log_path: Path = PROJECT_ROOT / "data" / "chatbot.log"
    message_debounce_seconds: float = 1.2
    model_provider: str = "mock"
    model_base_url: str | None = None
    model_api_key: str | None = field(default=None, repr=False)
    model_name: str | None = None
    model_timeout_seconds: float = 60.0
    model_temperature: float | None = 0.7
    model_max_tokens: int = 256
    context_max_messages: int = 12
    model_max_output_chars: int = 1200
    system_prompt_path: Path = PROJECT_ROOT / "app" / "prompts" / "chat.txt"
    memory_auto_process_enabled: bool = True
    memory_extraction_interval_messages: int = 12
    summary_trigger_messages: int = 24
    memory_processing_batch_messages: int = 40
    memory_confidence_threshold: float = 0.85
    memory_context_limit: int = 6
    memory_context_fallback: int = 2
    memory_debug: bool = False
    memory_prompt_path: Path = (
        PROJECT_ROOT / "app" / "prompts" / "memory_maintenance.txt"
    )

    @property
    def merge_window_seconds(self) -> float:
        """Compatibility alias for the stage-one setting."""
        return self.message_debounce_seconds

    @property
    def model_history_limit(self) -> int:
        """Compatibility alias for the previous context setting."""
        return self.context_max_messages

    @property
    def model_max_output_tokens(self) -> int:
        """Compatibility alias for the previous token setting."""
        return self.model_max_tokens

    @classmethod
    def from_env(
        cls,
        env_file: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "Settings":
        file_values = _read_env_file(env_file or PROJECT_ROOT / ".env")
        process_values = os.environ if environ is None else environ

        def get(name: str, default: str = "") -> str:
            return process_values.get(name, file_values.get(name, default)).strip()

        def get_first(*names: str, default: str = "") -> str:
            for source in (process_values, file_values):
                for name in names:
                    if name in source:
                        return source[name].strip()
            return default.strip()

        provider = get("MODEL_PROVIDER", "mock").lower()
        if provider not in {"mock", "openai_compatible"}:
            raise ConfigurationError(
                "MODEL_PROVIDER 只支持 mock 或 openai_compatible"
            )

        base_url = get("MODEL_BASE_URL") or None
        api_key = get("MODEL_API_KEY") or None
        model_name = get("MODEL_NAME") or None
        if provider == "openai_compatible":
            missing = [
                name
                for name, value in (
                    ("MODEL_BASE_URL", base_url),
                    ("MODEL_API_KEY", api_key),
                    ("MODEL_NAME", model_name),
                )
                if not value
            ]
            if missing:
                raise ConfigurationError(
                    "openai_compatible 缺少配置：" + ", ".join(missing)
                )
            parsed_url = urlsplit(base_url)
            if (
                parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc
                or parsed_url.username
                or parsed_url.password
                or parsed_url.query
                or parsed_url.fragment
            ):
                raise ConfigurationError("MODEL_BASE_URL 必须是安全的 HTTP(S) 基础地址")

        temperature_value = get("MODEL_TEMPERATURE", "0.7")

        return cls(
            database_path=_project_path(
                get("CHATBOT_DATABASE_PATH", "data/chatbot.db")
            ),
            log_path=_project_path(get("CHATBOT_LOG_PATH", "data/chatbot.log")),
            message_debounce_seconds=_parse_float(
                "MESSAGE_DEBOUNCE_SECONDS",
                get_first(
                    "MESSAGE_DEBOUNCE_SECONDS",
                    "CHATBOT_MERGE_WINDOW",
                    default="1.2",
                ),
            ),
            model_provider=provider,
            model_base_url=base_url,
            model_api_key=api_key,
            model_name=model_name,
            model_timeout_seconds=_parse_float(
                "MODEL_TIMEOUT_SECONDS", get("MODEL_TIMEOUT_SECONDS", "60"), minimum=0.1
            ),
            model_temperature=(
                None
                if temperature_value.lower() in {"", "none", "null"}
                else _parse_float(
                    "MODEL_TEMPERATURE",
                    temperature_value,
                    maximum_exclusive=2,
                )
            ),
            model_max_tokens=_parse_int(
                "MODEL_MAX_TOKENS",
                get_first(
                    "MODEL_MAX_TOKENS",
                    "MODEL_MAX_OUTPUT_TOKENS",
                    default="256",
                ),
            ),
            context_max_messages=_parse_int(
                "CONTEXT_MAX_MESSAGES",
                get_first(
                    "CONTEXT_MAX_MESSAGES",
                    "MODEL_HISTORY_LIMIT",
                    default="12",
                ),
            ),
            model_max_output_chars=_parse_int(
                "MODEL_MAX_OUTPUT_CHARS", get("MODEL_MAX_OUTPUT_CHARS", "1200")
            ),
            system_prompt_path=_project_path(
                get("SYSTEM_PROMPT_PATH", "app/prompts/chat.txt")
            ),
            memory_auto_process_enabled=_parse_bool(
                "MEMORY_AUTO_PROCESS_ENABLED",
                get("MEMORY_AUTO_PROCESS_ENABLED", "true"),
            ),
            memory_extraction_interval_messages=_parse_int(
                "MEMORY_EXTRACTION_INTERVAL_MESSAGES",
                get("MEMORY_EXTRACTION_INTERVAL_MESSAGES", "12"),
            ),
            summary_trigger_messages=_parse_int(
                "SUMMARY_TRIGGER_MESSAGES",
                get("SUMMARY_TRIGGER_MESSAGES", "24"),
            ),
            memory_processing_batch_messages=_parse_int(
                "MEMORY_PROCESSING_BATCH_MESSAGES",
                get("MEMORY_PROCESSING_BATCH_MESSAGES", "40"),
            ),
            memory_confidence_threshold=_parse_confidence(
                "MEMORY_CONFIDENCE_THRESHOLD",
                get("MEMORY_CONFIDENCE_THRESHOLD", "0.85"),
            ),
            memory_context_limit=_parse_int(
                "MEMORY_CONTEXT_LIMIT",
                get("MEMORY_CONTEXT_LIMIT", "6"),
            ),
            memory_context_fallback=_parse_int(
                "MEMORY_CONTEXT_FALLBACK",
                get("MEMORY_CONTEXT_FALLBACK", "2"),
                minimum=0,
            ),
            memory_debug=_parse_bool(
                "MEMORY_DEBUG",
                get("MEMORY_DEBUG", "false"),
            ),
            memory_prompt_path=_project_path(
                get(
                    "MEMORY_PROMPT_PATH",
                    "app/prompts/memory_maintenance.txt",
                )
            ),
        )
