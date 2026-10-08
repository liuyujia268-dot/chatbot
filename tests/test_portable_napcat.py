"""Portable delivery checks with synthetic installations, never real QQ/NapCat."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from unittest.mock import patch

import pytest

from app.ui.napcat_webui import find_napcat_launcher


SUPPORT = Path(__file__).resolve().parents[1] / "packaging"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


portable = load_module("test_portable_helper", SUPPORT / "portable_napcat.py")
entry = load_module("test_portable_entry", SUPPORT / "desktop_entry.py")
with patch.dict(sys.modules, {"portable_napcat": portable}):
    builder = load_module("test_portable_builder", SUPPORT / "build_portable.py")


def test_application_update_archive_preserves_all_mutable_customer_files(tmp_path):
    source = tmp_path / "release"
    source.mkdir()
    (source / "Chatbot.exe").write_bytes(b"NEW_FAKE_EXE")
    (source / "runtime.dll").write_bytes(b"NEW_FAKE_DLL")
    private_files = [".env", ".env.example", ".env.backup", "data/customer.db",
                     "app/prompts/chat.txt", "NapCat.Shell/config/webui.json"]
    secret = b"PRIVATE_CUSTOMER_CONTENT_MUST_NOT_SHIP"
    for relative in private_files:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secret)
    archive = tmp_path / "update.zip"
    builder.write_update_archive(source, archive, {"built_at": "synthetic"})
    target = tmp_path / "customer" / "Chatbot"
    for relative in private_files:
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secret)
    with zipfile.ZipFile(archive) as handle:
        assert not any(secret in handle.read(name) for name in handle.namelist())
        handle.extractall(target.parent)
    assert all((target / relative).read_bytes() == secret for relative in private_files)
    assert (target / "Chatbot.exe").read_bytes() == b"NEW_FAKE_EXE"
    assert (target / "runtime.dll").read_bytes() == b"NEW_FAKE_DLL"


def test_release_documentation_is_allowlisted_and_includes_acknowledgement(tmp_path, monkeypatch):
    source = tmp_path / "workspace"
    for relative in builder.RELEASE_DOCUMENTS:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"public document: {relative}", encoding="utf-8")
    secret = "PRIVATE_ARCHIVE_DATA_MUST_NOT_SHIP_7F92A1"
    for relative in (".env", "data/private.db", "docs/private-note.txt"):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret, encoding="utf-8")
    monkeypatch.setattr(builder, "ROOT", source)
    distribution = tmp_path / "Chatbot"
    builder.copy_release_documentation(distribution)
    files = {path.relative_to(distribution).as_posix()
             for path in distribution.rglob("*") if path.is_file()}
    assert files == set(builder.RELEASE_DOCUMENTS)
    assert all(secret not in path.read_text(encoding="utf-8")
               for path in distribution.rglob("*") if path.is_file())
    assert "感谢 NapCatQQ" in (SUPPORT / "README-portable.txt").read_text(encoding="utf-8")


def synthetic_installation(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in portable.ROOT_FILES:
        (source / name).write_text("synthetic component", encoding="utf-8")
    (source / "napcat.mjs").write_text('const version = "4.18.19" || "1.0.0-dev";', encoding="utf-8")
    for name in portable.RUNTIME_FOLDERS:
        folder = source / name
        folder.mkdir()
        (folder / "runtime.txt").write_text("synthetic runtime", encoding="utf-8")
    secret = "DO_NOT_PACKAGE_SOURCE_SECRET_7F92A1"
    for name in ("config", "cache", "logs"):
        folder = source / name
        folder.mkdir()
        (folder / "private.json").write_text(secret, encoding="utf-8")
    for name in ("guild1.db", "saved.log", "account.lnk", "loadNapCat.js"):
        (source / name).write_text(secret, encoding="utf-8")
    return source, secret


def clean_bundle(tmp_path):
    root = tmp_path / "portable"
    config = root / "NapCat.Shell" / "config"
    config.mkdir(parents=True)
    (config.parent / portable.MARKER_NAME).write_text(json.dumps({
        "owner": portable.OWNER, "schema": 1, "initialized": False}), encoding="utf-8")
    (config / "webui.json").write_text(json.dumps(portable.webui_template()), encoding="utf-8")
    (config / "onebot11.json").write_text(json.dumps(portable.onebot_template()), encoding="utf-8")
    (root / ".env").write_text("MODEL_PROVIDER=mock\nQQ_DRY_RUN=true\nQQ_AUTO_REPLY_ENABLED=false\n", encoding="utf-8")
    return root, config


def test_bundle_excludes_login_config_cache_database_and_absolute_loader(tmp_path):
    source, secret = synthetic_installation(tmp_path)
    before = (source / "config" / "private.json").read_bytes()
    target = tmp_path / "distribution" / "NapCat.Shell"
    result = portable.copy_clean_napcat(source, target, SUPPORT)
    assert result["version"] == "4.18.19" and not result["initialized"]
    assert (source / "config" / "private.json").read_bytes() == before
    assert not (target / "cache").exists() and not (target / "logs").exists()
    assert {p.name for p in (target / "config").iterdir()} == {"webui.json", "napcat.json", "onebot11.json"}
    assert all(secret.encode() not in p.read_bytes() for p in target.rglob("*") if p.is_file())
    assert "pathToFileURL" in (target / "loadNapCat.js").read_text(encoding="utf-8")
    assert (target / "LICENSE.txt").is_file() and (target / "NOTICE.txt").is_file()


def test_missing_runtime_fails_without_downloading(tmp_path):
    source, _ = synthetic_installation(tmp_path)
    (source / "NapCatWinBootMain.exe").unlink()
    with pytest.raises(RuntimeError, match="missing"):
        portable.copy_clean_napcat(source, tmp_path / "target", SUPPORT)
    assert not (tmp_path / "target").exists()


def test_copy_refuses_existing_distribution(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    with pytest.raises(RuntimeError, match="overwrite"):
        portable.copy_clean_napcat(tmp_path / "missing-source", target, SUPPORT)


def test_first_run_creates_independent_local_tokens_and_aligned_ports(tmp_path):
    root, config = clean_bundle(tmp_path)
    calls = []

    def choose(port, **kwargs):
        calls.append((port, kwargs))
        return port + 10

    assert portable.prepare_first_run(root, select_port=choose)
    webui = json.loads((config / "webui.json").read_text(encoding="utf-8"))
    server = json.loads((config / "onebot11.json").read_text(encoding="utf-8"))["network"]["websocketServers"][0]
    assert webui["host"] == server["host"] == "127.0.0.1"
    assert webui["token"] and server["token"] and webui["token"] != server["token"]
    assert server["enable"] and server["reportSelfMessage"]
    env = (root / ".env").read_text(encoding="utf-8")
    assert "http://127.0.0.1:6109/webui/" in env and "ws://127.0.0.1:3011" in env
    assert "QQ_WS_TOKEN=" + server["token"] in env
    assert "MODEL_PROVIDER=mock" in env and "QQ_DRY_RUN=true" in env and "QQ_AUTO_REPLY_ENABLED=false" in env
    assert calls == [(6099, {}), (3001, {"exclude": {6109}})]


def test_later_starts_preserve_model_and_napcat_configuration(tmp_path):
    root, config = clean_bundle(tmp_path)
    portable.prepare_first_run(root, select_port=lambda port, **_: port)
    env = root / ".env"
    env.write_text(env.read_text(encoding="utf-8").replace("MODEL_PROVIDER=mock", "MODEL_PROVIDER=openai_compatible"), encoding="utf-8")
    before = {p: p.read_bytes() for p in (env, config / "webui.json", config / "onebot11.json")}
    assert portable.prepare_first_run(root, select_port=lambda *_, **__: pytest.fail("must not reset ports"))
    assert all(path.read_bytes() == contents for path, contents in before.items())


def test_unowned_external_installation_is_not_initialized(tmp_path):
    root = tmp_path / "ordinary-project"
    root.mkdir()
    assert portable.prepare_first_run(root) is False
    assert list(root.iterdir()) == []


def test_unknown_owner_refused_without_changing_configuration(tmp_path):
    root, config = clean_bundle(tmp_path)
    marker = config.parent / portable.MARKER_NAME
    marker.write_text(json.dumps({"owner": "someone-else", "schema": 1}), encoding="utf-8")
    before = (config / "webui.json").read_bytes()
    with pytest.raises(RuntimeError, match="ownership"):
        portable.prepare_first_run(root)
    assert (config / "webui.json").read_bytes() == before


def test_setup_refuses_junction_even_if_it_resolves_inside_root(tmp_path, monkeypatch):
    root, config = clean_bundle(tmp_path)
    monkeypatch.setattr(Path, "is_junction", lambda path: path == config)
    with pytest.raises(RuntimeError, match="junctions"):
        portable.prepare_first_run(root, select_port=lambda port, **_: port)


def test_port_selection_skips_occupied_and_excluded_ports(monkeypatch):
    attempts = []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            attempts.append(address)
            if address[1] == 6099:
                raise OSError("synthetic occupied port")

    monkeypatch.setattr(portable.socket, "socket", lambda *args: Socket())
    assert portable.available_port(6099, exclude={6100}) == 6101
    assert attempts == [("127.0.0.1", 6099), ("127.0.0.1", 6101)]


def test_relocatable_loader_uses_chinese_space_path_without_qq(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Existing Node not available; nothing will be installed")
    folder = tmp_path / "验证 中文 空格"
    folder.mkdir()
    module = folder / "synthetic-runtime.mjs"
    module.write_text('console.log("SYNTHETIC_LOADER_OK");\n', encoding="utf-8")
    environment = dict(os.environ, NAPCAT_MAIN_PATH=str(module))
    result = subprocess.run([node, str(SUPPORT / "napcat_templates" / "loadNapCat.js")],
                            cwd=folder, env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "SYNTHETIC_LOADER_OK" in result.stdout


class MissingRegistry:
    HKEY_CURRENT_USER = "user"
    HKEY_LOCAL_MACHINE = "machine"
    KEY_WOW64_32KEY = 1
    KEY_WOW64_64KEY = 2
    KEY_READ = 4

    @staticmethod
    def OpenKey(*args):
        raise FileNotFoundError("synthetic missing registry")


def test_missing_qq_reports_absence_without_starting_or_installing():
    assert portable.find_qq_executable(environ={}, registry=MissingRegistry) is None


def test_custom_qq_path_with_spaces_is_accepted_without_registry(tmp_path):
    qq = tmp_path / "QQ installed here" / "QQ.exe"
    qq.parent.mkdir()
    qq.write_bytes(b"synthetic executable, never run")
    assert portable.find_qq_executable(environ={"CHATBOT_QQ_EXE": str(qq)}, registry=MissingRegistry) == qq


def test_qq_registry_uninstall_command_handles_quotes_and_arguments(tmp_path):
    qq = tmp_path / "QQ space" / "QQ.exe"
    qq.parent.mkdir()
    qq.write_bytes(b"synthetic")

    class Registry(MissingRegistry):
        @staticmethod
        def OpenKey(*args):
            class Key:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass
            return Key()

        @staticmethod
        def QueryValueEx(key, field):
            if field == "InstallLocation":
                raise FileNotFoundError(field)
            return f'"{qq.parent / "Uninstall.exe"}" /silent', 1

    assert portable.find_qq_executable(environ={}, registry=Registry) == qq


def test_bundled_launcher_preferred_without_changing_external_installation(tmp_path):
    root, config = clean_bundle(tmp_path)
    original = config.parent / "launcher-user.bat"
    original.write_text("@echo off\n", encoding="utf-8")
    bundled = config.parent / "chatbot-launcher.cmd"
    bundled.write_text("@echo off\n", encoding="utf-8")
    assert find_napcat_launcher(config / "webui.json") == bundled
    (config.parent / portable.MARKER_NAME).unlink()
    assert find_napcat_launcher(config / "webui.json") == original


@pytest.mark.parametrize("installed", [True, False])
def test_exe_startup_autoconnect_or_missing_qq_hint(tmp_path, monkeypatch, installed):
    root, _ = clean_bundle(tmp_path)
    monkeypatch.setitem(sys.modules, "portable_napcat", portable)
    monkeypatch.setattr(portable, "available_port", lambda port, **_: port)
    monkeypatch.setattr(portable, "find_qq_executable", lambda: root / "QQ.exe" if installed else None)
    monkeypatch.setattr(entry, "program_directory", lambda: root)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["Chatbot.exe"])
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr("app.ui.desktop.run", lambda **kwargs: calls.append(kwargs))
    assert entry.main() == 0
    assert calls[0]["auto_open_qq"] is installed
    assert bool(calls[0]["startup_hint"]) is not installed


def test_self_test_never_initializes_real_bundle(tmp_path, monkeypatch):
    root, config = clean_bundle(tmp_path)
    before = (config / "webui.json").read_bytes()
    monkeypatch.setattr(entry, "program_directory", lambda: root)
    monkeypatch.setattr(entry, "self_test", lambda output: 0)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["Chatbot.exe", "--self-test", str(tmp_path / "result.json")])
    monkeypatch.chdir(tmp_path)
    assert entry.main() == 0
    assert (config / "webui.json").read_bytes() == before
