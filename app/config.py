"""Environment-based application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path = PROJECT_ROOT / "data" / "chatbot.db"
    merge_window_seconds: float = 0.5

    @classmethod
    def from_env(cls) -> "Settings":
        merge_window = float(os.getenv("CHATBOT_MERGE_WINDOW", "0.5"))
        if merge_window < 0:
            raise ValueError("CHATBOT_MERGE_WINDOW must not be negative")
        return cls(
            database_path=Path(
                os.getenv("CHATBOT_DATABASE_PATH", str(PROJECT_ROOT / "data" / "chatbot.db"))
            ),
            merge_window_seconds=merge_window,
        )
