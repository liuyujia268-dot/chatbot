"""Real Tk widgets, withdrawn test-only window; no app startup or NapCat."""
import queue
import threading
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import pytest

from app.config import Settings
from app.core.identity import conversation_key
from app.ui.desktop import DesktopApp
from app.ui.repository import DesktopRepository
from app.ui.runtime import RuntimeControls, UiEvent
from app.ui.settings_dialog import SettingsDialog, _SAVED_SECRET
from app.ui.settings_store import save_validated_env
from tests.ux_support import system


@pytest.fixture(scope='module')
def tk_root():
    # One interpreter for this module avoids Windows Tcl reinitialization races.
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def build_view(root, repository, tmp_path):
    app = object.__new__(DesktopApp)  # never execute __init__ (which reads live .env)
    app.root = root
    app.repository = repository
    app.settings = Settings(qq_dry_run=False)
    controls = RuntimeControls(dry_run=True, auto_reply=False, proactive=False, followup=False)
    app.runtime = SimpleNamespace(controls=controls, runtime_id='fake-runtime', running=True,
                                  events=queue.Queue(), load_history=lambda _, **kwargs: None)
    app._env_path = tmp_path / '.env'
    app._configuration_error = None
    app._main = ttk.Frame(root)
    app._main.pack(fill='both', expand=True)
    app._account_id = '10001'
    app._login_name = '合成账号'
    app._current = None
    app._items = {}
    app._photo_refs = {}
    app._avatar_attempted = set()
    app._default_avatars = {kind: tk.PhotoImage(master=root, width=1, height=1) for kind in ('private', 'group')}
    app._takeover_conversations = set()
    app._history_loaded = set()
    app._history_inflight = set()
    app._suppress_selection = False
    app._webui_starting = False
    app._webui_opened = True
    app._webui_cancel = threading.Event()
    app._webui_launcher_lock = threading.Lock()
    app._napcat_launcher_process = None
    app._qq_exiting = app._qq_paused = False
    app._qq_switch_after_exit = False
    app._switch_from_account = None
    app._media_results = queue.Queue()
    app._connection_notice = ''
    app._scroll_states = {}
    app._rendered_key = None
    app._rendered_ids = ()
    app._message_rows = {}
    app._view_revision = 0
    app._force_follow_key = None
    app._ws_state = '已连接'
    app._model_state = '待验证'
    app._request_avatar = lambda *args: None
    return app


def test_tk_home_reentry_settings_keep_actual_controls_and_separate_drafts(tmp_path, tk_root):
    root = tk_root
    try:
        s = system(tmp_path / 'db.sqlite')
        repo = DesktopRepository(s.db)
        repo.initialize()
        for peer in ('20002', '20003'):
            repo.upsert_conversation(platform='qq', account_id='10001', conversation_id=f'qq:private:{peer}',
                conversation_type='private', peer_id=peer, display_name=f'合成{peer}')
        app = build_view(root, repo, tmp_path)
        app._show_qq()
        root.update_idletasks()
        assert app._dry_var.get() and not app._auto_var.get()
        app._select_tree_conversation('qq:private:20002')
        app._input.insert('1.0', 'A草稿')
        app._select_tree_conversation('qq:private:20003')
        assert app._input.get('1.0', 'end-1c') == ''
        app._input.insert('1.0', 'B草稿')
        app._select_tree_conversation('qq:private:20002')
        assert app._input.get('1.0', 'end-1c') == 'A草稿'
        app._show_home()
        # Background events while on home must not render destroyed chat widgets.
        app._refresh_messages()
        app._show_qq()
        root.update_idletasks()
        assert app._dry_var.get() and not app._auto_var.get()
        app._load_settings = lambda: (Settings(qq_dry_run=False), None)
        app._settings_saved()
        app._show_qq()
        assert app._dry_var.get()  # saving defaults did not toggle the live sender
        app._takeover_conversations = {('10001', 'qq:private:20002')}
        app._select_tree_conversation('qq:private:20002')
        assert app._takeover_var.get()
        app._handle_event(UiEvent('login', {'account_id': '10001', 'nickname': '合成账号', 'takeovers': ()}, 'fake-runtime'))
        assert not app._takeover_var.get()
        app._handle_event(UiEvent('login', {'account_id': '10001', 'nickname': '合成账号',
                                         'takeovers': ('qq:private:20002',)}, 'fake-runtime'))
        assert app._takeover_var.get()
        assert 'WebUI' in app._status.cget('text') and 'OneBot' in app._status.cget('text') and '模型' in app._status.cget('text')
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_tk_settings_onebot_token_is_masked_and_kept_when_saved(tmp_path, monkeypatch, tk_root):
    root = tk_root
    try:
        path = tmp_path / '.env'
        save_validated_env(path, {'QQ_WS_TOKEN': 'WS_ONLY_FAKE', 'QQ_WEBUI_TOKEN': 'WEBUI_ONLY_FAKE'})
        dialog = SettingsDialog(root, env_path=path, settings=Settings())
        dialog.withdraw()
        assert dialog._entries['QQ_WS_TOKEN'].get() == _SAVED_SECRET
        assert dialog._entries['QQ_WEBUI_TOKEN'].get() == _SAVED_SECRET
        monkeypatch.setattr('app.ui.settings_dialog.messagebox.showinfo', lambda *args, **kwargs: None)
        dialog._save()
        from app.ui.settings_store import read_env_values
        assert read_env_values(path)['QQ_WS_TOKEN'] == 'WS_ONLY_FAKE'
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_tk_model_proxy_mode_persists_and_proxy_credentials_stay_masked(tmp_path, monkeypatch, tk_root):
    root = tk_root
    try:
        path = tmp_path / '.env'
        save_validated_env(path, {'MODEL_PROXY_MODE': 'custom',
                                 'MODEL_PROXY_URL': 'socks5://FAKE_USER:FAKE_PASSWORD@localhost:1080'})
        settings = Settings.from_env(path, environ={})
        dialog = SettingsDialog(root, env_path=path, settings=settings)
        dialog.withdraw()
        assert dialog._entries['MODEL_PROXY_MODE'].get() == 'custom'
        assert dialog._entries['MODEL_PROXY_URL'].get() == _SAVED_SECRET
        dialog._entries['MODEL_PROXY_MODE'].set('direct')
        monkeypatch.setattr('app.ui.settings_dialog.messagebox.showinfo', lambda *args, **kwargs: None)
        dialog._save()
        saved = Settings.from_env(path, environ={})
        assert saved.model_proxy_mode == 'direct'
        assert saved.model_proxy_url == settings.model_proxy_url
        reopened = SettingsDialog(root, env_path=path, settings=saved)
        reopened.withdraw()
        assert reopened._entries['MODEL_PROXY_MODE'].get() == 'direct'
        assert reopened._entries['MODEL_PROXY_URL'].get() == _SAVED_SECRET
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_tk_network_configuration_error_is_visible_and_local_only(tmp_path, tk_root):
    root = tk_root
    try:
        s = system(tmp_path / 'db.sqlite')
        repo = DesktopRepository(s.db)
        repo.initialize()
        route = 'qq:private:20002'
        repo.upsert_conversation(platform='qq', account_id='10001', conversation_id=route,
                                conversation_type='private', peer_id='20002', display_name='合成联系人')
        app = build_view(root, repo, tmp_path)
        app._show_qq()
        app._select_tree_conversation(route)
        hint = '模型代理/网络配置失败；检查模型网络模式，保存后重连'
        app._handle_event(UiEvent('model_error', {
            'account_id': '10001', 'conversation_id': route,
            'code': 'ModelNetworkConfigurationError', 'message': hint,
        }, 'fake-runtime'))
        root.update_idletasks()
        assert hint in app._status.cget('text')
        messages = repo.list_messages(platform='qq', account_id='10001', conversation_id=route)
        assert len(messages) == 1 and messages[0].status == 'failed'
        assert messages[0].source_type == 'model_error' and messages[0].text == hint
        assert s.sender.calls == []
        assert s.db.list_messages(conversation_key('qq', '10001', route)) == []
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_tk_reconnect_clears_account_caches_but_saves_draft_and_controls(tmp_path, monkeypatch, tk_root):
    from app.ui.runtime import DesktopRuntime
    root = tk_root
    try:
        s = system(tmp_path / 'db.sqlite')
        repo = DesktopRepository(s.db)
        repo.initialize()
        repo.upsert_conversation(platform='qq', account_id='10001', conversation_id='qq:private:20002',
                                conversation_type='private', peer_id='20002', display_name='合成联系人')
        app = build_view(root, repo, tmp_path)
        app.runtime.stop = lambda: None  # Fake only; no running user program
        app._show_qq()
        app._select_tree_conversation('qq:private:20002')
        app._input.insert('1.0', '重连前的草稿')
        app._photo_refs['avatar:qq:private:20002'] = app._default_avatars['private']
        monkeypatch.setattr(DesktopRuntime, 'start', lambda _: None)  # never initialize env/core/network
        app._replace_qq_runtime()
        assert app.runtime.controls.dry_run
        assert app._current is None and app._input.get('1.0', 'end-1c') == ''
        assert 'avatar:qq:private:20002' not in app._photo_refs
        assert repo.load_draft('10001', 'qq:private:20002') == '重连前的草稿'
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_tk_automatic_directory_errors_are_nonmodal_and_login_recovers(tmp_path, monkeypatch, tk_root):
    root = tk_root
    try:
        s = system(tmp_path / 'db.sqlite')
        repo = DesktopRepository(s.db)
        repo.initialize()
        app = build_view(root, repo, tmp_path)
        app._show_qq()
        dialogs = []
        monkeypatch.setattr('app.ui.desktop.messagebox.showerror', lambda *args, **kwargs: dialogs.append(args))
        for _ in range(20):
            app._handle_event(UiEvent('directory_error', '连接暂时中断，正在重试', 'fake-runtime'))
        assert dialogs == [] and '正在重试' in app._status.cget('text')
        # Rebuilding the page preserves the actual connection notice.
        app._show_home()
        app._show_qq()
        assert '正在重试' in app._status.cget('text')
        app._handle_event(UiEvent('login', {'account_id': '10001', 'nickname': '合成账号'}, 'fake-runtime'))
        app._handle_event(UiEvent('directory', {'account_id': '10001', 'friends': [], 'groups': []}, 'fake-runtime'))
        assert app._connection_notice == '' and '正在重试' not in app._status.cget('text')
        assert dialogs == []
        app.runtime._connection_epoch = 2
        app._handle_event(UiEvent('login', {'account_id': '99999', 'nickname': '过期账号',
                                         'connection_epoch': 1}, 'fake-runtime'))
        assert app._account_id == '10001'
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_tk_qq_exit_confirmed_saves_draft_gates_sends_and_allows_reentry(tmp_path, monkeypatch, tk_root):
    from app.ui.runtime import DesktopRuntime
    from app.ui.qq_shutdown import QQShutdownResult
    root = tk_root
    try:
        s = system(tmp_path / 'db.sqlite')
        repo = DesktopRepository(s.db)
        repo.initialize()
        repo.upsert_conversation(platform='qq', account_id='10001', conversation_id='qq:private:20002',
                                conversation_type='private', peer_id='20002', display_name='合成联系人')
        app = build_view(root, repo, tmp_path)
        app._show_qq()
        app._select_tree_conversation('qq:private:20002')
        app._input.insert('1.0', '退出前未发送的草稿')
        calls, workers = [], []
        runtime = app.runtime

        def request_stop():
            calls.append('request-stop')
            runtime.running = False

        runtime.request_stop = request_stop
        runtime.stop = lambda **kwargs: calls.append('stop')

        class Worker:
            def __init__(self, *, target, **kwargs):
                self.target = target

            def start(self):
                workers.append(self.target)  # do not launch a real worker

        def cleanup(selected, **kwargs):
            assert selected is runtime and calls == ['request-stop']
            assert repo.load_draft('10001', 'qq:private:20002') == '退出前未发送的草稿'
            calls.append('fake-cleanup')
            return QQShutdownResult(4, forced=True)

        monkeypatch.setattr('app.ui.desktop.threading.Thread', Worker)
        monkeypatch.setattr('app.ui.desktop.shutdown_qq_session', cleanup)
        monkeypatch.setattr('app.ui.desktop.messagebox.askyesno', lambda *args, **kwargs: True)
        monkeypatch.setattr('app.ui.desktop.messagebox.showwarning', lambda *args, **kwargs: pytest.fail('unexpected warning'))
        app._exit_qq()
        assert calls == ['request-stop'] and app._qq_paused and app._qq_exiting
        assert str(app._send_button.cget('state')) == 'disabled'
        assert app._webui_cancel.is_set()
        # An already queued login or WebUI callback must not revive the session.
        app._handle_event(UiEvent('login', {'account_id': '99999', 'nickname': '迟到的回调'}, 'fake-runtime'))
        browser_opens = []
        app._apply_webui_result(app._webui_cancel, lambda: browser_opens.append(True))
        assert app._account_id == '10001' and browser_opens == []
        workers.pop()()
        app._input.insert('end', '（等待期间补充）')
        callback, *args = app._media_results.get_nowait()
        callback(*args)
        assert calls == ['request-stop', 'fake-cleanup']
        assert app._account_id == '' and app._current is None and app._items == {}
        assert app._qq_paused and not app._qq_exiting
        assert str(app._qq_exit_button.cget('state')) == 'normal'
        assert str(app._retry_button.cget('state')) == 'normal'
        assert '全部 QQ 后台已清理' in app._status.cget('text')
        assert app._input.get('1.0', 'end-1c') == ''
        assert repo.load_draft('10001', 'qq:private:20002') == '退出前未发送的草稿（等待期间补充）'
        app._handle_event(UiEvent('status', ('connected', None), 'fake-runtime'))
        assert '不会自动重连' in app._status.cget('text')
        # Explicit reconnect is permitted and uses a new runtime; still no network.
        starts = []
        monkeypatch.setattr(DesktopRuntime, 'start', lambda selected: starts.append(selected))
        app._begin_webui_launch = lambda: calls.append('fake-webui')
        app._retry_qq_login()
        assert not app._qq_paused and app.runtime is not runtime and starts == [app.runtime]
        assert app.runtime.controls.dry_run and calls[-1] == 'fake-webui'
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_tk_qq_exit_can_cancel_and_is_available_while_logged_out(tmp_path, monkeypatch, tk_root):
    root = tk_root
    try:
        s = system(tmp_path / 'db.sqlite')
        repo = DesktopRepository(s.db)
        repo.initialize()
        app = build_view(root, repo, tmp_path)
        app._account_id = ''
        app._show_qq()
        calls = []
        app.runtime.request_stop = lambda: calls.append('stop')
        monkeypatch.setattr('app.ui.desktop.messagebox.askyesno', lambda *args, **kwargs: False)
        assert str(app._qq_exit_button.cget('state')) == 'normal'
        app._exit_qq()
        assert calls == [] and not app._qq_paused and not app._webui_cancel.is_set()
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_tk_qq_cleanup_failure_is_reported_once_without_auto_reconnect(tmp_path, monkeypatch, tk_root):
    root = tk_root
    try:
        s = system(tmp_path / 'db.sqlite')
        repo = DesktopRepository(s.db)
        repo.initialize()
        app = build_view(root, repo, tmp_path)
        app._show_qq()
        app._qq_paused = app._qq_exiting = True
        warnings = []
        monkeypatch.setattr('app.ui.desktop.messagebox.showwarning', lambda *args, **kwargs: warnings.append(args))
        app._qq_exit_finished(None, '任务尚未完全退出；未执行 QQ 清理')
        assert len(warnings) == 1 and app._qq_paused and not app._qq_exiting
        assert '未执行 QQ 清理' in app._status.cget('text')
        for _ in range(10):
            app._handle_event(UiEvent('directory_error', '连接失败', 'fake-runtime'))
        assert len(warnings) == 1 and '未执行 QQ 清理' in app._status.cget('text')
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_tk_stale_webui_callbacks_are_ignored(tmp_path, tk_root):
    root = tk_root
    try:
        app = build_view(root, SimpleNamespace(), tmp_path)
        old_cancel = threading.Event()
        calls = []
        app._apply_webui_result(old_cancel, lambda: calls.append('stale'))
        assert calls == []
        app._apply_webui_result(app._webui_cancel, lambda: calls.append('current'))
        assert calls == ['current']
        app._webui_cancel.set()
        app._apply_webui_result(app._webui_cancel, lambda: calls.append('cancelled'))
        assert calls == ['current']
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_tk_switch_account_relaunches_login_and_loads_only_new_account(tmp_path, monkeypatch, tk_root):
    from app.ui.runtime import DesktopRuntime
    from app.ui.qq_shutdown import QQShutdownResult
    root = tk_root
    try:
        s = system(tmp_path / 'db.sqlite')
        repo = DesktopRepository(s.db)
        repo.initialize()
        route = 'qq:private:20002'
        for account, marker in (('10001', 'ONLY_A_UI_MARKER'), ('10002', 'ONLY_B_UI_MARKER')):
            repo.upsert_conversation(platform='qq', account_id=account, conversation_id=route,
                                    conversation_type='private', peer_id='20002', display_name=f'联系人{account}')
            repo.record_message(platform='qq', account_id=account, conversation_id=route, event_key='seed',
                sender_id='20002', sender_name='合成联系人', role='user', source_type='other_user', text=marker,
                parts_json='[]', created_at='2026-10-07T12:00:00+00:00', increment_unread=False)
        repo.save_draft('10002', route, 'B自己的草稿')
        app = build_view(root, repo, tmp_path)
        app._show_qq()
        app._select_tree_conversation(route)
        app._input.insert('1.0', 'A自己的草稿')
        old = app.runtime
        calls, workers = [], []
        old.request_stop = lambda: calls.append('gate-old')
        old.stop = lambda: calls.append('stop-old')

        class Worker:
            def __init__(self, *, target, **kwargs):
                self.target = target

            def start(self):
                workers.append(self.target)

        def cleanup(runtime, **kwargs):
            assert runtime is old and calls == ['gate-old']
            calls.append('clean-all-qq')
            return QQShutdownResult(4)

        monkeypatch.setattr('app.ui.desktop.threading.Thread', Worker)
        monkeypatch.setattr('app.ui.desktop.shutdown_qq_session', cleanup)
        monkeypatch.setattr('app.ui.desktop.messagebox.askyesno', lambda *args, **kwargs: True)
        monkeypatch.setattr(DesktopRuntime, 'start', lambda _: calls.append('new-runtime'))
        app._begin_webui_launch = lambda **kwargs: calls.append(('webui', kwargs))
        app._switch_account_button.invoke()
        assert app._qq_exiting and app._qq_switch_after_exit
        assert str(app._send_button.cget('state')) == 'disabled'
        assert str(app._switch_account_button.cget('state')) == 'disabled'
        workers.pop()()
        callback, *args = app._media_results.get_nowait()
        callback(*args)
        assert calls == ['gate-old', 'clean-all-qq', 'stop-old', 'new-runtime', ('webui', {'force_start': True})]
        assert app.runtime is not old and not app._qq_paused and app._account_id == ''
        assert app.runtime._switch_away_from_account == '10001'
        assert not app._qq_switch_after_exit
        assert app.runtime.controls.dry_run and not app.runtime.controls.auto_reply
        assert repo.load_draft('10001', route) == 'A自己的草稿'
        app.runtime._account_id = '10002'
        app.runtime._connection_state = 'connected'
        app.runtime._login_confirmed_epoch = app.runtime._connection_epoch
        app.runtime.load_history = lambda *args, **kwargs: None
        rid = app.runtime.runtime_id
        app._handle_event(UiEvent('login', {'account_id': '10002', 'nickname': '合成账号B'}, rid))
        app._handle_event(UiEvent('directory', {'account_id': '10002', 'friends': [], 'groups': []}, rid))
        app._select_tree_conversation(route)
        assert app._current.account_id == '10002'
        assert app._input.get('1.0', 'end-1c') == 'B自己的草稿'
        assert '10002' in app._status.cget('text') and '10001' not in app._status.cget('text')

        def label_texts(parent):
            text = []
            for child in parent.winfo_children():
                if isinstance(child, (tk.Label, ttk.Label)):
                    text.append(str(child.cget('text')))
                text.extend(label_texts(child))
            return text

        rendered = '\n'.join(label_texts(app._message_frame))
        assert 'ONLY_B_UI_MARKER' in rendered and 'ONLY_A_UI_MARKER' not in rendered
        app._handle_event(UiEvent('login', {'account_id': '10001', 'nickname': '过期账号A'}, rid))
        app._handle_event(UiEvent('login', {'account_id': '10001', 'nickname': '旧Runtime'}, 'fake-runtime'))
        assert app._account_id == '10002' and app._current.account_id == '10002'
        app._set_avatar(route, b'invalid-old-avatar', '10001')
        assert f'avatar:{route}' not in app._photo_refs
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_tk_switch_failure_never_starts_new_login(tmp_path, monkeypatch, tk_root):
    from app.ui.qq_shutdown import QQShutdownResult
    root = tk_root
    try:
        s = system(tmp_path / 'db.sqlite')
        repo = DesktopRepository(s.db)
        repo.initialize()
        app = build_view(root, repo, tmp_path)
        app._show_qq()
        app._qq_switch_after_exit = app._qq_exiting = app._qq_paused = True
        notices = []
        monkeypatch.setattr('app.ui.desktop.messagebox.showwarning', lambda *args, **kwargs: notices.append(args))
        app._replace_qq_runtime = lambda: pytest.fail('must not connect while old QQ is still running')
        app._begin_webui_launch = lambda **kwargs: pytest.fail('must not start a competing QQ login')
        app._qq_exit_finished(QQShutdownResult(2, (102,), True), None)
        assert app._qq_paused and not app._qq_exiting and not app._qq_switch_after_exit
        assert len(notices) == 1 and '仍有 1 个 QQ 进程' in app._status.cget('text')
        assert str(app._switch_account_button.cget('state')) == 'normal'
    finally:
        for child in root.winfo_children():
            child.destroy()


@pytest.mark.parametrize('remote', [False, True])
def test_tk_switch_cancel_or_remote_server_does_not_touch_qq(tmp_path, monkeypatch, tk_root, remote):
    root = tk_root
    try:
        s = system(tmp_path / 'db.sqlite')
        repo = DesktopRepository(s.db)
        repo.initialize()
        app = build_view(root, repo, tmp_path)
        app._show_qq()
        if remote:
            app.settings = Settings(qq_webui_url='http://remote.invalid:6099/webui')
        calls = []
        app.runtime.request_stop = lambda: pytest.fail('cancel/remote must not retire runtime')
        monkeypatch.setattr('app.ui.desktop.messagebox.askyesno', lambda *args, **kwargs: calls.append('confirmation') or False)
        monkeypatch.setattr('app.ui.desktop.messagebox.showwarning', lambda *args, **kwargs: calls.append('remote-warning'))
        app._switch_account()
        assert calls == (['remote-warning'] if remote else ['confirmation'])
        assert not app._qq_exiting and not app._qq_paused and not app._qq_switch_after_exit
    finally:
        for child in root.winfo_children():
            child.destroy()


@pytest.mark.parametrize('force_start', [False, True])
def test_tk_switch_reuses_existing_webui_without_competing_launcher(tmp_path, monkeypatch, tk_root, force_start):
    from app.ui.napcat_webui import WebUiLaunch
    root = tk_root
    try:
        app = build_view(root, SimpleNamespace(), tmp_path)
        calls = []
        launch = WebUiLaunch('http://localhost:6099/webui', True, 'fake', tmp_path / 'config' / 'webui.json')

        class Worker:
            def __init__(self, *, target, **kwargs):
                self.target = target

            def start(self):
                self.target()  # all calls inside are injected Fakes

        monkeypatch.setattr('app.ui.desktop.threading.Thread', Worker)
        monkeypatch.setattr('app.ui.desktop.resolve_webui_launch', lambda *args, **kwargs: launch)
        monkeypatch.setattr('app.ui.desktop.webui_is_ready', lambda *args: True)
        monkeypatch.setattr('app.ui.desktop.find_napcat_launcher', lambda *args: tmp_path / 'launcher-user.bat')
        monkeypatch.setattr('app.ui.desktop.start_napcat_launcher', lambda *args: calls.append('start') or 'fake-owned-process')
        monkeypatch.setattr('app.ui.desktop.restart_existing_napcat_for_switch', lambda *args: calls.append('restart-login-service'))
        monkeypatch.setattr('app.ui.desktop.wait_for_webui', lambda *args, **kwargs: calls.append('wait') or True)
        app._open_resolved_webui = lambda _: calls.append('browser')
        app._begin_webui_launch(force_start=force_start)
        callback, *args = app._media_results.get_nowait()
        callback(*args)
        assert calls == (['restart-login-service', 'wait', 'browser'] if force_start else ['browser'])
        assert app._napcat_launcher_process is None
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_tk_selecting_contact_during_switch_does_not_reenable_send(tmp_path, tk_root):
    root = tk_root
    try:
        s = system(tmp_path / 'db.sqlite')
        repo = DesktopRepository(s.db)
        repo.initialize()
        route = 'qq:private:20002'
        repo.upsert_conversation(platform='qq', account_id='10001', conversation_id=route,
                                conversation_type='private', peer_id='20002', display_name='合成联系人')
        app = build_view(root, repo, tmp_path)
        app._show_qq()
        app._qq_paused = app._qq_exiting = True
        app._select_tree_conversation(route)
        assert str(app._send_button.cget('state')) == 'disabled'
        assert str(app._takeover_widget.cget('state')) == 'disabled'
        app.runtime.send_message = lambda *args, **kwargs: pytest.fail('switching must block manual submits')
        app._input.insert('1.0', '换号过程中修改的草稿')
        app._send()
        assert repo.load_draft('10001', route) == '换号过程中修改的草稿'
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_tk_webui_config_search_is_deferred_and_known_path_is_reused(tmp_path, monkeypatch, tk_root):
    from app.ui.napcat_webui import WebUiLaunch
    app = build_view(tk_root, SimpleNamespace(), tmp_path)
    workers, calls = [], []
    path = tmp_path / 'config' / 'webui.json'

    class Worker:
        def __init__(self, *, target, **kwargs): self.target = target
        def start(self): workers.append(self.target)

    def resolve(*args, **kwargs):
        calls.append(kwargs.get('explicit_config_path'))
        return WebUiLaunch('http://localhost:6099/webui', True, 'fake', path)

    monkeypatch.setattr('app.ui.desktop.threading.Thread', Worker)
    monkeypatch.setattr('app.ui.desktop.resolve_webui_launch', resolve)
    monkeypatch.setattr('app.ui.desktop.webui_is_ready', lambda *args: True)
    monkeypatch.setattr('app.ui.desktop.webbrowser.open', lambda *args, **kwargs: True)
    app._begin_webui_launch()
    assert app._webui_starting and calls == []  # Tk returned before any disk scan
    workers.pop()()
    callback, *args = app._media_results.get_nowait()
    callback(*args)
    assert calls == [None] and app._webui_config_cache == path
    app._begin_webui_launch()
    assert calls == [None]
    workers.pop()()
    callback, *args = app._media_results.get_nowait()
    callback(*args)
    assert calls == [None, path]


def test_tk_webui_start_reloads_rotated_token_before_opening(tmp_path, monkeypatch, tk_root):
    from app.ui.napcat_webui import WebUiLaunch
    app = build_view(tk_root, SimpleNamespace(), tmp_path)
    workers, calls = [], []
    path = tmp_path / 'config' / 'webui.json'
    launches = iter([WebUiLaunch('http://localhost:6099/webui?token=old', True, 'config', path),
                     WebUiLaunch('http://localhost:6099/webui?token=new', True, 'config', path)])

    class Worker:
        def __init__(self, *, target, **kwargs): self.target = target
        def start(self): workers.append(self.target)

    monkeypatch.setattr('app.ui.desktop.threading.Thread', Worker)
    monkeypatch.setattr('app.ui.desktop.resolve_webui_launch', lambda *args, **kwargs: next(launches))
    monkeypatch.setattr('app.ui.desktop.webui_is_ready', lambda *args: False)
    monkeypatch.setattr('app.ui.desktop.find_napcat_launcher', lambda *args: tmp_path / 'launcher-user.bat')
    monkeypatch.setattr('app.ui.desktop.start_napcat_launcher', lambda *args: calls.append('start') or object())
    monkeypatch.setattr('app.ui.desktop.wait_for_webui', lambda *args, **kwargs: True)
    app._open_resolved_webui = lambda launch: calls.append(launch.url)
    app._begin_webui_launch()
    workers.pop()()
    callback, *args = app._media_results.get_nowait()
    callback(*args)
    assert calls == ['start', 'http://localhost:6099/webui?token=new']


def test_tk_existing_napcat_restart_failure_never_starts_duplicate(tmp_path, monkeypatch, tk_root):
    from app.ui.napcat_webui import WebUiLaunch
    from app.ui.napcat_connection import NapCatSwitchError
    app = build_view(tk_root, SimpleNamespace(), tmp_path)
    workers, notices = [], []

    class Worker:
        def __init__(self, *, target, **kwargs): self.target = target
        def start(self): workers.append(self.target)

    def fail(*args): raise NapCatSwitchError('不支持重启：请人工关闭旧 NapCat 后重新连接')

    monkeypatch.setattr('app.ui.desktop.threading.Thread', Worker)
    monkeypatch.setattr('app.ui.desktop.resolve_webui_launch', lambda *args, **kwargs: WebUiLaunch('http://localhost:6099/webui', True, 'fake'))
    monkeypatch.setattr('app.ui.desktop.webui_is_ready', lambda *args: True)
    monkeypatch.setattr('app.ui.desktop.restart_existing_napcat_for_switch', fail)
    monkeypatch.setattr('app.ui.desktop.start_napcat_launcher', lambda *args: pytest.fail('no competing process'))
    app._webui_launch_failed = lambda message: notices.append(message)
    app._begin_webui_launch(force_start=True)
    workers.pop()()
    callback, *args = app._media_results.get_nowait()
    callback(*args)
    assert notices == ['不支持重启：请人工关闭旧 NapCat 后重新连接']
