"""Manual replay runner for qualitative Chinese chat evaluation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import ConfigurationError, PROJECT_ROOT, Settings
from app.core.prompt import load_system_prompt
from app.core.reply_engine import ReplyEngine
from app.core.session import Session
from app.memory.context import MemoryContextProvider
from app.memory.types import MemoryContext, LongTermMemory
from app.models.base import ModelError
from app.models.factory import build_model


DEFAULT_CASES_PATH = PROJECT_ROOT / "tests" / "chat_cases.json"
DEFAULT_RESULTS_PATH = PROJECT_ROOT / "data" / "chat_replay_results.json"
VALID_ROLES = {"user", "assistant"}


class ReplayContextProvider:
    """Inject confirmed-memory fixtures without changing the real database."""

    system_messages = staticmethod(MemoryContextProvider.system_messages)

    def __init__(self, context: MemoryContext) -> None:
        self._context = context

    def load(self, conversation_id: str, current_text: str) -> MemoryContext:
        if conversation_id != self._context.conversation_id:
            raise ValueError("replay memory context belongs to another contact")
        return self._context


def _fixture_context(conversation: dict[str, Any]) -> ReplayContextProvider | None:
    fixtures = conversation.get("long_term_memories")
    if not fixtures:
        return None
    session_id = conversation["session_id"]
    memories = tuple(
        LongTermMemory(
            id=index,
            conversation_id=session_id,
            memory_type=item["memory_type"],
            memory_key=item["memory_key"],
            content=item["content"],
            source_start_message_id=item.get("source_start_message_id", index),
            source_end_message_id=item.get("source_end_message_id", index),
            confidence=float(item.get("confidence", 1.0)),
            status="active",
            supersedes_memory_id=None,
            created_at="replay-fixture",
            updated_at="replay-fixture",
            last_used_at=None,
            use_count=0,
        )
        for index, item in enumerate(fixtures, start=1)
    )
    return ReplayContextProvider(
        MemoryContext(
            conversation_id=session_id,
            summary=None,
            memories=memories,
            contact_profile=None,
            account_profile=None,
        )
    )


def load_cases(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"无法读取聊天测试集：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"聊天测试集不是有效 JSON：第 {exc.lineno} 行"
        ) from exc
    if not isinstance(payload, list) or not payload:
        raise ConfigurationError("聊天测试集必须是非空 JSON 数组")

    seen_ids: set[str] = set()
    for index, case in enumerate(payload, start=1):
        if not isinstance(case, dict):
            raise ConfigurationError(f"第 {index} 个聊天场景必须是对象")
        case_id = case.get("id")
        conversations = case.get("conversations")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ConfigurationError(f"第 {index} 个聊天场景缺少 id")
        if case_id in seen_ids:
            raise ConfigurationError(f"聊天场景 id 重复：{case_id}")
        seen_ids.add(case_id)
        if not isinstance(conversations, list) or not conversations:
            raise ConfigurationError(f"聊天场景 {case_id} 缺少 conversations")
        for conversation in conversations:
            _validate_conversation(case_id, conversation)
    return payload


def _validate_conversation(case_id: str, conversation: Any) -> None:
    if not isinstance(conversation, dict):
        raise ConfigurationError(f"聊天场景 {case_id} 的会话必须是对象")
    session_id = conversation.get("session_id")
    messages = conversation.get("messages")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ConfigurationError(f"聊天场景 {case_id} 的 session_id 无效")
    if not isinstance(messages, list) or not messages:
        raise ConfigurationError(f"聊天场景 {case_id} 的 messages 不能为空")
    for message in messages:
        if (
            not isinstance(message, dict)
            or message.get("role") not in VALID_ROLES
            or not isinstance(message.get("content"), str)
            or not message["content"].strip()
        ):
            raise ConfigurationError(f"聊天场景 {case_id} 包含无效消息")
    fixtures = conversation.get("long_term_memories", [])
    if not isinstance(fixtures, list):
        raise ConfigurationError(f"聊天场景 {case_id} 的长期记忆必须是数组")
    for item in fixtures:
        if (
            not isinstance(item, dict)
            or item.get("memory_type") not in {
                "fact", "preference", "relationship", "ongoing_event"
            }
            or not isinstance(item.get("memory_key"), str)
            or not isinstance(item.get("content"), str)
        ):
            raise ConfigurationError(f"聊天场景 {case_id} 包含无效长期记忆")


def select_cases(
    cases: list[dict[str, Any]],
    case_ids: list[str] | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    if limit is not None and limit < 1:
        raise ConfigurationError("--limit 必须大于或等于 1")
    selected = cases
    if case_ids:
        wanted = set(case_ids)
        selected = [case for case in cases if case["id"] in wanted]
        missing = wanted - {case["id"] for case in selected}
        if missing:
            raise ConfigurationError("找不到聊天场景：" + ", ".join(sorted(missing)))
    return selected[:limit] if limit is not None else selected


def _read_score(label: str) -> int | None:
    while True:
        raw = input(f"{label}（1～5，回车跳过）：").strip()
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if 1 <= value <= 5:
            return value
        print("请输入 1～5，或直接回车跳过。")


def _read_flag(label: str) -> bool | None:
    while True:
        raw = input(f"{label}（y/n，回车跳过）：").strip().lower()
        if not raw:
            return None
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("请输入 y、n，或直接回车跳过。")


def collect_ratings() -> dict[str, Any]:
    ratings = {
        "naturalness": _read_score("自然程度"),
        "context_fit": _read_score("是否接对上下文"),
        "verbosity_excess": _read_score("话太多程度（1=没有，5=非常明显）"),
        "ai_service_tone": _read_score(
            "AI/客服腔程度（1=没有，5=非常明显）"
        ),
        "pointless_question": _read_flag("是否有无意义追问"),
        "unwanted_advice": _read_flag("是否乱给建议"),
        "fabrication": _read_flag("是否编造信息"),
    }
    ratings["comment"] = input("备注（回车跳过）：").strip() or None
    return ratings


def _print_case(case: dict[str, Any]) -> None:
    print()
    print(f"=== {case['id']} | {case.get('title', '')} ===")
    print(f"类别：{case.get('category', '')}")
    expected = case.get("expected_behavior", [])
    avoid = case.get("avoid", [])
    if expected:
        print("期望：" + "；".join(expected))
    if avoid:
        print("不希望：" + "；".join(avoid))


def _print_messages(session_id: str, messages: list[dict[str, str]]) -> None:
    print(f"会话：{session_id}")
    for message in messages:
        speaker = "对方" if message["role"] == "user" else "助手"
        print(f"  {speaker}：{message['content']}")


def _load_existing_results(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"无法读取已有回放结果：{path}") from exc
    if not isinstance(payload, list):
        raise ConfigurationError("已有回放结果必须是 JSON 数组")
    return payload


def save_run(path: Path, run: dict[str, Any]) -> None:
    results = _load_existing_results(path)
    results.append(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def replay(args: argparse.Namespace) -> Path:
    settings = Settings.from_env()
    if settings.model_provider == "mock" and not args.allow_mock:
        raise ConfigurationError(
            "聊天回放默认要求真实模型；当前 MODEL_PROVIDER=mock。"
            "如只想检查工具，请显式传入 --allow-mock。"
        )
    prompt = load_system_prompt(settings.system_prompt_path)
    model = build_model(settings)
    cases = select_cases(
        load_cases(args.cases),
        args.case_ids,
        args.limit,
    )
    run: dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now(UTC).isoformat(),
        "model": {
            "provider": settings.model_provider,
            "name": settings.model_name or "mock",
            "temperature": settings.model_temperature,
            "max_tokens": settings.model_max_tokens,
            "context_max_messages": settings.context_max_messages,
        },
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "cases_path": str(args.cases.resolve()),
        "cases": [],
    }

    for case in cases:
        _print_case(case)
        case_result = {
            "id": case["id"],
            "title": case.get("title"),
            "outputs": [],
        }
        for conversation in case["conversations"]:
            session_id = conversation["session_id"]
            messages = conversation["messages"]
            _print_messages(session_id, messages)
            session = Session(
                session_id=session_id,
                max_messages=settings.context_max_messages,
            )
            for message in messages:
                session.add_message(message["role"], message["content"])
            engine = ReplyEngine(
                model=model,
                system_prompt=prompt,
                context_max_messages=settings.context_max_messages,
                max_output_chars=settings.model_max_output_chars,
                context_provider=_fixture_context(conversation),
            )
            output: dict[str, Any] = {
                "session_id": session_id,
                "reply": None,
                "error": None,
                "ratings": None,
            }
            try:
                reply = await engine.generate(session)
            except ModelError as exc:
                output["error"] = str(exc)
                print(f"  模型错误：{exc}")
            else:
                output["reply"] = reply
                print(f"  模型回复：{reply}")
                if not args.no_score:
                    output["ratings"] = collect_ratings()
            case_result["outputs"].append(output)
        run["cases"].append(case_result)

    save_run(args.results, run)
    return args.results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="手动回放中文聊天测试集，并保存可比较的人工评分结果。"
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_PATH)
    parser.add_argument(
        "--case",
        dest="case_ids",
        action="append",
        help="只运行指定场景 id，可重复传入。",
    )
    parser.add_argument("--limit", type=int, help="只运行前 N 个已选场景。")
    parser.add_argument(
        "--no-score",
        action="store_true",
        help="非交互运行，不询问人工评分。",
    )
    parser.add_argument(
        "--allow-mock",
        action="store_true",
        help="允许使用 MockModel，仅用于检查回放工具。",
    )
    return parser


def _configure_console_encoding() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def main() -> None:
    _configure_console_encoding()
    args = build_parser().parse_args()
    try:
        result_path = asyncio.run(replay(args))
    except (ConfigurationError, ValueError) as exc:
        raise SystemExit(f"[回放配置错误] {exc}") from exc
    print()
    print(f"回放结果已保存：{result_path}")


if __name__ == "__main__":
    main()
