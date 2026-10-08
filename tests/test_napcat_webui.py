import json
from pathlib import Path

from app.ui.napcat_webui import (
    discover_webui_config,
    find_napcat_launcher,
    resolve_webui_launch,
    should_launch_webui,
    start_napcat_launcher,
    webui_is_ready,
)


SECRET = "TEST_WEBUI_SECRET_7F92A1"


def test_explicit_token_is_appended_and_preserves_query() -> None:
    launch = resolve_webui_launch(
        "http://127.0.0.1:6099/webui/?theme=dark",
        explicit_token=SECRET,
        search_roots=(),
    )

    assert launch.authenticated is True
    assert launch.token_source == "explicit"
    assert "/webui/web_login?" in launch.url
    assert "theme=dark" in launch.url
    assert f"token={SECRET}" in launch.url
    assert SECRET not in repr(launch)


def test_explicit_token_replaces_url_token_without_duplication() -> None:
    launch = resolve_webui_launch(
        "http://127.0.0.1:6099/webui/?token=old",
        explicit_token=SECRET,
        search_roots=(),
    )

    assert launch.url.count("token=") == 1
    assert "token=old" not in launch.url
    assert f"token={SECRET}" in launch.url


def test_existing_url_token_is_used_when_no_explicit_token() -> None:
    launch = resolve_webui_launch(
        f"http://127.0.0.1:6099/webui/?token={SECRET}",
        search_roots=(),
    )

    assert launch.authenticated is True
    assert launch.token_source == "url"
    assert launch.url.count("token=") == 1


def test_local_webui_config_is_discovered_without_persisting_token(
    tmp_path: Path,
) -> None:
    config = tmp_path / "NapCat.Shell" / "config" / "webui.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"port": 6099, "token": SECRET}), encoding="utf-8")

    launch = resolve_webui_launch(
        "http://127.0.0.1:6099/webui/",
        search_roots=(tmp_path,),
    )

    assert launch.authenticated is True
    assert launch.token_source == "config"
    assert launch.config_path == config
    assert f"token={SECRET}" in launch.url
    assert SECRET not in repr(launch)


def test_missing_or_invalid_config_falls_back_without_crashing(
    tmp_path: Path,
) -> None:
    config = tmp_path / "webui.json"
    config.write_text("not-json", encoding="utf-8")

    launch = resolve_webui_launch(
        "http://127.0.0.1:6099/webui/",
        explicit_config_path=config,
        search_roots=(),
    )

    assert launch.authenticated is False
    assert launch.token_source == "missing"
    assert launch.url == "http://127.0.0.1:6099/webui"


def test_napcat_launcher_is_found_beside_config_and_started_in_its_directory(
    tmp_path: Path,
) -> None:
    config = tmp_path / "NapCat.Shell" / "config" / "webui.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"token": SECRET}), encoding="utf-8")
    launcher = config.parent.parent / "launcher-user.bat"
    launcher.write_text("@echo off\n", encoding="utf-8")
    calls: list[tuple[list[str], dict]] = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    found = find_napcat_launcher(config)
    assert found == launcher
    start_napcat_launcher(found, popen=fake_popen)

    assert calls[0][0][-1] == str(launcher)
    assert calls[0][1]["cwd"] == launcher.parent
    assert calls[0][1]["stdin"] is not None


def test_webui_readiness_uses_configured_host_and_port() -> None:
    calls = []

    class Connection:
        def close(self) -> None:
            calls.append("closed")

    def connector(address, *, timeout):
        calls.append((address, timeout))
        return Connection()

    assert webui_is_ready(
        "http://127.0.0.1:6099/webui",
        connector=connector,
    )
    assert calls[0][0] == ("127.0.0.1", 6099)
    assert calls[-1] == "closed"


def test_failed_or_incomplete_login_can_always_be_retried() -> None:
    assert should_launch_webui(
        starting=False,
        opened=False,
        account_id=None,
    )
    assert should_launch_webui(
        starting=False,
        opened=True,
        account_id=None,
    )
    assert not should_launch_webui(
        starting=True,
        opened=False,
        account_id=None,
    )
    assert not should_launch_webui(
        starting=False,
        opened=True,
        account_id="current-account",
    )


def test_explicit_config_short_circuits_all_directory_search(tmp_path, monkeypatch):
    config = tmp_path / 'webui.json'
    config.write_text(json.dumps({'token': SECRET}), encoding='utf-8')
    monkeypatch.setattr('app.ui.napcat_webui._default_roots', lambda: (_ for _ in ()).throw(AssertionError('no scan')))
    monkeypatch.setattr(Path, 'glob', lambda *args: (_ for _ in ()).throw(AssertionError('no glob')))
    assert discover_webui_config(explicit_config_path=config) == config


def test_common_config_location_does_not_scan_unrelated_folders(tmp_path, monkeypatch):
    config = tmp_path / 'NapCat.Shell' / 'config' / 'webui.json'
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({'token': SECRET}), encoding='utf-8')
    monkeypatch.setattr(Path, 'glob', lambda *args: (_ for _ in ()).throw(AssertionError('no glob')))
    assert discover_webui_config(search_roots=[tmp_path]) == config


def test_running_webui_with_known_token_does_not_search_installation(monkeypatch):
    monkeypatch.setattr('app.ui.napcat_webui.discover_webui_config', lambda **kwargs: (_ for _ in ()).throw(AssertionError('no disk search')))
    launch = resolve_webui_launch('http://localhost:6099/webui', explicit_token=SECRET, discover_config=False)
    assert launch.authenticated and launch.config_path is None
    assert f'token={SECRET}' in launch.url
