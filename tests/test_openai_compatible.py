from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest

from app.core.session import Message
from app.models.base import (
    EmptyModelResponseError,
    InvalidModelResponseError,
    ModelAuthenticationError,
    ModelConnectionError,
    ModelNotFoundError,
    ModelServiceError,
    ModelTimeoutError,
)
from app.models.openai import OpenAICompatibleModel


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        text: str = "",
        json_error: bool = False,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error:
            raise ValueError("invalid json")
        return self._payload


class FakeClient:
    def __init__(
        self,
        response: FakeResponse | None = None,
        *,
        delay: float = 0,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.delay = delay
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


def make_model(
    client: FakeClient,
    *,
    timeout_seconds: float = 1,
    temperature: float | None = 0.7,
) -> OpenAICompatibleModel:
    return OpenAICompatibleModel(
        base_url="http://model.test/v1",
        api_key="fake-key",
        model_name="fake-model",
        timeout_seconds=timeout_seconds,
        max_output_tokens=123,
        temperature=temperature,
        client=client,
    )


def test_openai_compatible_model_parses_response_and_builds_request() -> None:
    async def scenario() -> None:
        client = FakeClient(
            FakeResponse(
                payload={
                    "choices": [{"message": {"role": "assistant", "content": " 你好 "}}]
                }
            )
        )
        model = make_model(client)

        reply = await model.generate(
            [Message("system", "系统提示"), Message("user", "你好")]
        )

        assert reply == "你好"
        url, request = client.calls[0]
        assert url == "http://model.test/v1/chat/completions"
        assert request["json"]["model"] == "fake-model"
        assert request["json"]["max_tokens"] == 123
        assert request["json"]["temperature"] == 0.7
        assert request["json"]["messages"] == [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "你好"},
        ]
        assert request["headers"]["Authorization"] == "Bearer fake-key"

    asyncio.run(scenario())


def test_temperature_is_omitted_when_disabled() -> None:
    async def scenario() -> None:
        client = FakeClient(
            FakeResponse(payload={"choices": [{"message": {"content": "ok"}}]})
        )
        model = make_model(client, temperature=None)

        await model.generate([Message("user", "hello")])

        assert "temperature" not in client.calls[0][1]["json"]

    asyncio.run(scenario())


def test_openai_compatible_model_handles_timeout() -> None:
    async def scenario() -> None:
        client = FakeClient(
            FakeResponse(payload={"choices": []}),
            delay=1,
        )
        model = make_model(client, timeout_seconds=0.01)

        with pytest.raises(ModelTimeoutError):
            await model.generate([Message("user", "hello")])

    asyncio.run(scenario())


def test_openai_compatible_model_handles_connection_error() -> None:
    async def scenario() -> None:
        client = FakeClient(error=httpx.ConnectError("offline"))
        model = make_model(client)

        with pytest.raises(ModelConnectionError):
            await model.generate([Message("user", "hello")])

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, ModelAuthenticationError),
        (404, ModelNotFoundError),
        (500, ModelServiceError),
    ],
)
def test_openai_compatible_model_maps_api_errors(
    status_code: int,
    error_type: type[Exception],
) -> None:
    async def scenario() -> None:
        client = FakeClient(
            FakeResponse(
                status_code=status_code,
                payload={"error": {"message": "provider error"}},
            )
        )
        model = make_model(client)

        with pytest.raises(error_type):
            await model.generate([Message("user", "hello")])

    asyncio.run(scenario())


def test_openai_compatible_model_rejects_empty_reply() -> None:
    async def scenario() -> None:
        client = FakeClient(
            FakeResponse(payload={"choices": [{"message": {"content": "   "}}]})
        )
        model = make_model(client)

        with pytest.raises(EmptyModelResponseError):
            await model.generate([Message("user", "hello")])

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(json_error=True),
        FakeResponse(payload={"unexpected": True}),
        FakeResponse(payload={"choices": [{"message": {"content": 123}}]}),
    ],
)
def test_openai_compatible_model_rejects_invalid_response(
    response: FakeResponse,
) -> None:
    async def scenario() -> None:
        model = make_model(FakeClient(response))

        with pytest.raises(InvalidModelResponseError):
            await model.generate([Message("user", "hello")])

    asyncio.run(scenario())


def test_api_key_is_redacted_from_error_log(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        client = FakeClient(
            FakeResponse(
                status_code=500,
                payload={"error": {"message": "rejected fake-key"}},
            )
        )
        model = make_model(client)

        with caplog.at_level(logging.ERROR):
            with pytest.raises(ModelServiceError):
                await model.generate([Message("user", "hello")])

        assert "fake-key" not in caplog.text
        assert "rejected ***" in caplog.text

    asyncio.run(scenario())
