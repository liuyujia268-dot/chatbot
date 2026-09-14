"""Resolve a local NapCat WebUI URL without exposing its login token."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True, slots=True)
class WebUiLaunch:
    url: str = field(repr=False)
    authenticated: bool
    token_source: str
    config_path: Path | None = None


def should_launch_webui(
    *,
    starting: bool,
    opened: bool,
    account_id: str | None,
) -> bool:
    """Retry an opened WebUI when it never produced a logged-in QQ account."""

    return not starting and (not opened or not account_id)


def resolve_webui_launch(
    base_url: str,
    *,
    explicit_token: str | None = None,
    explicit_config_path: Path | None = None,
    search_roots: Iterable[Path] | None = None,
) -> WebUiLaunch:
    """Add the WebUI token to *base_url* using a safe, documented precedence."""

    url_token = _query_token(base_url)
    token = (explicit_token or "").strip()
    source = "explicit" if token else ""
    config_path = discover_webui_config(
        explicit_config_path=explicit_config_path,
        search_roots=search_roots,
    )

    if not token and url_token:
        token = url_token
        source = "url"

    if not token:
        if config_path is not None:
            token = read_webui_token(config_path) or ""
            if token:
                source = "config"

    return WebUiLaunch(
        url=(
            _with_token(_web_login_url(_normalize_webui_url(base_url)), token)
            if token
            else _normalize_webui_url(base_url)
        ),
        authenticated=bool(token),
        token_source=source or "missing",
        config_path=config_path,
    )


def discover_webui_config(
    *,
    explicit_config_path: Path | None = None,
    search_roots: Iterable[Path] | None = None,
) -> Path | None:
    """Find common NapCat Shell ``config/webui.json`` locations."""

    candidates: list[Path] = []
    if explicit_config_path is not None:
        candidates.append(_as_config_file(explicit_config_path))

    roots = list(search_roots) if search_roots is not None else _default_roots()
    for root in roots:
        candidates.extend(
            (
                root / "NapCat.Shell" / "config" / "webui.json",
                root / "NapCatQQ" / "config" / "webui.json",
                root / "NapCat" / "config" / "webui.json",
                root / "config" / "webui.json",
            )
        )
        if root.exists() and root.is_dir():
            for pattern in (
                "NapCat*/config/webui.json",
                "*/NapCat*/config/webui.json",
            ):
                try:
                    candidates.extend(root.glob(pattern))
                except OSError:
                    continue

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            if candidate.is_file() and read_webui_token(candidate):
                return candidate
        except OSError:
            continue
    return None


def read_webui_token(path: Path) -> str | None:
    """Read only the NapCat WebUI token; callers must never log the result."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    token = str(payload.get("token") or "").strip()
    return token or None


def find_napcat_launcher(config_path: Path | None) -> Path | None:
    """Return NapCat Shell's non-elevated launcher beside a known config."""

    if config_path is None:
        return None
    install_root = config_path.parent.parent
    for name in ("launcher-user.bat", "launcher-win10-user.bat"):
        candidate = install_root / name
        if candidate.is_file():
            return candidate
    return None


def start_napcat_launcher(
    launcher_path: Path,
    *,
    popen=subprocess.Popen,
) -> object:
    """Start NapCat without a visible command window or shell interpolation."""

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return popen(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher_path)],
        cwd=launcher_path.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


def is_local_webui(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").casefold()
    return hostname in {"127.0.0.1", "localhost", "::1"}


def webui_is_ready(
    url: str,
    *,
    timeout_seconds: float = 0.75,
    connector=socket.create_connection,
) -> bool:
    parsed = urlsplit(url)
    if not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection = None
    try:
        connection = connector(
            (parsed.hostname, port),
            timeout=timeout_seconds,
        )
        return True
    except OSError:
        return False
    finally:
        if connection is not None:
            connection.close()


def wait_for_webui(
    url: str,
    *,
    timeout_seconds: float = 45.0,
    interval_seconds: float = 0.5,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if webui_is_ready(url):
            return True
        time.sleep(interval_seconds)
    return webui_is_ready(url)


def _default_roots() -> list[Path]:
    home = Path.home()
    roots = [home / "Documents", home / "Downloads", home / "Desktop"]
    for name in ("APPDATA", "LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(name)
        if value:
            roots.append(Path(value))
    return roots


def _as_config_file(path: Path) -> Path:
    return path / "webui.json" if path.is_dir() else path


def _query_token(url: str) -> str | None:
    parsed = urlsplit(url)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() == "token" and value.strip():
            return value.strip()
    return None


def _normalize_webui_url(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path
    if path.rstrip("/").casefold().endswith("/webui"):
        path = path.rstrip("/")
    return urlunsplit(
        (parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment)
    )


def _web_login_url(url: str) -> str:
    """Use the login route directly so stale browser credentials are replaced."""

    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    if path.casefold().endswith("/webui"):
        path += "/web_login"
    return urlunsplit(
        (parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment)
    )


def _with_token(url: str, token: str) -> str:
    parsed = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() != "token"
    ]
    query.append(("token", token))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )
