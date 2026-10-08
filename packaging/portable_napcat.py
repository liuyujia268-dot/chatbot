"""Allowlisted NapCat bundling and first-run setup in the portable directory only."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import socket
from pathlib import Path


BUNDLE_NAME = "NapCat.Shell"
MARKER_NAME = "chatbot-bundle.json"
OWNER = "chatbot-portable"
SOURCE_URL = "https://github.com/NapNeko/NapCatQQ"
ROOT_FILES = (
    "napcat.mjs", "NapCatWinBootHook.dll", "NapCatWinBootMain.exe",
    "conout-wiJ7YKRd.js", "package.json", "qqnt.json",
    "launcher-user.bat", "launcher-win10-user.bat",
)
RUNTIME_FOLDERS = ("native", "node_modules", "static", "worker")
PRIVATE_SUFFIXES = {".db", ".db-wal", ".db-shm", ".log", ".jsonl", ".lnk"}
EXCLUDED_FOLDERS = {"config", "cache", "logs", ".git", ".github", "tests", "test", "examples", "__pycache__"}


def webui_template() -> dict:
    return {"host": "127.0.0.1", "port": 6099, "token": "", "loginRate": 10,
            "autoLoginAccount": "", "disableWebUI": False,
            "enable2FA": False, "totpSecret": ""}


def onebot_template() -> dict:
    return {
        "network": {"httpServers": [], "httpSseServers": [], "httpClients": [],
                    "websocketServers": [{"name": "Chatbot-local", "enable": True,
                        "host": "127.0.0.1", "port": 3001, "messagePostFormat": "array",
                        "reportSelfMessage": True, "token": "", "enableForcePushEvent": True,
                        "debug": False, "heartInterval": 30000}],
                    "websocketClients": [], "plugins": []},
        "musicSignUrl": "", "enableLocalFile2Url": False, "parseMultMsg": False,
        "imageDownloadProxy": "",
    }


def _no_link(path: Path) -> None:
    if path.is_symlink() or path.is_junction():
        raise RuntimeError("Portable packaging refuses symlinks and junctions")


def _owned_path(root: Path, *parts: str) -> Path:
    path = root
    for part in parts:
        path = path / part
        _no_link(path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("Portable setup cannot write outside its own directory")
    return path


def _write_json(path: Path, payload: dict) -> None:
    pending = path.with_name(path.name + ".new")
    _no_link(pending)
    pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def copy_clean_napcat(source: Path, target: Path, support: Path) -> dict:
    """Never copy an installation's config, credentials, databases or loader path."""
    _no_link(source)
    if target.exists():
        raise RuntimeError("Refusing to overwrite an existing NapCat distribution")
    for name in ROOT_FILES + RUNTIME_FOLDERS:
        item = source / name
        _no_link(item)
        if not item.exists():
            raise RuntimeError(f"NapCat runtime component is missing: {name}")
    license_path = support / "third_party" / "NapCat-LICENSE.txt"
    if not license_path.is_file():
        raise RuntimeError("NapCat's complete redistribution license is required")
    runtime = (source / "napcat.mjs").read_text(encoding="utf-8")
    match = re.search(r'[\"\'](\d+\.\d+\.\d+)[\"\']\s*\|\|\s*[\"\']1\.0\.0-dev[\"\']', runtime)
    if match is None:
        raise RuntimeError("Cannot identify installed NapCat version; inspect before redistributing")
    target.mkdir(parents=True)
    for name in ROOT_FILES:
        shutil.copyfile(source / name, target / name)
    for name in RUNTIME_FOLDERS:
        folder = source / name
        for item in folder.rglob("*"):
            relative = item.relative_to(source)
            _no_link(item)
            if any(part.casefold() in EXCLUDED_FOLDERS for part in relative.parts):
                continue
            if not item.is_file() or item.suffix.casefold() in PRIVATE_SUFFIXES or item.name.startswith(".env"):
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, destination)
    for name in ("loadNapCat.js", "chatbot-launcher.cmd"):
        shutil.copyfile(support / "napcat_templates" / name, target / name)
    shutil.copyfile(license_path, target / "LICENSE.txt")
    shutil.copyfile(support / "third_party" / "NOTICE.txt", target / "NOTICE.txt")
    config = target / "config"
    config.mkdir()
    _write_json(config / "webui.json", webui_template())
    _write_json(config / "onebot11.json", onebot_template())
    _write_json(config / "napcat.json", {"fileLog": True, "consoleLog": True,
                "fileLogLevel": "info", "consoleLogLevel": "info"})
    marker = {"owner": OWNER, "schema": 1, "initialized": False,
              "version": match[1], "source": SOURCE_URL,
              "runtime_sha256": hashlib.sha256((target / "napcat.mjs").read_bytes()).hexdigest()}
    _write_json(target / MARKER_NAME, marker)
    return marker


def available_port(preferred: int, *, exclude: set[int] | None = None) -> int:
    """Briefly bind and close; do not start a listener or resident service."""
    exclude = exclude or set()
    for port in range(preferred, min(preferred + 100, 65536)):
        if port in exclude:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise RuntimeError("No free local NapCat port; close conflicting software and retry")


def prepare_first_run(root: Path, *, select_port=None) -> bool:
    """Initialize only our clean bundle; subsequent starts preserve all settings."""
    root = root.resolve()
    marker_path = _owned_path(root, BUNDLE_NAME, MARKER_NAME)
    if not marker_path.is_file():
        return False
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("owner") != OWNER or marker.get("schema") != 1:
        raise RuntimeError("Unrecognized portable NapCat ownership marker")
    if marker.get("initialized") is True:
        return True
    webui_path = _owned_path(root, BUNDLE_NAME, "config", "webui.json")
    onebot_path = _owned_path(root, BUNDLE_NAME, "config", "onebot11.json")
    env_path = _owned_path(root, ".env")
    webui = json.loads(webui_path.read_text(encoding="utf-8"))
    onebot = json.loads(onebot_path.read_text(encoding="utf-8"))
    env_text = env_path.read_text(encoding="utf-8-sig")
    servers = onebot.get("network", {}).get("websocketServers", [])
    if webui.get("host") != "127.0.0.1" or len(servers) != 1 or servers[0].get("host") != "127.0.0.1":
        raise RuntimeError("First-run defaults changed; inspect local NapCat config before initializing")
    server = servers[0]
    choose = select_port or available_port
    webui["port"] = choose(int(webui["port"]))
    server["port"] = choose(int(server["port"]), exclude={webui["port"]})
    webui["token"] = webui.get("token") or secrets.token_urlsafe(24)
    server["token"] = server.get("token") or secrets.token_urlsafe(24)
    # Store fresh per-user credentials only at first use, never in the release ZIP.
    values = {"QQ_WEBUI_CONFIG_PATH": "NapCat.Shell/config/webui.json",
              "QQ_WEBUI_URL": f"http://127.0.0.1:{webui['port']}/webui/",
              "QQ_WEBUI_TOKEN": "", "QQ_WS_URL": f"ws://127.0.0.1:{server['port']}",
              "QQ_WS_TOKEN": server["token"]}
    updated = []
    for line in env_text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        updated.append(f"{key}={values.pop(key)}" if key in values else line)
    updated.extend(f"{key}={value}" for key, value in values.items())
    _write_json(webui_path, webui)
    _write_json(onebot_path, onebot)
    pending_env = _owned_path(root, ".env.new")
    pending_env.write_text("\n".join(updated) + "\n", encoding="utf-8")
    pending_env.replace(env_path)
    marker["initialized"] = True
    _write_json(marker_path, marker)
    return True


def find_qq_executable(*, environ=None, registry=None) -> Path | None:
    """Read installation metadata, not QQ profiles; never start/install QQ here."""
    environ = os.environ if environ is None else environ
    explicit = environ.get("CHATBOT_QQ_EXE", "")
    if explicit:
        candidate = Path(explicit.strip('"'))
        if candidate.name.casefold() == "qq.exe" and candidate.is_file():
            return candidate.resolve()
    if registry is None:
        try:
            import winreg as registry
        except ImportError:
            return None
    for hive in (registry.HKEY_CURRENT_USER, registry.HKEY_LOCAL_MACHINE):
        for view in (registry.KEY_WOW64_32KEY, registry.KEY_WOW64_64KEY):
            try:
                with registry.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\QQ",
                                      0, registry.KEY_READ | view) as key:
                    for field in ("InstallLocation", "UninstallString"):
                        try:
                            value = str(registry.QueryValueEx(key, field)[0]).strip()
                        except OSError:
                            continue
                        if field == "UninstallString":
                            match = re.match(r'"([^\"]+)"|(.+?\.exe)(?:\s|$)', value, re.I)
                            if not match:
                                continue
                            value = (match[1] or match[2])
                            candidate = Path(value).parent / "QQ.exe"
                        else:
                            candidate = Path(value.strip('"')) / "QQ.exe"
                        if candidate.is_file():
                            return candidate.resolve()
            except OSError:
                continue
    for name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        if environ.get(name):
            candidate = Path(environ[name]) / "Tencent" / "QQNT" / "QQ.exe"
            if candidate.is_file():
                return candidate.resolve()
    return None
