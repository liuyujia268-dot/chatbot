from types import SimpleNamespace

from app.ui.desktop import DesktopApp


class NavigationTree:
    def __init__(
        self,
        *,
        hit_column: str = "#0",
        hit_row: str = "qq:private:1",
    ) -> None:
        self.hit_column = hit_column
        self.hit_row = hit_row
        self.rows = {
            "category:private": ("qq:private:1", "qq:private:2"),
            "category:group": ("qq:group:1", "qq:group:2"),
        }

    def identify_row(self, _y: int) -> str:
        return self.hit_row

    def identify_column(self, _x: int) -> str:
        return self.hit_column

    def parent(self, item: str) -> str:
        for parent, children in self.rows.items():
            if item in children:
                return parent
        return ""

    def get_children(self, parent: str):
        return self.rows.get(parent, ())


class SelectionTree:
    def winfo_exists(self) -> bool:
        return True

    def selection(self):
        return ("qq:private:2",)


def test_left_side_click_only_selects_and_never_arms_reorder() -> None:
    selected: list[str] = []
    app = SimpleNamespace(
        _drag_item=None,
        _tree=NavigationTree(),
        _select_tree_conversation=selected.append,
    )

    result = DesktopApp._drag_start(app, SimpleNamespace(x=10, y=10))

    assert app._drag_item is None
    assert selected == ["qq:private:1"]
    assert result == "break"


def test_only_right_drag_handle_arms_reorder() -> None:
    app = SimpleNamespace(
        _drag_item=None,
        _tree=NavigationTree(hit_column="#2"),
    )

    result = DesktopApp._drag_start(app, SimpleNamespace(x=300, y=10))

    assert app._drag_item == "qq:private:1"
    assert result == "break"


def test_clicking_drag_handle_without_moving_does_not_reorder() -> None:
    app = SimpleNamespace(
        _drag_item="qq:private:1",
        _tree=NavigationTree(hit_column="#2"),
    )

    result = DesktopApp._drag_end(app, SimpleNamespace(x=300, y=10))

    assert app._drag_item is None
    assert result == "break"


def test_selection_during_refresh_is_processed_after_refresh() -> None:
    selected: list[bool] = []
    app = SimpleNamespace(
        _suppress_selection=True,
        _tree=SelectionTree(),
        _current=SimpleNamespace(conversation_id="qq:private:1"),
        _select_conversation=lambda: selected.append(True),
        _update_navigation_controls=lambda: None,
    )

    DesktopApp._enable_selection_events(app)

    assert app._suppress_selection is False
    assert selected == [True]


def test_conversation_navigation_stays_in_current_group() -> None:
    selected: list[str] = []
    app = SimpleNamespace(
        _tree=NavigationTree(),
        _current=SimpleNamespace(conversation_id="qq:private:1"),
        _select_tree_conversation=selected.append,
    )

    DesktopApp._navigate_conversation(app, 1)

    assert selected == ["qq:private:2"]


def test_group_navigation_selects_first_conversation_in_target_group() -> None:
    selected: list[str] = []
    app = SimpleNamespace(
        _tree=NavigationTree(),
        _current=SimpleNamespace(conversation_id="qq:private:2"),
        _select_tree_conversation=selected.append,
    )

    DesktopApp._navigate_group(app, 1)

    assert selected == ["qq:group:1"]
