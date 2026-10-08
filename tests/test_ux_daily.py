"""U08/U11/U12/U13/U15: offline daily-use callbacks and config validation."""
import asyncio
from types import SimpleNamespace

import pytest

from app.config import ConfigurationError
from app.ui.settings_store import save_validated_env, read_env_values
from app.ui.settings_dialog import SettingsDialog, _SAVED_SECRET
from app.ui.repository import DesktopRepository
from app.ui.desktop import DesktopApp
from app.ui.chat_state import ReadingPosition, near_bottom, has_new_tail
from app.core.identity import conversation_key
from app.core.identity_resolver import ConversationIdentityResolver
from app.adapters.websocket_client import OneBotWebSocketEvents, OneBotAuthenticationError
from tests.ux_support import event, system


class TextWidget:
    def __init__(self, text=''):
        self.text = text
    def get(self, *args): return self.text
    def delete(self, *args): self.text = ''
    def insert(self, position, text): self.text = text
    def winfo_exists(self): return True


def test_drafts_separate_accounts_contacts_and_send_target(tmp_path):
    s = system(tmp_path / 'db.sqlite')
    repo = DesktopRepository(s.db)
    repo.initialize()
    app = object.__new__(DesktopApp)
    app.repository = repo
    app._input = TextWidget('账号A联系人1草稿')
    a = SimpleNamespace(account_id='10001', conversation_id='qq:private:20002')
    b = SimpleNamespace(account_id='10001', conversation_id='qq:private:20003')
    app._current = a
    app._save_draft()
    app._current = b
    app._load_draft()
    assert app._input.text == ''
    app._input.text = '账号A联系人2草稿'
    app._save_draft()
    app._current = SimpleNamespace(account_id='10002', conversation_id=a.conversation_id)
    app._load_draft()
    assert app._input.text == ''
    app._current = a
    app._load_draft()
    sent = []
    def send(route, text, *, account_id):
        sent.append((route, text, account_id))
        app._current = b  # switching during a callback cannot change captured target
    app.runtime = SimpleNamespace(send_message=send)
    app._send()
    assert sent == [(a.conversation_id, '账号A联系人1草稿', a.account_id)]
    assert repo.load_draft(a.account_id, a.conversation_id) == ''
    assert repo.load_draft(b.account_id, b.conversation_id) == '账号A联系人2草稿'
    # A failed outbound event does not restore old text over a newer draft.
    app._input.text = '用户随后输入的新草稿'
    app._save_draft()
    assert repo.load_draft(b.account_id, b.conversation_id) == '用户随后输入的新草稿'


@pytest.mark.parametrize('updates,field', [
    ({'MODEL_PROVIDER': 'openai_compatible', 'MODEL_BASE_URL': 'not-a-url', 'MODEL_API_KEY': 'fake', 'MODEL_NAME': 'fake'}, 'MODEL_BASE_URL'),
    ({'MODEL_TEMPERATURE': 'NaN'}, 'MODEL_TEMPERATURE'),
    ({'MODEL_MAX_TOKENS': '0'}, 'MODEL_MAX_TOKENS'),
    ({'QQ_PROACTIVE_MIN_SECONDS': '9999', 'QQ_PROACTIVE_MAX_SECONDS': '1'}, 'QQ_PROACTIVE_MIN_SECONDS'),
    ({'QQ_TRAINING_GROUP_IDS': '90001', 'QQ_REPLY_GROUP_IDS': '90001'}, 'QQ_TRAINING_GROUP_IDS'),
    ({'QQ_WS_TOKEN': 'fake\nQQ_DRY_RUN=false'}, 'QQ_WS_TOKEN'),
])
def test_invalid_merged_settings_cannot_replace_valid_config(tmp_path, updates, field):
    target = tmp_path / '.env'
    save_validated_env(target, {'MODEL_PROVIDER': 'mock', 'QQ_DRY_RUN': 'true'})
    before = target.read_bytes()
    with pytest.raises(ConfigurationError, match=field):
        save_validated_env(target, updates)
    assert target.read_bytes() == before


def test_dialog_save_preserves_all_masked_tokens(tmp_path, monkeypatch):
    target = tmp_path / '.env'
    save_validated_env(target, {'MODEL_API_KEY': 'MODEL_FAKE_SECRET', 'QQ_WEBUI_TOKEN': 'WEBUI_FAKE_SECRET', 'QQ_WS_TOKEN': 'WS_FAKE_SECRET'})
    dialog = object.__new__(SettingsDialog)
    dialog._env_path = target
    dialog._values = read_env_values(target)
    dialog._entries = {key: TextWidget(_SAVED_SECRET) for key in ('MODEL_API_KEY', 'QQ_WEBUI_TOKEN', 'QQ_WS_TOKEN')}
    dialog._booleans = {}
    dialog._on_saved = None
    dialog.destroy = lambda: None
    monkeypatch.setattr('app.ui.settings_dialog.messagebox.showinfo', lambda *args, **kw: None)
    dialog._save()
    assert read_env_values(target) == dialog._values


def test_scroll_policy_preserves_anchor_history_and_new_tail_hint():
    assert near_bottom((0.8, 0.99)) and not near_bottom((0.2, 0.5))
    assert has_new_tail((10, 11), (10, 11, 12))
    assert not has_new_tail((10, 11), (1, 2, 10, 11))  # prepend history is not new chat
    class Canvas:
        def __init__(self): self.moved = []
        def update_idletasks(self): pass
        def bbox(self, *args): return (0, 0, 100, 2000)
        def yview_moveto(self, fraction): self.moved.append(fraction)
    app = object.__new__(DesktopApp)
    key = ('10001', 'qq:private:20002')
    app._rendered_key = key
    app._view_revision = 2
    app._chat_canvas = Canvas()
    app._message_rows = {11: SimpleNamespace(winfo_y=lambda: 800)}
    app._sync_scroll_region = lambda: None
    app._restore_reading_position(key, 2, ReadingPosition(False, 11, 20, 0.3))
    assert app._chat_canvas.moved == [0.41]
    app._restore_reading_position(key, 1, ReadingPosition())
    app._restore_reading_position(('other', 'route'), 2, ReadingPosition())
    assert app._chat_canvas.moved == [0.41]  # late callbacks do not scroll
    app._restore_reading_position(key, 2, ReadingPosition())
    assert app._chat_canvas.moved[-1] == 1


def test_identity_ui_cli_use_same_key_names_ambiguous_and_empty_not_missing(tmp_path):
    s = system(tmp_path / 'db.sqlite')
    repo = DesktopRepository(s.db)
    repo.initialize()
    for peer in ('20002', '20003'):
        repo.upsert_conversation(platform='qq', account_id='10001', conversation_id=f'qq:private:{peer}',
            conversation_type='private', peer_id=peer, display_name='重名', nickname='重名')
    resolver = ConversationIdentityResolver(s.db)
    item = repo.list_conversations(platform='qq', account_id='10001', conversation_type='private')[0]
    key = resolver.for_item(item)
    assert resolver.resolve(key, account_key='qq:10001') == key
    assert resolver.resolve('20002', account_key='qq:10001') == key
    assert s.service.repository.list_memories(key) == []  # known target, genuinely empty
    with pytest.raises(ValueError, match='歧义'):
        resolver.resolve('重名', account_key='qq:10001')
    with pytest.raises(ValueError, match='目标不存在'):
        resolver.resolve('not-there', account_key='qq:10001')


def test_fake_handshake_auth_failure_points_to_onebot_not_webui_token():
    with pytest.raises(OneBotAuthenticationError) as failure:
        OneBotWebSocketEvents._validate_handshake(b'HTTP/1.1 401 Unauthorized\r\n\r\nWS_FAKE_SECRET', 'fake')
    assert 'QQ_WS_TOKEN' in str(failure.value) and 'WS_FAKE_SECRET' not in str(failure.value)


def test_fake_connection_uses_onebot_token_and_closes_on_auth_failure(monkeypatch):
    class Reader:
        async def readuntil(self, _):
            return b'HTTP/1.1 403 Forbidden\r\n\r\n'
    class Writer:
        def __init__(self): self.data = b''; self.closed = False
        def write(self, data): self.data += data
        async def drain(self): pass
        def close(self): self.closed = True
        async def wait_closed(self): pass
    async def run():
        writer = Writer()
        async def connect(*args, **kwargs): return Reader(), writer
        monkeypatch.setattr(asyncio, 'open_connection', connect)
        client = OneBotWebSocketEvents('ws://invalid.test:3001', 'WS_FAKE_SECRET')
        with pytest.raises(OneBotAuthenticationError) as failure:
            await client._connect()
        assert b'Authorization: Bearer WS_FAKE_SECRET' in writer.data
        assert writer.closed and not client.connected
        assert 'QQ_WS_TOKEN' in str(failure.value) and 'WS_FAKE_SECRET' not in str(failure.value)
    asyncio.run(run())


def test_settings_duplicate_key_updates_are_effective(tmp_path):
    target = tmp_path / '.env'
    target.write_text('MODEL_MAX_TOKENS=256\nMODEL_MAX_TOKENS=128\n', encoding='utf-8')
    settings = save_validated_env(target, {'MODEL_MAX_TOKENS': '64'})
    assert settings.model_max_tokens == 64
    assert read_env_values(target)['MODEL_MAX_TOKENS'] == '64'
    assert target.read_text(encoding='utf-8').count('MODEL_MAX_TOKENS=') == 1


def test_send_before_connection_keeps_current_draft(tmp_path):
    from app.ui.runtime import DesktopRuntime
    from app.config import Settings
    s = system(tmp_path / 'db.sqlite')
    repo = DesktopRepository(s.db)
    repo.initialize()
    app = object.__new__(DesktopApp)
    app.repository = repo
    app._input = TextWidget('不要丢失的正文')
    app._current = SimpleNamespace(account_id='10001', conversation_id='qq:private:20002')
    app.runtime = DesktopRuntime(Settings())  # no thread, no real connection
    app._send()
    assert app._input.text == '不要丢失的正文'
    assert repo.load_draft('10001', 'qq:private:20002') == '不要丢失的正文'


def test_stale_gui_account_cannot_send_or_takeover_under_new_account(tmp_path):
    from app.ui.runtime import DesktopRuntime
    from app.config import Settings
    async def run():
        s = system(tmp_path / 'db.sqlite')
        runtime = DesktopRuntime(Settings())
        runtime._loop = asyncio.get_running_loop()
        runtime._core = SimpleNamespace(database=s.db, merger=s.merger)
        runtime._manual_sender = s.sender
        runtime._account_id = '10002'
        route = 'qq:private:20002'
        assert runtime.send_message(route, '旧账号草稿', account_id='10001') is False
        runtime.set_human_takeover(route, True, account_id='10001')
        await asyncio.sleep(0)
        assert not s.merger.is_human_takeover(conversation_key('qq', '10002', route))
        assert s.sender.calls == []
        with s.db.connect() as conn:
            assert conn.execute('SELECT COUNT(*) FROM outbound_deliveries').fetchone()[0] == 0
    asyncio.run(run())


def test_delivery_projection_counts_terminal_once_and_recognizes_self_echo(tmp_path):
    from app.core.platform import OutboundMessage
    from app.adapters.onebot import parse_onebot_event
    s = system(tmp_path / 'db.sqlite')
    repo = DesktopRepository(s.db)
    repo.initialize()
    repo.upsert_conversation(platform='qq', account_id='10001', conversation_id='qq:private:20002',
                             conversation_type='private', peer_id='20002', display_name='合成联系人')
    service = s.adapter._delivery
    mid = service.prepare(OutboundMessage('qq', '10001', 'qq:private:20002', '合成回应'))
    repo.record_delivery(service.get(mid), increment_unread=True)
    service._state(mid, 'sent', platform_id='fake-receipt')
    repo.record_delivery(service.get(mid), increment_unread=True)
    repo.record_delivery(service.get(mid), increment_unread=True)
    assert repo.list_conversations(platform='qq', account_id='10001', conversation_type='private')[0].unread_count == 1
    echo = event('fake-receipt', '合成回应', sender='10001')
    echo['post_type'] = 'message_sent'
    echo['target_id'] = '20002'
    assert repo.has_delivery_receipt(parse_onebot_event(echo).message)
