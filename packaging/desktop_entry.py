"""Portable desktop entry point and an explicitly offline distribution check."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch


def program_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def self_test(output: Path) -> int:
    """Never read the user's .env/database or connect to external services."""
    from app.config import PROJECT_ROOT, Settings
    from app.core.platform import OutboundMessage, PlatformMessage
    from app.models.mock import MockModel
    from app.models.base import ModelNetworkConfigurationError
    from app.models.openai import OpenAICompatibleModel
    from app.runtime import build_core_runtime
    from app.adapters.outbound import DryRunMessageSender
    from app.ui.desktop import DesktopApp
    from app.ui.settings_store import save_validated_env
    import tkinter as tk

    report = {"status": "running", "python": sys.version.split()[0], "checks": []}
    root = None
    try:
        assert sys.version_info >= (3, 12), "Python 3.12 or later is required"
        assert PROJECT_ROOT == program_directory(), "Resources must be beside the EXE"
        report["checks"].append("portable_resource_root")

        with tempfile.TemporaryDirectory(prefix=".portable-check-", dir=program_directory()) as temporary:
            temporary_root = Path(temporary)
            values = {
                "MODEL_PROVIDER": "mock",
                "MODEL_PROXY_MODE": "direct",
                "CHATBOT_DATABASE_PATH": str(temporary_root / "check.db"),
                "CHATBOT_LOG_PATH": str(temporary_root / "check.log"),
                "MESSAGE_DEBOUNCE_SECONDS": "0",
                "MEMORY_AUTO_PROCESS_ENABLED": "false",
                "QQ_ENABLED": "false",
                "QQ_DRY_RUN": "true",
                "QQ_AUTO_REPLY_ENABLED": "false",
                "QQ_PROACTIVE_ENABLED": "false",
                "QQ_FOLLOWUP_ENABLED": "false",
            }
            settings = Settings.from_values(values)
            for path in (
                settings.system_prompt_path,
                settings.reply_repair_prompt_path,
                settings.memory_prompt_path,
                settings.proactive_prompt_path,
                settings.followup_prompt_path,
            ):
                assert path.is_file() and path.read_text(encoding="utf-8").strip()
            report["checks"].append("all_five_prompts")
            assert (PROJECT_ROOT / "app" / "assets" / "whale_girl_profile.jpg").is_file()
            report["checks"].append("bundled_image_asset")

            from portable_napcat import (
                MARKER_NAME, OWNER, onebot_template, prepare_first_run, webui_template,
            )

            bundled = PROJECT_ROOT / "NapCat.Shell"
            if bundled.is_dir():
                for name in ("napcat.mjs", "NapCatWinBootMain.exe", "NapCatWinBootHook.dll",
                             "loadNapCat.js", "chatbot-launcher.cmd", "LICENSE.txt", MARKER_NAME):
                    assert (bundled / name).is_file()
                report["checks"].append("bundled_napcat_runtime")
            # Exercise setup only in a fresh fixture, not the EXE's real config.
            setup = temporary_root / "setup"
            config = setup / "NapCat.Shell" / "config"
            config.mkdir(parents=True)
            (config.parent / MARKER_NAME).write_text(json.dumps(
                {"owner": OWNER, "schema": 1, "initialized": False}), encoding="utf-8")
            (config / "webui.json").write_text(json.dumps(webui_template()), encoding="utf-8")
            (config / "onebot11.json").write_text(json.dumps(onebot_template()), encoding="utf-8")
            (setup / ".env").write_text("MODEL_PROVIDER=mock\nQQ_DRY_RUN=true\n", encoding="utf-8")
            assert prepare_first_run(setup, select_port=lambda port, **_: port + 10)
            first_webui = json.loads((config / "webui.json").read_text(encoding="utf-8"))
            first_onebot = json.loads((config / "onebot11.json").read_text(encoding="utf-8"))
            server = first_onebot["network"]["websocketServers"][0]
            assert first_webui["token"] and server["token"] and first_webui["token"] != server["token"]
            assert server["enable"] and server["reportSelfMessage"]
            prepared = Settings.from_env(setup / ".env", environ={})
            assert prepared.model_provider == "mock" and prepared.qq_dry_run
            assert prepared.qq_ws_token == server["token"]
            before = (config / "webui.json").read_bytes(), (setup / ".env").read_bytes()
            assert prepare_first_run(setup, select_port=lambda *_, **__: (_ for _ in ()).throw(AssertionError("reset")))
            assert before == ((config / "webui.json").read_bytes(), (setup / ".env").read_bytes())
            report["checks"].extend(["fresh_local_napcat_credentials_and_onebot", "first_run_preserves_later_settings"])

            def blocked(*args, **kwargs):
                raise AssertionError("Portable self-test forbids network and external applications")

            async def blocked_async(*args, **kwargs):
                return blocked(*args, **kwargs)

            with (
                patch("urllib.request.OpenerDirector.open", blocked),
                patch("socket.create_connection", blocked),
                patch("asyncio.open_connection", blocked_async),
                patch("httpx.AsyncClient.send", blocked_async),
                patch("webbrowser.open", blocked),
                patch("subprocess.Popen", blocked),
                patch("app.ui.desktop.DesktopApp._load_settings", lambda self: (settings, None)),
            ):
                async def exercise_model_network():
                    class Response:
                        status_code = 200

                        def json(self):
                            return {"choices": [{"message": {"content": "离线代理测试回复"}}]}

                    async def fake_post(*args, **kwargs):
                        return Response()

                    from app.core.session import Message

                    # Initialize real HTTPX transports, but block real sends.
                    # Isolate Windows registry/env proxy lookup from this PC.
                    with (
                        patch("httpx._utils.getproxies", return_value={"all": "socks5://127.0.0.1:1080"}),
                        patch("httpx.AsyncClient.post", fake_post),
                    ):
                        for mode, url in (("system", None), ("direct", None),
                                          ("custom", "socks5h://127.0.0.1:1080")):
                            provider = OpenAICompatibleModel(
                                base_url="https://model.invalid/v1", api_key="OFFLINE_FAKE_KEY",
                                model_name="offline", timeout_seconds=1, max_output_tokens=16,
                                proxy_mode=mode, proxy_url=url,
                            )
                            assert await provider.generate([Message("user", "离线合成消息")]) == "离线代理测试回复"
                            report["checks"].append("model_proxy_" + mode + "_initialization")
                    with patch("httpx.AsyncClient", side_effect=ImportError("OFFLINE_FAKE_PROXY_SECRET")):
                        try:
                            await provider.generate([Message("user", "离线错误测试")])
                        except ModelNetworkConfigurationError as exc:
                            assert "OFFLINE_FAKE_PROXY_SECRET" not in str(exc)
                        else:
                            raise AssertionError("Proxy dependency failure was not a safe model error")
                    report["checks"].append("safe_proxy_dependency_error")

                asyncio.run(exercise_model_network())
                core = build_core_runtime(settings)

                async def exercise_chat():
                    first = PlatformMessage(
                        platform="console", account_id="portable-test-account",
                        conversation_id="contact-a", sender_id="contact-a",
                        message_id="test-message-1", timestamp=datetime.now(UTC),
                        message_type="text", text="你好，这是离线测试。",
                    )
                    result = await core.merger.submit_platform(first)
                    assert result is not None and result.reply == MockModel.DEFAULT_REPLY
                    assert await core.merger.submit_platform(first) is None
                    second = PlatformMessage(
                        platform="console", account_id="portable-test-account",
                        conversation_id="contact-b", sender_id="contact-b",
                        message_id="test-message-2", timestamp=datetime.now(UTC),
                        message_type="text", text="另一个独立会话。",
                    )
                    result = await core.merger.submit_platform(second)
                    assert result is not None and result.reply == MockModel.DEFAULT_REPLY
                    with core.database.connect() as connection:
                        rows = connection.execute(
                            "SELECT DISTINCT session_id FROM messages WHERE role='user'"
                        ).fetchall()
                    assert {row[0] for row in rows} == {first.session_id, second.session_id}

                    class NoRealSender:
                        async def send(self, message):
                            raise AssertionError("Dry Run called a real sender")

                    sender = DryRunMessageSender(NoRealSender())
                    await sender.send(OutboundMessage("console", "portable-test-account", "contact-a", "候选回复"))
                    assert len(sender.records) == 1

                asyncio.run(exercise_chat())
                report["checks"].extend(["sqlite_and_mock_chat", "persistent_dedup", "separate_contacts", "dry_run_no_transport"])

                test_env = temporary_root / ".env"
                save_validated_env(test_env, values)
                assert Settings.from_env(test_env, environ={}).model_provider == "mock"
                assert Settings.from_env(test_env, environ={}).model_proxy_mode == "direct"
                report["checks"].append("editable_configuration")

                root = tk.Tk()
                root.withdraw()
                app = DesktopApp(root)
                app._env_path = test_env
                root.update_idletasks()
                assert app.runtime is None
                app._show_qq()  # Build widgets, but do not enter _open_qq/connect.
                root.update_idletasks()
                assert app.runtime is None
                app._show_home()
                app._open_settings()
                root.update_idletasks()
                report["checks"].append("tk_home_qq_and_settings_widgets")
                root.destroy()
                root = None
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__, error=str(exc))
    finally:
        if root is not None:
            root.destroy()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


def run_console() -> None:
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import ctypes

        kernel = ctypes.windll.kernel32
        if not kernel.GetConsoleWindow():
            if not kernel.AllocConsole():
                raise RuntimeError("Cannot open a console window")
        kernel.SetConsoleCP(65001)
        kernel.SetConsoleOutputCP(65001)
        sys.stdin = open("CONIN$", encoding="utf-8")
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
    from app.main import run

    run()


def main() -> int:
    directory = program_directory()
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(directory))
    os.chdir(directory)
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        output = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else program_directory() / "self-test.json"
        return self_test(output)
    auto_open_qq = False
    startup_hint = None
    if getattr(sys, "frozen", False):
        from portable_napcat import find_qq_executable, prepare_first_run

        try:
            if prepare_first_run(directory):
                qq = find_qq_executable()
                if qq is not None:
                    os.environ["CHATBOT_QQ_EXE"] = str(qq)
                    auto_open_qq = True
                else:
                    startup_hint = (
                        "本包已内置 NapCat.Shell，但没有找到已安装的 Windows QQ。\n\n"
                        "请先安装官方 Windows QQ，然后重新双击 Chatbot.exe。\n"
                        "不会自动安装 QQ；已有 QQ 安装在自定义位置时，可在 CMD 设置 "
                        "CHATBOT_QQ_EXE 为 QQ.exe 的完整路径后启动。\n\n"
                        "模型默认为 Mock，请在设置中填写自己的模型配置。"
                    )
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            startup_hint = (
                "随包 NapCat 的本地配置初始化失败，未自动启动 QQ。\n"
                "请把原始压缩包完整解压到有写入权限的普通目录，再启动。\n"
                "请勿在压缩包内运行，或覆盖一份已有登录数据的 NapCat。"
            )
    if len(sys.argv) > 1 and sys.argv[1] == "--console":
        run_console()
    else:
        from app.ui.desktop import run

        run(auto_open_qq=auto_open_qq, startup_hint=startup_hint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
