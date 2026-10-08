"""Stable local-management identity lookup; names are never guessed."""
from app.core.identity import conversation_key, parse_conversation_key


class ConversationIdentityResolver:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def for_item(item) -> str:
        return conversation_key(item.platform, item.account_id, item.conversation_id)

    def resolve(self, value: str, *, account_key: str | None = None, allow_new_console: bool = False) -> str:
        value = value.strip()
        known: dict[str, set[str]] = {}
        with self.database.connect() as conn:
            for row in conn.execute('SELECT DISTINCT session_id FROM messages UNION SELECT contact_id FROM contact_profiles'):
                key = str(row[0])
                parsed = parse_conversation_key(key)
                if parsed:
                    platform, account, route = parsed
                    if account_key and account_key != f'{platform}:{account}':
                        continue
                    known.setdefault(key, set()).update((key, route, route.rpartition(':')[2]))
                elif not key.startswith('qq:') and not account_key:
                    known.setdefault(key, set()).add(key)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'desktop_conversations' in tables:
                for row in conn.execute('SELECT * FROM desktop_conversations'):
                    if account_key and account_key != f"{row['platform']}:{row['account_id']}":
                        continue
                    key = conversation_key(row['platform'], row['account_id'], row['conversation_id'])
                    known.setdefault(key, set()).update(filter(None, (key, row['conversation_id'], row['peer_id'], row['display_name'], row['nickname'])))
            for row in conn.execute('SELECT DISTINCT platform,account_id,conversation_id FROM inbound_platform_messages'):
                if account_key and account_key != f"{row['platform']}:{row['account_id']}":
                    continue
                key = conversation_key(row['platform'], row['account_id'], row['conversation_id'])
                known.setdefault(key, set()).update((key, row['conversation_id'], row['conversation_id'].rpartition(':')[2]))
        if value in known:
            return value
        matches = [key for key, aliases in known.items() if value in aliases]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError('联系人标识有歧义；请从会话页复制完整 scope 标识，不按昵称猜测')
        if allow_new_console and not account_key and not value.startswith(('scope:', 'qq:')) and not value.isdecimal():
            return value
        raise ValueError('目标不存在（不是“没有记忆”）；请复制会话页的完整管理标识')
