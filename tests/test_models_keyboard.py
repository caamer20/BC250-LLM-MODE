"""Keyboard-only model highlighting and primary-action routing."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.gui.models_page import (  # noqa: E402
    ModelItemView,
    ModelsPage,
)


class FakeTree:
    def __init__(self, rows=(), *, selected=(), focused="") -> None:
        self.rows = tuple(rows)
        self._selected = tuple(selected)
        self._focused = focused
        self.seen: list[str] = []

    def get_children(self, _parent=""):
        return self.rows

    def selection(self):
        return self._selected

    def selection_set(self, key):
        self._selected = (key,)

    def focus(self, key=None):
        if key is None:
            return self._focused
        self._focused = key

    def see(self, key):
        self.seen.append(key)


class FakePrimaryButton:
    def __init__(self, command, *, disabled=False) -> None:
        self.command = command
        self.disabled = disabled
        self.invoke_count = 0

    def invoke(self):
        self.invoke_count += 1
        if not self.disabled:
            return self.command()
        return None


def _item(
    key: str,
    *,
    state: str = "INSTALLED",
    remote: bool = False,
    busy: bool = False,
) -> ModelItemView:
    return ModelItemView(
        key=key,
        display_name=key,
        family="test",
        size_gib=1.0,
        state=state,
        fit_verdict="FITS",
        fit_detail="Fits the activation draft.",
        support_tier="supported",
        description="Test model",
        source_repo="example/model",
        catalog_id=key.removeprefix("catalog::") if remote else None,
        alias=None if remote else key.removeprefix("installed::"),
        quant="Q5_K_M",
        available_quants=("Q5_K_M",),
        tags=(),
        remote=remote,
        busy=busy,
    )


def _page(*items: ModelItemView, selected: str | None = None) -> ModelsPage:
    page = ModelsPage.__new__(ModelsPage)
    page._visible = {item.key: item for item in items}
    page._selected_key = selected
    page.tree = FakeTree(
        page._visible,
        selected=((selected,) if selected is not None else ()),
        focused=selected or "",
    )
    page._render_detail = lambda: None
    return page


def test_models_tree_owns_the_four_keyboard_bindings():
    source = inspect.getsource(ModelsPage._build)
    for sequence, handler in (
        ("<Up>", "_highlight_previous"),
        ("<Down>", "_highlight_next"),
        ("<Return>", "_run_highlighted_action"),
        ("<KP_Enter>", "_run_highlighted_action"),
    ):
        assert f'self.tree.bind("{sequence}", self.{handler})' in source
    assert "bind_all" not in source


def test_model_tree_declares_local_arrow_and_enter_bindings():
    source = Path("bc250_llm_mode/gui/models_page.py").read_text(encoding="utf-8")

    assert 'self.tree.bind("<Up>", self._highlight_previous)' in source
    assert 'self.tree.bind("<Down>", self._highlight_next)' in source
    assert 'self.tree.bind("<Return>", self._run_highlighted_action)' in source
    assert 'self.tree.bind("<KP_Enter>", self._run_highlighted_action)' in source


def test_arrow_keys_move_highlight_and_clamp_at_boundaries():
    items = tuple(_item(f"installed::{name}") for name in ("a", "b", "c"))
    page = _page(*items, selected=items[0].key)
    rendered: list[str | None] = []
    page._render_detail = lambda: rendered.append(page._selected_key)

    assert page._highlight_previous() == "break"
    assert page.tree.selection() == (items[0].key,)
    assert page._highlight_next() == "break"
    assert page.tree.selection() == (items[1].key,)
    assert page._highlight_next() == "break"
    assert page.tree.selection() == (items[2].key,)
    assert page._highlight_next() == "break"
    assert page.tree.selection() == (items[2].key,)
    assert page._highlight_previous() == "break"

    assert page._selected_key == items[1].key
    assert page.tree.focus() == items[1].key
    assert page.tree.seen == [
        items[0].key,
        items[1].key,
        items[2].key,
        items[2].key,
        items[1].key,
    ]
    assert rendered == page.tree.seen


def test_arrow_keys_are_a_bounded_noop_for_an_empty_view():
    page = _page()
    rendered = []
    page._render_detail = lambda: rendered.append(True)

    assert page._highlight_previous() == "break"
    assert page._highlight_next() == "break"
    assert page._selected_key is None
    assert page.tree.selection() == ()
    assert page.tree.seen == []
    assert rendered == []


def test_enter_invokes_primary_button_once_for_highlighted_installed_model():
    first = _item("installed::first")
    second = _item("installed::second")
    page = _page(first, second, selected=first.key)
    page.tree.selection_set(second.key)
    actions: list[str] = []
    page._run_action = actions.append
    page._primary_action_button = FakePrimaryButton(page._run_primary_action)

    assert page._run_highlighted_action() == "break"

    assert page._selected_key == second.key
    assert page.tree.focus() == second.key
    assert page.tree.seen == [second.key]
    assert page._primary_action_button.invoke_count == 1
    assert actions == ["activate"]


def test_enter_preserves_remote_and_busy_primary_routes():
    remote = _item("catalog::remote", state="AVAILABLE", remote=True)
    busy = _item("installed::busy", busy=True)
    actions: list[str] = []

    for item, expected in ((remote, "install-start"), (busy, "activity")):
        page = _page(item, selected=item.key)
        page._run_action = actions.append
        page._primary_action_button = FakePrimaryButton(page._run_primary_action)

        assert page._run_highlighted_action() == "break"
        assert actions[-1] == expected

    assert actions == ["install-start", "activity"]


def test_enter_is_inert_without_selection_or_when_primary_is_disabled():
    item = _item("installed::inactive")
    actions: list[str] = []
    page = _page(item, selected=None)
    page.tree.focus(item.key)
    page._run_action = actions.append
    page._primary_action_button = FakePrimaryButton(page._run_primary_action)

    assert page._run_highlighted_action() == "break"
    assert page._primary_action_button.invoke_count == 0
    assert actions == []

    page.tree.selection_set(item.key)
    page._primary_action_button.disabled = True
    assert page._run_highlighted_action() == "break"
    assert page._primary_action_button.invoke_count == 1
    assert actions == []
