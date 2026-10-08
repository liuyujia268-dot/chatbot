"""Proxy regression tests: real HTTPX construction, fake HTTP responses only."""
from __future__ import annotations

import asyncio
import logging
import sys
import traceback
from pathlib import Path

import httpx
import pytest

from app.config import ConfigurationError, Settings
from app.core.session import Message
from app.models.base import ModelConnectionError, ModelNetworkConfigurationError
from app.models.factory import build_model
from app.models.openai import OpenAICompatibleModel
from app.ui.settings_store import save_validated_env
from tests.test_openai_compatible import FakeClient, FakeResponse


def model(**options) -> OpenAICompatibleModel:
    return OpenAICompatibleModel(
        base_url="https://model.invalid/v1", api_key="TEST_API_KEY_ONLY",
        model_name="test-model", timeout_seconds=1, max_output_tokens=16,
        **options,
    )


def test_proxy_settings_persist_and_reach_model_factory(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    values = {
        "MODEL_PROVIDER": "openai_compatible",
        "MODEL_BASE_URL": "https://model.invalid/v1",
        "MODEL_API_KEY": "TEST_API_KEY_ONLY",
        "MODEL_NAME": "test-model",
        "MODEL_PROXY_MODE": "custom",
        "MODEL_PROXY_URL": "socks5h://TEST_PROXY_USER:TEST_PROXY_PASS@localhost:1080",
        "MEMORY_FAILURE_RETRY_SECONDS": "30",
    }
    save_validated_env(path, values)
    settings = Settings.from_env(path, environ={})
    provider = build_model(settings)
    assert settings.model_proxy_mode == provider._proxy_mode == "custom"
    assert settings.model_proxy_url == provider._proxy_url == values["MODEL_PROXY_URL"]
    assert settings.memory_failure_retry_seconds == 30
    assert "TEST_PROXY_PASS" not in repr(settings)
    save_validated_env(path, {"MODEL_PROXY_MODE": "direct"})
    assert Settings.from_env(path, environ={}).model_proxy_mode == "direct"


@pytest.mark.parametrize("values", [
    {"MODEL_PROXY_MODE": "unexpected"},
    {"MODEL_PROXY_MODE": "custom"},
    {"MODEL_PROXY_MODE": "custom", "MODEL_PROXY_URL": "ftp://localhost:1080"},
    {"MODEL_PROXY_MODE": "custom", "MODEL_PROXY_URL": "socks5://localhost:99999"},
    {"MODEL_PROXY_MODE": "custom", "MODEL_PROXY_URL": "http://localhost:1080/path"},
    {"MODEL_PROXY_MODE": "custom", "MODEL_PROXY_URL": "http://localhost:1080/?key=TEST_PROXY_PASS"},
    {"MODEL_PROXY_MODE": "custom", "MODEL_PROXY_URL": "socks5://TEST_PROXY_PASS@[bad"},
    {"MEMORY_FAILURE_RETRY_SECONDS": "0"},
])
def test_invalid_network_settings_are_rejected_without_secrets(values) -> None:
    with pytest.raises(ConfigurationError) as caught:
        Settings.from_values(values)
    assert "TEST_PROXY_PASS" not in str(caught.value)


@pytest.mark.parametrize(("mode", "proxy_url", "trust_env"), [
    ("system", None, True),
    ("direct", None, False),
    ("custom", "socks5://localhost:1080", False),
    ("custom", "socks5h://localhost:1080", False),
    ("custom", "http://localhost:1080", False),
])
def test_real_httpx_proxy_initialization_without_network(monkeypatch, mode, proxy_url, trust_env) -> None:
    # getproxies() also reads Windows registry; isolate it from the real machine.
    monkeypatch.setattr("httpx._utils.getproxies", lambda: {"all": "socks5://localhost:1080"})
    original = httpx.AsyncClient
    constructed, requests = [], []

    async def post(self, url, **kwargs):
        requests.append((url, kwargs))
        return FakeResponse(payload={"choices": [{"message": {"content": "离线回复"}}]})

    def client(**kwargs):
        constructed.append(kwargs)
        return original(**kwargs)  # Actually initializes HTTPX's SOCKS transport.

    monkeypatch.setattr(original, "post", post)
    monkeypatch.setattr("app.models.openai.httpx.AsyncClient", client)
    reply = asyncio.run(model(proxy_mode=mode, proxy_url=proxy_url).generate([Message("user", "合成消息")]))
    assert reply == "离线回复" and len(requests) == 1
    expected = {"trust_env": trust_env}
    if proxy_url:
        expected["proxy"] = proxy_url
    assert constructed == [expected]


def test_direct_ignores_invalid_computer_proxy(monkeypatch) -> None:
    monkeypatch.setattr("httpx._utils.getproxies", lambda: {"all": "invalid-scheme://bad-proxy"})

    async def post(self, *args, **kwargs):
        return FakeResponse(payload={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    assert asyncio.run(model(proxy_mode="direct").generate([Message("user", "hello")])) == "ok"


@pytest.mark.parametrize("failure", [ImportError, ValueError, OSError])
def test_client_initialization_failure_is_safe_model_error(monkeypatch, caplog, failure) -> None:
    def broken(**kwargs):
        raise failure("socks5://TEST_PROXY_USER:TEST_PROXY_PASS@localhost:1080 TEST_API_KEY_ONLY")

    monkeypatch.setattr("app.models.openai.httpx.AsyncClient", broken)
    with caplog.at_level(logging.WARNING), pytest.raises(ModelNetworkConfigurationError) as caught:
        asyncio.run(model().generate([Message("user", "hello")]))
    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb)) + caplog.text
    assert "TEST_PROXY_PASS" not in rendered and "TEST_API_KEY_ONLY" not in rendered
    assert "error_type=" + failure.__name__ in caplog.text
    assert "direct" in str(caught.value)


def test_missing_socks_component_maps_to_actionable_error(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "socksio", None)
    monkeypatch.setattr("httpx._utils.getproxies", lambda: {"all": "socks5://localhost:1080"})
    with pytest.raises(ModelNetworkConfigurationError, match="SOCKS"):
        asyncio.run(model().generate([Message("user", "hello")]))


def test_proxy_request_error_does_not_expose_credentials(caplog) -> None:
    client = FakeClient(error=httpx.ProxyError("TEST_PROXY_PASS TEST_API_KEY_ONLY"))
    with caplog.at_level(logging.WARNING), pytest.raises(ModelConnectionError) as caught:
        asyncio.run(model(client=client).generate([Message("user", "hello")]))
    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb)) + caplog.text
    assert "TEST_PROXY_PASS" not in rendered and "TEST_API_KEY_ONLY" not in rendered


def test_proxy_credentials_redacted_from_server_error(caplog) -> None:
    proxy_url = "socks5://TEST_PROXY_USER:TEST_PROXY_PASS@localhost:1080"
    client = FakeClient(FakeResponse(status_code=500, payload={"error": {
        "message": f"rejected {proxy_url} TEST_PROXY_USER TEST_PROXY_PASS TEST_API_KEY_ONLY",
    }}))
    from app.models.base import ModelServiceError

    with caplog.at_level(logging.ERROR), pytest.raises(ModelServiceError):
        asyncio.run(model(client=client, proxy_url=proxy_url).generate([Message("user", "hello")]))
    assert not any(secret in caplog.text for secret in ("TEST_PROXY_USER", "TEST_PROXY_PASS", "TEST_API_KEY_ONLY"))
