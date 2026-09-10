from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.adapters.image_resolver import ImageResolutionError, ImageResolver
from app.adapters.onebot import parse_onebot_event
from app.core.platform import MessagePart
from app.core.session import Message
from app.core.reply_engine import ReplyEngine
from app.core.session import Session
from app.models.openai import OpenAICompatibleModel


def onebot_message(parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "post_type": "message",
        "message_type": "private",
        "self_id": 10001,
        "user_id": 20002,
        "message_id": 30003,
        "time": 1788796800,
        "sender": {"nickname": "宇飞"},
        "message": parts,
    }


def image_segment(**overrides: Any) -> dict[str, Any]:
    data = {
        "file": "image.jpg",
        "url": "https://images.example.test/image.jpg",
        "summary": "测试图片",
        "file_size": "1234",
        "sub_type": 0,
    }
    data.update(overrides)
    return {"type": "image", "data": data}


def test_parses_pure_image_private_message() -> None:
    parsed = parse_onebot_event(onebot_message([image_segment()]))

    assert parsed is not None
    assert parsed.message.message_type == "image"
    assert parsed.message.text == ""
    image = parsed.message.parts[0]
    assert image.part_type == "image"
    assert image.file == "image.jpg"
    assert image.url == "https://images.example.test/image.jpg"
    assert image.summary == "测试图片"
    assert image.file_size == 1234
    assert image.sub_type == 0


def test_text_image_text_order_is_preserved() -> None:
    parsed = parse_onebot_event(
        onebot_message(
            [
                {"type": "text", "data": {"text": "前面"}},
                image_segment(),
                {"type": "text", "data": {"text": "后面"}},
            ]
        )
    )

    assert parsed is not None
    assert [part.part_type for part in parsed.message.parts] == [
        "text", "image", "text"
    ]
    assert parsed.message.storage_text.splitlines() == [
        "前面", "[图片：测试图片]", "后面"
    ]


def test_qq_face_is_preserved_as_safe_text() -> None:
    parsed = parse_onebot_event(
        onebot_message(
            [
                {
                    "type": "face",
                    "data": {
                        "id": "123",
                        "raw": {"faceText": "坚强"},
                        "chainCount": 2,
                    },
                }
            ]
        )
    )

    assert parsed is not None
    assert parsed.message.storage_text == "[QQ表情：坚强]×2"


class _ModelResponse:
    status_code = 200

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "看到了"}}]}


class _ModelClient:
    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None

    async def post(self, url: str, **kwargs: Any) -> _ModelResponse:
        self.request = kwargs
        return _ModelResponse()


def test_provider_builds_ordered_multimodal_request() -> None:
    async def scenario() -> None:
        client = _ModelClient()
        model = OpenAICompatibleModel(
            base_url="https://model.example.test/v1",
            api_key="fake",
            model_name="vision",
            timeout_seconds=1,
            max_output_tokens=64,
            client=client,
        )
        content = (
            MessagePart.text_part("这是什么"),
            MessagePart(
                part_type="image",
                url="https://images.example.test/image.jpg",
            ),
            MessagePart.text_part("看得清吗"),
        )

        reply = await model.generate([Message("user", content)])

        assert reply == "看到了"
        assert client.request is not None
        parts = client.request["json"]["messages"][0]["content"]
        assert parts == [
            {"type": "text", "text": "这是什么"},
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://images.example.test/image.jpg"
                },
            },
            {"type": "text", "text": "看得清吗"},
        ]

    asyncio.run(scenario())


class _StreamResponse:
    def __init__(self, body: bytes, content_type: str) -> None:
        self.body = body
        self.headers = {
            "content-type": content_type,
            "content-length": str(len(body)),
        }

    async def __aenter__(self) -> "_StreamResponse":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self):
        yield self.body


class _StreamClient:
    def __init__(self, response: _StreamResponse) -> None:
        self.response = response

    def stream(self, *args: Any, **kwargs: Any) -> _StreamResponse:
        return self.response


class _FailingStream:
    async def __aenter__(self):
        raise httpx.ConnectError("offline")

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FailingStreamClient:
    def stream(self, *args: Any, **kwargs: Any) -> _FailingStream:
        return _FailingStream()


@pytest.mark.parametrize(
    ("body", "mime", "max_bytes", "expected"),
    [
        (b"not-an-image", "text/plain", 100, "远程内容不是图片"),
        (b"x" * 11, "image/png", 10, "图片超过大小限制"),
    ],
)
def test_image_download_rejects_unsafe_content(
    body: bytes,
    mime: str,
    max_bytes: int,
    expected: str,
) -> None:
    async def scenario() -> None:
        resolver = ImageResolver(
            max_bytes=max_bytes,
            timeout_seconds=1,
            allow_remote_url=False,
            client=_StreamClient(_StreamResponse(body, mime)),
        )
        part = MessagePart(
            part_type="image",
            url="https://images.example.test/image",
        )

        with pytest.raises(ImageResolutionError, match=expected):
            await resolver.resolve(part)

    asyncio.run(scenario())


def test_declared_oversized_remote_image_is_rejected_without_download() -> None:
    async def scenario() -> None:
        resolver = ImageResolver(
            max_bytes=10,
            timeout_seconds=1,
            allow_remote_url=True,
        )
        with pytest.raises(ImageResolutionError, match="图片超过大小限制"):
            await resolver.resolve(
                MessagePart(
                    part_type="image",
                    url="https://images.example.test/large.jpg",
                    file_size=11,
                )
            )

    asyncio.run(scenario())


def test_image_download_failure_is_reported_safely() -> None:
    async def scenario() -> None:
        resolver = ImageResolver(
            max_bytes=100,
            timeout_seconds=1,
            allow_remote_url=False,
            client=_FailingStreamClient(),
        )
        with pytest.raises(ImageResolutionError, match="图片下载失败"):
            await resolver.resolve(
                MessagePart(
                    part_type="image",
                    url="https://images.example.test/offline.jpg",
                )
            )

    asyncio.run(scenario())


def test_historical_image_uses_description_instead_of_original_url() -> None:
    client = _ModelClient()
    model = OpenAICompatibleModel(
        base_url="https://model.example.test/v1",
        api_key="fake",
        model_name="vision",
        timeout_seconds=1,
        max_output_tokens=64,
        client=client,
    )
    session = Session("qq:private:20002")
    session.add_message(
        "user",
        (
            MessagePart(
                part_type="image",
                url="https://images.example.test/old-secret-url.jpg",
                description="Windows 报错窗口",
            ),
        ),
    )
    session.add_message("assistant", "这是一个报错窗口")
    session.add_message("user", "刚才那个怎么办")

    messages = ReplyEngine(model, image_history_limit=0).build_model_messages(
        session
    )

    old = messages[0]
    assert not isinstance(old.content, str)
    assert old.content[0].part_type == "text"
    assert old.content[0].text == "[图片：Windows 报错窗口]"
    assert "old-secret-url" not in repr(messages)


def test_most_recent_image_can_accompany_following_text_message() -> None:
    client = _ModelClient()
    model = OpenAICompatibleModel(
        base_url="https://model.example.test/v1",
        api_key="fake",
        model_name="vision",
        timeout_seconds=1,
        max_output_tokens=64,
        client=client,
    )
    session = Session("qq:private:20002")
    session.add_message(
        "user",
        (
            MessagePart(
                part_type="image",
                url="https://images.example.test/current.jpg",
            ),
        ),
    )
    session.add_message("user", "能看到这个图吗")

    messages = ReplyEngine(model, image_history_limit=1).build_model_messages(
        session
    )

    image_message = messages[-2]
    assert not isinstance(image_message.content, str)
    assert image_message.content[0].model_image_url.endswith("current.jpg")
    assert messages[-1].content == "能看到这个图吗"
