"""Verify the delivered ZIP after extraction under a different working path."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from build_portable import SAFE_ENV


def main() -> int:
    archive = Path(sys.argv[1]).resolve()
    report_path = archive.parent / "verification.json"
    report = {"archive": archive.name, "status": "running", "checks": []}
    with zipfile.ZipFile(archive) as handle:
        assert handle.testzip() is None, "ZIP CRC check failed"
        entries = handle.namelist()
        assert all(name.startswith("Chatbot/") and ".." not in Path(name).parts for name in entries)
        forbidden = {".db", ".db-wal", ".db-shm", ".log", ".jsonl", ".lnk"}
        assert not any(Path(name).suffix.lower() in forbidden for name in entries)
        assert not any(".cp311-" in name.lower() for name in entries)
        assert not any(part in {".git", ".venv", "tests", "artifacts"} for name in entries for part in Path(name).parts)
        manifest = json.loads(handle.read("Chatbot/manifest.json"))
        assert manifest["socks_support_included"] is True
        assert manifest["model_proxy_modes"] == ["system", "direct", "custom"]
        assert any("socksio-" in name.lower() and name.endswith("/METADATA") for name in entries)
        report["checks"].append("bundled_socks_dependency_and_proxy_modes")
        defaults = SAFE_ENV if manifest["napcat_included"] else SAFE_ENV.replace("QQ_WEBUI_CONFIG_PATH=NapCat.Shell/config/webui.json", "QQ_WEBUI_CONFIG_PATH=")
        assert handle.read("Chatbot/.env").decode("utf-8").replace("\r\n", "\n") == defaults
        assert handle.read("Chatbot/.env.example").decode("utf-8").replace("\r\n", "\n") == defaults
        for name in entries:
            if Path(name).suffix.lower() in {".txt", ".json", ".cmd"}:
                text = handle.read(name).decode("utf-8", errors="replace")
                assert not re.search(r"sk-[A-Za-z0-9_-]{24,}", text), "Key-like text found in distribution"
        if manifest["napcat_included"]:
            prefix = "Chatbot/NapCat.Shell/"
            config_names = {name.removeprefix(prefix + "config/") for name in entries if name.startswith(prefix + "config/")}
            assert config_names == {"webui.json", "onebot11.json", "napcat.json"}
            assert not any(name.startswith(prefix + folder + "/") for name in entries for folder in ("cache", "logs"))
            webui = json.loads(handle.read(prefix + "config/webui.json"))
            onebot = json.loads(handle.read(prefix + "config/onebot11.json"))
            server = onebot["network"]["websocketServers"][0]
            assert webui["token"] == server["token"] == webui["autoLoginAccount"] == webui["totpSecret"] == ""
            assert webui["host"] == server["host"] == "127.0.0.1"
            assert server["enable"] is True and server["reportSelfMessage"] is True
            marker = json.loads(handle.read(prefix + "chatbot-bundle.json"))
            assert marker["initialized"] is False
            assert marker["version"] == manifest["napcat_version"]
            assert marker["runtime_sha256"] == hashlib.sha256(handle.read(prefix + "napcat.mjs")).hexdigest()
            loader = handle.read(prefix + "loadNapCat.js").decode("utf-8")
            assert "pathToFileURL" in loader and "C:" not in loader and "D:" not in loader
            assert handle.read(prefix + "LICENSE.txt").strip()
            report["checks"].append("napcat_clean_templates_and_relocatable_loader")
        # Role/personality files must exactly match the source, not fallback prompts.
        source_prompts = Path(__file__).resolve().parents[1] / "app" / "prompts"
        for path in source_prompts.glob("*.txt"):
            assert handle.read("Chatbot/app/prompts/" + path.name) == path.read_bytes()
        report["checks"].append("all_prompts_unchanged_including_personality")
        for item in manifest["files"]:
            assert hashlib.sha256(handle.read("Chatbot/" + item["path"])).hexdigest() == item["sha256"]
        report["checks"].extend(["zip_crc", "clean_first_run_configuration", "no_live_database_logs_or_shortcuts", "no_cp311_binaries", "file_hashes"])

        # The EXE must work without PATH/PYTHONPATH pointing at developer Python.
        with tempfile.TemporaryDirectory(prefix="验证 中文 空格 ", dir=archive.parent) as temporary:
            working = Path(temporary)
            handle.extractall(working)
            distribution = working / "Chatbot"
            output = working / "self-test.json"
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith(("PYTHON", "MODEL_", "QQ_", "CHATBOT_", "MEMORY_", "SYSTEM_PROMPT", "PROACTIVE_PROMPT", "FOLLOWUP_PROMPT", "REPLY_REPAIR_PROMPT"))}
            windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
            environment["PATH"] = str(windows / "System32") + os.pathsep + str(windows)
            completed = subprocess.run([str(distribution / "Chatbot.exe"), "--self-test", str(output)],
                                       cwd=working, env=environment, timeout=45)
            assert completed.returncode == 0, "EXE self-test failed"
            result = json.loads(output.read_text(encoding="utf-8"))
            assert result["status"] == "passed", result
            assert not (distribution / "data").exists(), "Self-test leaked runtime data into distribution"
            report["exe_self_test"] = result
            report["checks"].append("extracted_exe_chinese_spaces_no_python_path")
            if len(sys.argv) > 2:
                update_archive = Path(sys.argv[2]).resolve()
                # Simulate an already configured customer installation, never
                # read an actual .env/database/NapCat login from this machine.
                preserved = {
                    ".env": b"MODEL_PROVIDER=mock\nMODEL_API_KEY=OFFLINE_KEEP_KEY\nQQ_DRY_RUN=true\n",
                    ".env.example": b"OFFLINE_KEEP_EXAMPLE\n",
                    "data/keep.db": b"OFFLINE_KEEP_SYNTHETIC_DATABASE",
                    "app/prompts/chat.txt": b"OFFLINE_KEEP_PERSONALITY",
                }
                if manifest["napcat_included"]:
                    preserved["NapCat.Shell/config/webui.json"] = b'{"token":"OFFLINE_KEEP_TOKEN"}'
                for relative, content in preserved.items():
                    path = distribution / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                with zipfile.ZipFile(update_archive) as update:
                    assert update.testzip() is None
                    names = update.namelist()
                    assert all(name.startswith("Chatbot/") and ".." not in Path(name).parts for name in names)
                    assert not any(name.startswith(("Chatbot/.env", "Chatbot/data/", "Chatbot/app/prompts/", "Chatbot/NapCat.Shell/")) for name in names)
                    metadata = json.loads(update.read("Chatbot/update-manifest.json"))
                    assert metadata["requires_existing_distribution"] and metadata["socks_support_included"]
                    assert update.read("Chatbot/Chatbot.exe") == handle.read("Chatbot/Chatbot.exe")
                    for item in metadata["files"]:
                        assert hashlib.sha256(update.read("Chatbot/" + item["path"])).hexdigest() == item["sha256"]
                    update.extractall(working)
                assert all((distribution / name).read_bytes() == value for name, value in preserved.items())
                output.unlink()
                completed = subprocess.run([str(distribution / "Chatbot.exe"), "--self-test", str(output)],
                                           cwd=working, env=environment, timeout=45)
                assert completed.returncode == 0, "Updated EXE self-test failed"
                result = json.loads(output.read_text(encoding="utf-8"))
                assert result["status"] == "passed"
                assert all((distribution / name).read_bytes() == value for name, value in preserved.items())
                report["update_archive"] = update_archive.name
                report["update_exe_self_test"] = result
                report["checks"].extend(["update_preserves_settings_data_prompts_and_napcat", "updated_exe_offline_self_test"])
    report["status"] = "passed"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
