from pathlib import Path

from app.config import Settings
from app.ui.settings_store import read_env_values, update_env_file


def test_env_editor_preserves_unrelated_secrets_and_comments(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text(
        "# keep this comment\nMODEL_API_KEY=do-not-touch\nMODEL_NAME=old\n",
        encoding="utf-8",
    )

    update_env_file(target, {"MODEL_NAME": "new", "QQ_DRY_RUN": "true"})

    content = target.read_text(encoding="utf-8")
    assert "# keep this comment" in content
    assert "MODEL_API_KEY=do-not-touch" in content
    assert "MODEL_NAME=new" in content
    assert "QQ_DRY_RUN=true" in content


def test_desktop_settings_are_read_from_env(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text(
        "QQ_WEBUI_URL=http://127.0.0.1:6099/webui/\n"
        "QQ_WEBUI_TOKEN=webui-secret\n"
        "QQ_WEBUI_CONFIG_PATH=C:/NapCat.Shell/config/webui.json\n"
        "QQ_AUTO_REPLY_ENABLED=false\n"
        "DESKTOP_AUTO_SHOW_MEDIA=true\n"
        "DESKTOP_HISTORY_MESSAGE_LIMIT=80\n",
        encoding="utf-8",
    )

    settings = Settings.from_env(env_file=target, environ={})

    assert settings.qq_webui_url.endswith("/webui/")
    assert settings.qq_webui_token == "webui-secret"
    assert settings.qq_webui_config_path == Path(
        "C:/NapCat.Shell/config/webui.json"
    )
    assert "webui-secret" not in repr(settings)
    assert settings.qq_auto_reply_enabled is False
    assert settings.desktop_auto_show_media is True
    assert settings.desktop_history_message_limit == 80
    assert read_env_values(target)["QQ_AUTO_REPLY_ENABLED"] == "false"


def test_desktop_module_import_does_not_create_tk_root() -> None:
    import app.ui.desktop as desktop

    assert callable(desktop.run)
