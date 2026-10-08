"""Run account assignment only against an explicitly selected database copy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.storage.database import Database
from app.storage.migration import migrate_legacy_accounts


def main() -> None:
    parser = argparse.ArgumentParser(description="保守迁移旧数据；先备份数据库，未确定归属的数据保留且不注入模型")
    parser.add_argument("--database-copy", required=True, type=Path)
    parser.add_argument('--source-links', type=Path, help='人工核验的JSON消息ID→原始事件ID映射；禁止按文本或昵称猜测')
    args = parser.parse_args()
    path = args.database_copy.resolve()
    if not path.is_file():
        parser.error("指定的数据库副本不存在")
    if path == (Path(__file__).resolve().parents[2] / "data" / "chatbot.db").resolve():
        parser.error("请先备份并指定数据库副本，禁止直接操作默认正式库")
    database = Database(path)
    database.initialize()
    links = None
    if args.source_links:
        links = {int(key): int(value) for key, value in json.loads(args.source_links.read_text(encoding='utf-8')).items()}
    counts = migrate_legacy_accounts(database, message_event_links=links)
    print(f"已归属：{counts['assigned']}；待归属：{counts['unassigned']}；原始记录未删除")


if __name__ == "__main__":
    main()
