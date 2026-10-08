"""Build an allowlisted Windows distribution without copying live user data."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from portable_napcat import copy_clean_napcat


ROOT = Path(__file__).resolve().parents[1]
RELEASE_DOCUMENTS = (
    "README.md", "CHANGELOG.md", "CONTRIBUTING.md", "qq新项目.md", "release/README.md",
    "docs/USER_GUIDE.md", "docs/ux_fixes_2026-10-07.md", "docs/COMMIT_MESSAGE.txt",
)
SAFE_ENV = """# Portable first-run defaults. Fill in your own model and NapCat settings.
MODEL_PROVIDER=mock
MODEL_BASE_URL=
MODEL_API_KEY=
MODEL_NAME=
MODEL_PROXY_MODE=system
MODEL_PROXY_URL=
MEMORY_FAILURE_RETRY_SECONDS=300
QQ_ENABLED=false
QQ_WEBUI_URL=http://127.0.0.1:6099/webui/
QQ_WEBUI_TOKEN=
QQ_WEBUI_CONFIG_PATH=NapCat.Shell/config/webui.json
QQ_WS_URL=ws://127.0.0.1:3001
QQ_WS_TOKEN=
QQ_ADMIN_CONTACT_IDS=
QQ_PRIMARY_CONTACT_ID=
QQ_PRIMARY_CONTACT_NAME=
QQ_TRAINING_GROUP_IDS=
QQ_REPLY_GROUP_IDS=
QQ_AUTO_REPLY_ENABLED=false
QQ_DRY_RUN=true
QQ_PROACTIVE_ENABLED=false
QQ_FOLLOWUP_ENABLED=false
MEMORY_DEBUG=false
"""


def find_existing_builder(tools_root: Path) -> str:
    """Stage only installed build tools; never expose another Python's apps."""
    spec = importlib.util.find_spec("PyInstaller")
    source = Path(spec.origin).parent.parent if spec and spec.origin else None
    if source is None:
        executable = shutil.which("pyinstaller")
        if executable:
            candidate = Path(executable).resolve().parent.parent / "Lib" / "site-packages"
            if (candidate / "PyInstaller" / "__init__.py").is_file():
                source = candidate
    if source is None:
        raise RuntimeError("PyInstaller is not installed. No dependency was automatically installed.")
    tools_root.mkdir()
    for name in ("PyInstaller", "_pyinstaller_hooks_contrib", "altgraph", "packaging", "win32ctypes", "ordlookup", "pefile.py", "peutils.py"):
        item = source / name
        if item.is_dir():
            shutil.copytree(item, tools_root / name, ignore=shutil.ignore_patterns("__pycache__"))
        elif item.is_file():
            shutil.copyfile(item, tools_root / name)
    allowed = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "packaging", "pywin32-ctypes", "pefile"}
    for metadata in source.glob("*.dist-info"):
        info = metadata / "METADATA"
        if not info.is_file():
            continue
        package_name = next((line[6:].strip().lower().replace("_", "-")
                             for line in info.read_text(encoding="utf-8").splitlines()
                             if line.startswith("Name: ")), "")
        if package_name in allowed:
            shutil.copytree(metadata, tools_root / metadata.name)
    sys.path.insert(0, str(tools_root))
    import PyInstaller

    return PyInstaller.__version__


def copy_release_documentation(distribution: Path) -> None:
    """Copy explicit public documents only, never a whole workspace folder."""
    for relative in RELEASE_DOCUMENTS:
        target = distribution / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)


def write_update_archive(distribution: Path, archive: Path, manifest: dict) -> None:
    """Replace application runtime only; never ship mutable user settings/data."""
    files = []
    for path in sorted(distribution.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(distribution)
        parts = tuple(part.casefold() for part in relative.parts)
        if (parts[0].startswith(".env") or parts[0] in {
                "data", "napcat.shell", "manifest.json", "readme-first.txt",
            } or parts[:2] == ("app", "prompts")):
            continue
        files.append(path)
    instructions = (ROOT / "packaging" / "README-update.txt").read_bytes()
    update = {
        "name": "Chatbot application update", "built_at": manifest["built_at"],
        "target": "Windows x64", "requires_existing_distribution": True,
        "preserved_paths": [".env", ".env.example", "data", "app/prompts", "NapCat.Shell"],
        "socks_support_included": True,
        "files": [{"path": path.relative_to(distribution).as_posix(),
                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in files],
    }
    update["files"].append({"path": "README-update.txt", "sha256": hashlib.sha256(instructions).hexdigest()})
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
        for path in files:
            handle.write(path, "Chatbot/" + path.relative_to(distribution).as_posix())
        handle.writestr("Chatbot/README-update.txt", instructions)
        handle.writestr("Chatbot/update-manifest.json", json.dumps(update, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--napcat-source", type=Path,
                        default=Path.home() / "Documents" / "NapCat.Shell")
    parser.add_argument("--without-napcat", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release",
                        help="Release output directory (default: project release directory).")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info < (3, 12) or platform.architecture()[0] != "64bit":
        raise RuntimeError("Build with Windows x64 and Python 3.12 or later.")
    if not args.without_napcat and not args.napcat_source.is_dir():
        raise RuntimeError("Installed NapCat.Shell not found; provide --napcat-source. Nothing was downloaded.")
    if importlib.util.find_spec("socksio") is None:
        raise RuntimeError("SOCKS support is missing from the build environment; cannot create a complete release.")
    tag = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    release_name = f"Chatbot{'-With-NapCat' if not args.without_napcat else ''}-Windows-x64-{tag}"
    release_root = args.output_dir.resolve() / release_name
    work_root = ROOT / "build" / "portable" / tag
    if release_root.exists() or work_root.exists():
        raise RuntimeError("This build directory already exists; retry with a new timestamp.")
    release_root.mkdir(parents=True)
    work_root.mkdir(parents=True)
    builder_version = find_existing_builder(work_root / "tools")

    from PyInstaller.__main__ import run

    run([
        "--onedir", "--windowed", "--noupx", "--name", "Chatbot",
        "--contents-directory", ".",  # Keep existing PROJECT_ROOT/resource paths valid.
        "--distpath", str(release_root),
        "--workpath", str(work_root / "work"),
        "--specpath", str(work_root),
        "--paths", str(ROOT),
        "--add-data", f"{ROOT / 'app' / 'prompts'};app/prompts",
        "--add-data", f"{ROOT / 'app' / 'assets'};app/assets",
        "--hidden-import", "socksio",
        "--copy-metadata", "socksio",
        "--exclude-module", "pytest", "--exclude-module", "pip",
        str(ROOT / "packaging" / "desktop_entry.py"),
    ])
    distribution = release_root / "Chatbot"
    defaults = SAFE_ENV if not args.without_napcat else SAFE_ENV.replace("QQ_WEBUI_CONFIG_PATH=NapCat.Shell/config/webui.json", "QQ_WEBUI_CONFIG_PATH=")
    (distribution / ".env").write_text(defaults, encoding="utf-8")
    (distribution / ".env.example").write_text(defaults, encoding="utf-8")
    shutil.copyfile(ROOT / "packaging" / "README-portable.txt", distribution / "README-first.txt")
    shutil.copyfile(ROOT / "packaging" / "run-console.cmd", distribution / "run-console.cmd")
    copy_release_documentation(distribution)
    napcat = None
    if not args.without_napcat:
        napcat = copy_clean_napcat(args.napcat_source, distribution / "NapCat.Shell", ROOT / "packaging")

    forbidden_suffixes = {".db", ".db-wal", ".db-shm", ".log", ".jsonl", ".lnk"}
    files = sorted(path for path in distribution.rglob("*") if path.is_file())
    for path in files:
        if path.suffix.lower() in forbidden_suffixes:
            raise RuntimeError(f"Unexpected private/runtime file in distribution: {path.name}")
        if ".cp311-" in path.name.lower():
            raise RuntimeError("Python 3.11 binary unexpectedly included in a Python 3.12 build")
    manifest = {
        "name": "Chatbot", "target": "Windows x64", "built_at": tag,
        "python": sys.version.split()[0], "pyinstaller": builder_version,
        "default_provider": "mock", "default_qq_dry_run": True,
        "napcat_included": napcat is not None,
        "napcat_version": napcat["version"] if napcat else None,
        "model_proxy_modes": ["system", "direct", "custom"],
        "socks_support_included": True,
        "files": [{"path": str(path.relative_to(distribution)).replace("\\", "/"),
                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in files],
    }
    (distribution / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = release_root / f"{release_name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(distribution.rglob("*")):
            if path.is_file():
                handle.write(path, path.relative_to(release_root))
    update_archive = release_root / f"Chatbot-Update-Windows-x64-{tag}.zip"
    write_update_archive(distribution, update_archive, manifest)
    summary = {"exe": str(distribution / "Chatbot.exe"), "zip": str(archive),
               "zip_bytes": archive.stat().st_size,
               "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
               "update_zip": str(update_archive), "update_zip_bytes": update_archive.stat().st_size,
               "update_sha256": hashlib.sha256(update_archive.read_bytes()).hexdigest()}
    subprocess.run([sys.executable, "-B", "-X", "utf8",
                    str(ROOT / "packaging" / "verify_portable.py"), str(archive), str(update_archive)],
                   cwd=ROOT, check=True)
    (release_root / "SHA256SUMS.txt").write_text(
        f"{summary['sha256']}  {archive.name}\n"
        f"{summary['update_sha256']}  {update_archive.name}\n", encoding="utf-8")
    (release_root / "build-result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
