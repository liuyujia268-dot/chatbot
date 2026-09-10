from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import ConfigurationError, PROJECT_ROOT
from app.tools.chat_replay import load_cases, save_run, select_cases


def test_fixed_chat_cases_cover_required_scenarios() -> None:
    cases = load_cases(PROJECT_ROOT / "tests" / "chat_cases.json")

    assert 20 <= len(cases) <= 40
    assert all(case["expected_behavior"] for case in cases)
    assert all(case["avoid"] for case in cases)
    assert {case["id"] for case in cases} >= {
        "single_question_mark",
        "single_cao",
        "laughter",
        "single_okay",
        "forget_it",
        "two_contacts",
        "during_generation_supplement",
        "memory_driving_followup",
        "memory_cilantro_preference",
        "memory_location_superseded",
        "memory_contact_isolation",
    }


def test_memory_replay_fixtures_remain_inside_their_conversation() -> None:
    cases = load_cases(PROJECT_ROOT / "tests" / "chat_cases.json")
    case = next(item for item in cases if item["id"] == "memory_contact_isolation")

    first, second = case["conversations"]

    assert first["session_id"] == "联系人A"
    assert first["long_term_memories"][0]["content"].startswith("联系人A")
    assert second["session_id"] == "联系人B"
    assert "long_term_memories" not in second


def test_select_cases_rejects_unknown_id() -> None:
    cases = load_cases(PROJECT_ROOT / "tests" / "chat_cases.json")

    with pytest.raises(ConfigurationError, match="missing"):
        select_cases(cases, ["missing"], None)


def test_replay_results_append_without_overwriting(tmp_path: Path) -> None:
    path = tmp_path / "results.json"

    save_run(path, {"run": 1})
    save_run(path, {"run": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == [
        {"run": 1},
        {"run": 2},
    ]
