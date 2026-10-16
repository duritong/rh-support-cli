"""Offline keyboard-dispatch contracts exercised through a mounted Textual app."""

import pathlib
import sys
import unittest
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from unittest.mock import patch

from textual.binding import Binding
from textual.widgets import DataTable, Input, Static, TextArea

from rh_support_lib.services.state import StateService
from rh_support_lib.tui.app import (
    CaseJumpModal,
    FocusableContainer,
    SupportApp,
    UnsavedChangesModal,
)
from tests import test_tui_actions

# Deliberately independent of BINDINGS: removals and new, untested keys must fail.
APP_BINDINGS = {
    "q": "quit",
    "r": "refresh",
    "c": "add_comment",
    "a": "add_attachment",
    "d": "download_attachment",
    "t": "apply_template",
    "b": "select_bookmark",
    "f": "focus_pane",
    "w": "toggle_follow",
    "m": "mark_read",
    "o": "toggle_comment_order",
    "g": "jump_to_case",
    ":": "jump_to_case",
    "/": "search_cases",
    "s": "search_cases",
    "K": "search_kcs",
    "?": "search_kcs",
    "v": "toggle_layout",
    "l": "toggle_layout",
    "ctrl+up": "focus_top",
    "ctrl+down": "focus_bottom",
    "ctrl+left": "focus_left",
    "ctrl+right": "focus_right",
    "escape": "escape_action",
    "ctrl+x": "exit_commenting",
}
SCROLL_BINDINGS = {
    "up": "scroll_up",
    "down": "scroll_down",
    "pageup": "scroll_page_up",
    "pagedown": "scroll_page_down",
    "home": "scroll_home",
    "end": "scroll_end",
}
PRINTABLE_KEYS = tuple(key for key in APP_BINDINGS if len(key) == 1)


def declared_bindings(widget_class):
    bindings = []
    for entry in widget_class.BINDINGS:
        binding = entry if isinstance(entry, Binding) else Binding(*entry)
        bindings.extend((key.strip(), binding.action) for key in binding.key.split(","))
    return bindings


class TuiBindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Import the module, not its TestCase, to avoid duplicate discovery.
        await test_tui_actions.TuiActionTests.asyncSetUp(self)
        browser_patch = patch("webbrowser.open")
        self.browser = browser_patch.start()
        self.addCleanup(browser_patch.stop)
        network_patch = patch(
            "socket.socket.connect", side_effect=AssertionError("Network access")
        )
        network_patch.start()
        self.addCleanup(network_patch.stop)
        self.maxDiff = None
        self.test_stdout = sys.stdout
        self.test_stderr = sys.stderr

    @contextmanager
    def keyboard_case(self, **params):
        # Textual's print capture lacks getvalue(), which buffered unittest needs
        # when reporting a subtest failure while the app is still mounted.
        with (
            redirect_stdout(self.test_stdout),
            redirect_stderr(self.test_stderr),
            self.subTest(**params),
        ):
            yield

    def test_00_declared_binding_contract(self):
        for widget_class, expected in (
            (SupportApp, APP_BINDINGS),
            (FocusableContainer, SCROLL_BINDINGS),
        ):
            with self.subTest(widget=widget_class.__name__):
                declared = declared_bindings(widget_class)
                self.assertEqual(len(declared), len(dict(declared)), "Duplicate keys")
                self.assertEqual(dict(declared), expected)

    async def test_every_app_binding_dispatches_to_expected_action(self):
        async with self.app.run_test() as pilot:
            table = self.app.query_one(DataTable)
            with ExitStack() as stack:
                actions = {
                    action: stack.enter_context(
                        patch.object(self.app, "action_" + action)
                    )
                    for action in set(APP_BINDINGS.values())
                }
                for key, expected in APP_BINDINGS.items():
                    with self.keyboard_case(key=key, action=expected):
                        table.focus()
                        await pilot.pause()
                        for action in actions.values():
                            action.reset_mock()
                        await pilot.press(key)
                        await pilot.pause()
                        actions[expected].assert_called_once_with()
                        for name, action in actions.items():
                            if name != expected:
                                action.assert_not_called()
            self.browser.assert_not_called()

    async def test_follow_keyboard_persists_state_and_updates_visible_star(self):
        state_path = str(pathlib.Path(self.temp, "state.yaml"))
        state = StateService(state_file=state_path)
        self.service.state_service = state
        async with self.app.run_test() as pilot:
            case = test_tui_actions.details().case
            self.app.cases = [case]
            self.app.populate_cases_table()
            table = self.app.query_one(DataTable)
            table.focus()
            await pilot.pause()
            self.assertEqual(table.get_cell_at((0, 0)).plain, "1")
            # First press uses the table cursor fallback; second uses selected ID.
            for selected, followed in ((None, True), ("1", False)):
                with self.keyboard_case(followed=followed):
                    self.app.selected_case_id = selected
                    await pilot.press("w")
                    await pilot.pause()
                    persisted = StateService(state_file=state_path)
                    self.assertEqual(
                        "1" in persisted.get_followed_cases("offline"), followed
                    )
                    self.assertEqual(case.is_followed, followed)
                    self.assertEqual(
                        table.get_cell_at((0, 0)).plain,
                        "1 " + chr(0x2605) if followed else "1",
                    )
            self.service.get_case_details.assert_not_called()
            self.browser.assert_not_called()

    async def test_direction_keys_focus_real_panes_in_both_layouts(self):
        async with self.app.run_test(size=(120, 40)) as pilot:
            table = self.app.query_one(DataTable)
            detail = self.app.query_one("#case-detail-container")
            for vertical in (False, True):
                self.app.screen.set_class(vertical, "layout-vertical")
                for key, target, origin in (
                    ("ctrl+up", table, detail),
                    ("ctrl+left", table, detail),
                    ("ctrl+down", detail, table),
                    ("ctrl+right", detail, table),
                ):
                    with self.keyboard_case(vertical=vertical, key=key):
                        origin.focus()
                        await pilot.pause()
                        await pilot.press(key)
                        self.assertIs(self.app.focused, target)
                self.app.selected_case_id = "1"
                self.app.render_case_details(test_tui_actions.details(), [])
                await pilot.pause()
                self.app.action_add_comment()
                await pilot.pause()
                editor = self.app.query_one(TextArea)
                for key in ("ctrl+down", "ctrl+right"):
                    table.focus()
                    await pilot.press(key)
                    self.assertIs(self.app.focused, editor)
                table.focus()
                await pilot.press("ctrl+x")
                await pilot.pause()
                self.assertFalse(self.app.screen.has_class("commenting"))

    async def test_layout_aliases_change_and_persist_layout(self):
        async with self.app.run_test() as pilot:
            self.app.query_one(DataTable).focus()
            initial = self.app.screen.has_class("layout-vertical")
            for key, vertical in (("v", not initial), ("l", initial)):
                with self.keyboard_case(key=key):
                    await pilot.press(key)
                    self.assertEqual(
                        self.app.screen.has_class("layout-vertical"), vertical
                    )
                    self.assertEqual(
                        self.app.layout, "vertical" if vertical else "horizontal"
                    )
                    self.service.state_service.set_tui_layout.assert_called_with(
                        "offline", self.app.layout
                    )

    async def test_cancel_shortcut_works_from_editor_and_guards_dirty_text(self):
        async with self.app.run_test() as pilot:
            self.app.selected_case_id = "1"
            self.app.render_case_details(test_tui_actions.details(), [])
            await pilot.pause()
            self.app.action_add_comment()
            await pilot.pause()
            self.app.query_one(TextArea).focus()
            await pilot.press("ctrl+x")
            await pilot.pause()
            self.assertFalse(self.app.screen.has_class("commenting"))
            self.app.action_add_comment()
            await pilot.pause()
            editor = self.app.query_one(TextArea)
            editor.text = "Keep this draft"
            editor.focus()
            await pilot.press("ctrl+x")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, UnsavedChangesModal)
            self.assertEqual(editor.text, "Keep this draft")
            await pilot.click("#cancel-btn")
            await pilot.pause()
            self.assertEqual(editor.text, "Keep this draft")

    async def test_container_keys_really_scroll_overflowing_content(self):
        async with self.app.run_test(size=(100, 30)) as pilot:
            container = self.app.query_one("#case-detail-container", FocusableContainer)
            await container.query("*").remove()
            await container.mount(Static("\n".join(f"Line {n}" for n in range(200))))
            container.focus()
            await pilot.pause()
            self.assertGreater(container.max_scroll_y, container.size.height)
            for key in SCROLL_BINDINGS:
                with self.keyboard_case(key=key):
                    container.scroll_to(y=container.max_scroll_y // 2, animate=False)
                    await pilot.pause()
                    before = container.scroll_y
                    await pilot.press(key)
                    await pilot.pause()
                    after = container.scroll_y
                    if key in ("up", "pageup", "home"):
                        self.assertLess(after, before)
                    else:
                        self.assertGreater(after, before)
                    if key == "home":
                        self.assertEqual(after, 0)
                    elif key == "end":
                        self.assertEqual(after, container.max_scroll_y)
                    elif key in ("up", "down"):
                        self.assertEqual(abs(after - before), 1)
                    else:
                        self.assertGreater(abs(after - before), 1)

    async def test_printable_shortcuts_type_in_editor_without_app_actions(self):
        async with self.app.run_test() as pilot:
            self.app.selected_case_id = "1"
            self.app.render_case_details(test_tui_actions.details(), [])
            await pilot.pause()
            self.app.action_add_comment()
            await pilot.pause()
            editor = self.app.query_one(TextArea)
            editor.focus()
            await self.assert_printable_keys(pilot, editor, "text")

    async def test_printable_shortcuts_type_in_modal_input_without_app_actions(self):
        async with self.app.run_test() as pilot:
            modal = CaseJumpModal()
            self.app.push_screen(modal)
            await pilot.pause()
            field = modal.query_one(Input)
            self.assertIs(self.app.focused, field)
            await self.assert_printable_keys(pilot, field, "value")
            self.assertIs(self.app.screen, modal)

    async def assert_printable_keys(self, pilot, widget, value_attribute):
        with ExitStack() as stack:
            actions = [
                stack.enter_context(patch.object(self.app, "action_" + action))
                for action in set(APP_BINDINGS.values())
            ]
            expected = ""
            for key in PRINTABLE_KEYS:
                with self.keyboard_case(key=key):
                    await pilot.press(key)
                    expected += key
                    self.assertEqual(getattr(widget, value_attribute), expected)
                    self.assertIs(self.app.focused, widget)
                    for action in actions:
                        action.assert_not_called()
            self.browser.assert_not_called()
