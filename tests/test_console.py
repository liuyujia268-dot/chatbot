from __future__ import annotations

import pytest

from app.adapters.console import parse_contact_message, parse_takeover_command


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("[张三] 你好", ("张三", "你好")),
        (" [李四]   在吗？ ", ("李四", "在吗？")),
    ],
)
def test_parse_contact_message(line: str, expected: tuple[str, str]) -> None:
    assert parse_contact_message(line) == expected


def test_parse_contact_message_rejects_invalid_format() -> None:
    with pytest.raises(ValueError):
        parse_contact_message("张三：你好")


def test_parse_takeover_command() -> None:
    assert parse_takeover_command("/takeover 张三 on") == ("张三", True)
    assert parse_takeover_command("/takeover 张三 OFF") == ("张三", False)
    assert parse_takeover_command("[张三] hello") is None
