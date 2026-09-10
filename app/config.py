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


def _parse_hhmm(name: str, value: str) -> str:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if match is None:
        raise ConfigurationError(f"{name} 必须使用 HH:MM 格式")
    hour, minute = (int(part) for part in match.groups())
    if hour > 23 or minute > 59:
        raise ConfigurationError(f"{name} 必须是有效的本地时间")
    return f"{hour:02d}:{minute:02d}"


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path = PROJECT_ROOT / "data" / "chatbot.db"
    log_path: Path = PROJECT_ROOT / "data" / "chatbot.log"
    outbound_dry_run: bool = False
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
    reply_repair_prompt_path: Path = (
        PROJECT_ROOT / "app" / "prompts" / "reply_repair.txt"
    )
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
    qq_enabled: bool = False
    qq_ws_url: str = "ws://127.0.0.1:3001"
    qq_ws_token: str | None = field(default=None, repr=False)
    qq_primary_contact_id: str | None = None
    qq_primary_contact_name: str = "宇飞"
    qq_admin_contact_ids: tuple[str, ...] = ()
    qq_admin_memory_prefix: str = "#指令"
    qq_training_group_ids: tuple[str, ...] = ()
    qq_reply_group_ids: tuple[str, ...] = ()
    qq_dry_run: bool = True
    qq_proactive_enabled: bool = False
    qq_proactive_contact_ids: tuple[str, ...] = ()
    qq_proactive_exclude_contact_ids: tuple[str, ...] = ()
    qq_proactive_min_seconds: float = 2400.0
    qq_proactive_max_seconds: float = 4800.0
    qq_proactive_daily_limit: int = 3
    qq_proactive_recent_days: int = 7
    qq_proactive_quiet_start: str = "00:00"
    qq_proactive_quiet_end: str = "08:00"
    proactive_prompt_path: Path = (
        PROJECT_ROOT / "app" / "prompts" / "proactive.txt"
    )
    qq_followup_enabled: bool = True
    qq_followup_probability: float = 0.15
    qq_followup_min_delay_seconds: float = 2.0
    qq_followup_max_delay_seconds: float = 7.0
    followup_prompt_path: Path = (
        PROJECT_ROOT / "app" / "prompts" / "followup.txt"
    )
    qq_image_max_bytes: int = 8 * 1024 * 1024
    qq_image_timeout_seconds: float = 10.0
    qq_image_allow_remote_url: bool = True
    qq_image_history_limit: int = 1
    style_context_min_messages: int = 3
    style_context_max_messages: int = 10
    style_export_path: Path = PROJECT_ROOT / "data" / "style_samples.jsonl"

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
        qq_enabled = _parse_bool("QQ_ENABLED", get("QQ_ENABLED", "false"))
        qq_dry_run = _parse_bool("QQ_DRY_RUN", get("QQ_DRY_RUN", "true"))
        qq_ws_url = get("QQ_WS_URL", "ws://127.0.0.1:3001")
        parsed_qq_url = urlsplit(qq_ws_url)
        if parsed_qq_url.scheme not in {"ws", "wss"} or not parsed_qq_url.hostname:
            raise ConfigurationError("QQ_WS_URL 必须是 ws:// 或 wss:// 地址")
        style_min = _parse_int(
            "STYLE_CONTEXT_MIN_MESSAGES",
            get("STYLE_CONTEXT_MIN_MESSAGES", "3"),
        )
        style_max = _parse_int(
            "STYLE_CONTEXT_MAX_MESSAGES",
            get("STYLE_CONTEXT_MAX_MESSAGES", "10"),
        )
        if style_min > style_max:
            raise ConfigurationError("STYLE_CONTEXT_MIN_MESSAGES 不能大于最大值")
        training_group_ids = tuple(
            item.strip()
            for item in get("QQ_TRAINING_GROUP_IDS").split(",")
            if item.strip()
        )
        reply_group_ids = tuple(
            item.strip()
            for item in get("QQ_REPLY_GROUP_IDS").split(",")
            if item.strip()
        )
        if set(training_group_ids) & set(reply_group_ids):
            raise ConfigurationError(
                "QQ_TRAINING_GROUP_IDS 与 QQ_REPLY_GROUP_IDS 不能重叠"
            )
        proactive_min = _parse_float(
            "QQ_PROACTIVE_MIN_SECONDS",
            get("QQ_PROACTIVE_MIN_SECONDS", "2400"),
            minimum=1,
        )
        proactive_max = _parse_float(
            "QQ_PROACTIVE_MAX_SECONDS",
            get("QQ_PROACTIVE_MAX_SECONDS", "4800"),
            minimum=1,
        )
        if proactive_min > proactive_max:
            raise ConfigurationError(
                "QQ_PROACTIVE_MIN_SECONDS 不能大于最大值"
            )
        followup_min = _parse_float(
            "QQ_FOLLOWUP_MIN_DELAY_SECONDS",
            get("QQ_FOLLOWUP_MIN_DELAY_SECONDS", "2"),
        )
        followup_max = _parse_float(
            "QQ_FOLLOWUP_MAX_DELAY_SECONDS",
            get("QQ_FOLLOWUP_MAX_DELAY_SECONDS", "7"),
        )
        if followup_min > followup_max:
            raise ConfigurationError(
                "QQ_FOLLOWUP_MIN_DELAY_SECONDS 不能大于最大值"
            )

        return cls(
            database_path=_project_path(
                get("CHATBOT_DATABASE_PATH", "data/chatbot.db")
            ),
            log_path=_project_path(get("CHATBOT_LOG_PATH", "data/chatbot.log")),
            outbound_dry_run=_parse_bool(
                "OUTBOUND_DRY_RUN",
                get("OUTBOUND_DRY_RUN", "false"),
            ),
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
            reply_repair_prompt_path=_project_path(
                get(
                    "REPLY_REPAIR_PROMPT_PATH",
                    "app/prompts/reply_repair.txt",
                )
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
            qq_enabled=qq_enabled,
            qq_ws_url=qq_ws_url,
            qq_ws_token=get("QQ_WS_TOKEN") or None,
            qq_primary_contact_id=get("QQ_PRIMARY_CONTACT_ID") or None,
            qq_primary_contact_name=get("QQ_PRIMARY_CONTACT_NAME", "宇飞"),
            qq_admin_contact_ids=tuple(
                item.strip()
                for item in get("QQ_ADMIN_CONTACT_IDS").split(",")
                if item.strip()
            ),
            qq_admin_memory_prefix=(
                get("QQ_ADMIN_MEMORY_PREFIX", "#指令") or "#指令"
            ),
            qq_training_group_ids=training_group_ids,
            qq_reply_group_ids=reply_group_ids,
            qq_dry_run=qq_dry_run,
            qq_proactive_enabled=_parse_bool(
                "QQ_PROACTIVE_ENABLED",
                get("QQ_PROACTIVE_ENABLED", "false"),
            ),
            qq_proactive_contact_ids=tuple(
                item.strip()
                for item in get("QQ_PROACTIVE_CONTACT_IDS").split(",")
                if item.strip()
            ),
            qq_proactive_exclude_contact_ids=tuple(
                item.strip()
                for item in get("QQ_PROACTIVE_EXCLUDE_CONTACT_IDS").split(",")
                if item.strip()
            ),
            qq_proactive_min_seconds=proactive_min,
            qq_proactive_max_seconds=proactive_max,
            qq_proactive_daily_limit=_parse_int(
                "QQ_PROACTIVE_DAILY_LIMIT",
                get("QQ_PROACTIVE_DAILY_LIMIT", "3"),
            ),
            qq_proactive_recent_days=_parse_int(
                "QQ_PROACTIVE_RECENT_DAYS",
                get("QQ_PROACTIVE_RECENT_DAYS", "7"),
            ),
            qq_proactive_quiet_start=_parse_hhmm(
                "QQ_PROACTIVE_QUIET_START",
                get("QQ_PROACTIVE_QUIET_START", "00:00"),
            ),
            qq_proactive_quiet_end=_parse_hhmm(
                "QQ_PROACTIVE_QUIET_END",
                get("QQ_PROACTIVE_QUIET_END", "08:00"),
            ),
            proactive_prompt_path=_project_path(
                get("PROACTIVE_PROMPT_PATH", "app/prompts/proactive.txt")
            ),
            qq_followup_enabled=_parse_bool(
                "QQ_FOLLOWUP_ENABLED",
                get("QQ_FOLLOWUP_ENABLED", "true"),
            ),
            qq_followup_probability=_parse_confidence(
                "QQ_FOLLOWUP_PROBABILITY",
                get("QQ_FOLLOWUP_PROBABILITY", "0.15"),
            ),
            qq_followup_min_delay_seconds=followup_min,
            qq_followup_max_delay_seconds=followup_max,
            followup_prompt_path=_project_path(
                get("FOLLOWUP_PROMPT_PATH", "app/prompts/followup.txt")
            ),
            qq_image_max_bytes=_parse_int(
                "QQ_IMAGE_MAX_BYTES", get("QQ_IMAGE_MAX_BYTES", "8388608")
            ),
            qq_image_timeout_seconds=_parse_float(
                "QQ_IMAGE_TIMEOUT_SECONDS",
                get("QQ_IMAGE_TIMEOUT_SECONDS", "10"),
                minimum=0.1,
            ),
            qq_image_allow_remote_url=_parse_bool(
                "QQ_IMAGE_ALLOW_REMOTE_URL",
                get("QQ_IMAGE_ALLOW_REMOTE_URL", "true"),
            ),
            qq_image_history_limit=_parse_int(
                "QQ_IMAGE_HISTORY_LIMIT",
                get("QQ_IMAGE_HISTORY_LIMIT", "1"),
                minimum=0,
            ),
            style_context_min_messages=style_min,
            style_context_max_messages=style_max,
            style_export_path=_project_path(
                get("STYLE_EXPORT_PATH", "data/style_samples.jsonl")
            ),
        )
