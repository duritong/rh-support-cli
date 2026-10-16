"""Standalone offline regressions for advertised TUI actions and read dwell."""

import os
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rich.panel import Panel
from textual.widgets import DataTable, Select, TextArea

from rh_support_lib.api import RedHatAPIClient
from rh_support_lib.models import (
    Case,
    CaseCheckpoint,
    CaseComment,
    CaseDetails,
    TrackedCase,
)
from rh_support_lib.tui.app import BookmarkModal, SupportApp, TemplateModal


def details(number="1", comment="c1", status="Waiting on Red Hat"):
    return CaseDetails(
        Case(
            number,
            "Case " + number,
            status,
            "3",
            last_modified_date="version",
            id="internal-" + number,
        ),
        "Description",
        [CaseComment(comment, "Body " + comment, "2026-10-15T12:00:00Z", "Author")],
    )


class TuiActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = temp.name
        self.service = Mock()
        self.service.get_current_user.return_value = "offline"
        self.service.state_service.get_tui_layout.return_value = "vertical"
        self.service.state_service.get_tracked_cases.return_value = {}
        self.service.cache_service.get_case_details.return_value = None
        self.attachments = Mock()
        self.attachments.list_attachments.return_value = []
        patches = [
            patch.dict(
                os.environ,
                {
                    "HOME": self.temp,
                    "XDG_CONFIG_HOME": self.temp,
                    "XDG_CACHE_HOME": self.temp,
                },
            ),
            patch("rh_support_lib.tui.app.CaseService", return_value=self.service),
            patch(
                "rh_support_lib.tui.app.AttachmentService",
                return_value=self.attachments,
            ),
            patch(
                "rh_support_lib.tui.app.resolve_templates_dir", return_value=self.temp
            ),
            patch.object(
                SupportApp,
                "on_mount",
                lambda app: app.query_one(DataTable).add_columns(
                    "NUMBER", "TITLE", "SEVERITY", "STATUS"
                ),
            ),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = SupportApp(
            Mock(spec=RedHatAPIClient),
            config={
                "tui": {"poll_interval": 0},
                "bookmarks": {"team": {"status": "open"}},
            },
            comments_order="oldest",
        )

    async def test_mounted_template_initial_values_and_apply(self):
        async with self.app.run_test() as pilot:
            for default, expected, result in (
                ("", None, ""),
                ("missing", None, ""),
                ("incident", "incident", "incident"),
            ):
                pathlib.Path(self.temp, "incident.yaml").write_text("summary: test\n")
                with self.subTest(default=default):
                    callback = Mock()
                    self.app.push_screen(TemplateModal("1", default), callback)
                    await pilot.pause()
                    value = self.app.screen.query_one(Select).value
                    if expected is None:
                        self.assertNotIsInstance(value, str)
                    else:
                        self.assertEqual(value, expected)
                    await pilot.click("#apply-btn")
                    await pilot.pause()
                    callback.assert_called_once_with(result)
            pathlib.Path(self.temp, "incident.yaml").unlink()
            callback = Mock()
            self.app.push_screen(TemplateModal("1"), callback)
            await pilot.pause()
            select = self.app.screen.query_one(Select)
            self.assertNotIsInstance(select.value, str)
            select.value = "none"
            await pilot.click("#apply-btn")
            await pilot.pause()
            callback.assert_called_once_with("")

    async def test_mounted_bookmark_values_and_blank_apply(self):
        async with self.app.run_test() as pilot:
            for active, expected, result in (
                (None, None, ""),
                ("missing", None, ""),
                ("None (Clear Filters)", None, ""),
                ("team", "team", "team"),
                ("none", "none", "none"),
            ):
                with self.subTest(active=active):
                    callback = Mock()
                    self.app.push_screen(
                        BookmarkModal(self.app.config, active), callback
                    )
                    await pilot.pause()
                    value = self.app.screen.query_one(Select).value
                    if expected is None:
                        self.assertNotIsInstance(value, str)
                    else:
                        self.assertEqual(value, expected)
                    await pilot.click("#select-btn")
                    await pilot.pause()
                    callback.assert_called_once_with(result)

    async def test_bookmark_keyboard_blank_does_not_mutate_filters(self):
        async with self.app.run_test() as pilot:
            self.app.query_one(DataTable).focus()
            with patch.object(self.app, "_request_cases") as request:
                await pilot.press("b")
                self.assertIsInstance(self.app.screen, BookmarkModal)
                await pilot.click("#select-btn")
                await pilot.pause()
                self.assertIsNone(self.app.active_bookmark)
                request.assert_not_called()
                await pilot.press("b")
                self.app.screen.query_one(Select).value = "none"
                await pilot.click("#select-btn")
                await pilot.pause()
                self.assertEqual(self.app.active_bookmark, "none")
                request.assert_called_once_with()

    async def test_focus_keyboard_toggles_current_pane(self):
        async with self.app.run_test() as pilot:
            for selector, focused in (
                ("#case-table", "focused-left"),
                ("#case-detail-container", "focused-right"),
            ):
                self.app.query_one(selector).focus()
                await pilot.press("f")
                self.assertTrue(self.app.screen.has_class(focused))
                await pilot.press("f")
                self.assertFalse(self.app.screen.has_class(focused))

    async def test_order_keyboard_rerenders_locally_and_preserves_draft(self):
        async with self.app.run_test() as pilot:
            self.app.query_one(DataTable).focus()
            await pilot.press("o")  # No details is safe, and still changes preference.
            self.assertEqual(self.app.comments_order, "newest")
            current = details()
            current.comments.append(
                CaseComment("c2", "Body c2", "2026-10-15T13:00:00Z", "Author")
            )
            self.app.selected_case_id = "1"
            self.app.render_case_details(current, [])
            await pilot.pause()
            self.app.action_add_comment()
            await pilot.pause()
            editor = self.app.query_one(TextArea)
            editor.focus()
            await pilot.press("b", "f", "o")
            self.assertEqual(editor.text, "bfo")
            self.assertEqual(self.app.comments_order, "newest")
            self.assertNotIsInstance(self.app.screen, BookmarkModal)
            self.assertFalse(self.app.screen.has_class("focused-comment"))
            self.app.query_one("#case-detail-container").focus()
            with patch("rh_support_lib.tui.app.Panel", wraps=Panel) as rendered:
                await pilot.press("o")
                await pilot.pause()
            self.assertEqual(self.app.comments_order, "oldest")
            self.assertIs(self.app.current_details, current)
            self.assertEqual(editor.text, "bfo")
            panels = [
                str(call.kwargs["title"])
                for call in rendered.call_args_list
                if "Author" in str(call.kwargs.get("title", ""))
            ]
            self.assertIn("12:00:00", panels[0])
            self.assertIn("13:00:00", panels[1])
            self.service.get_case_details.assert_not_called()
            self.attachments.list_attachments.assert_not_called()

    def track_unread(self):
        self.service.state_service.get_tracked_cases.return_value = {
            number: TrackedCase(
                number,
                read_checkpoint=CaseCheckpoint(
                    status="Waiting on Red Hat",
                    last_read_comment_date="2026-10-14T12:00:00Z",
                ),
            )
            for number in ("1", "2")
        }
        self.timers = []

        def timer(delay, callback, **kwargs):
            self.assertEqual(delay, 2.5)
            handle = Mock()
            self.timers.append((handle, callback))
            return handle

        patcher = patch.object(self.app, "set_timer", side_effect=timer)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_dwell_navigation_a_b_a_rejects_stopped_callbacks(self):
        async with self.app.run_test() as pilot:
            self.track_unread()
            original = details()
            self.app.selected_case_id = "1"
            self.app.render_case_details(original, [])
            await pilot.pause()
            first, callback = self.timers[-1]
            with patch.object(self.app, "run_worker"):
                for number in ("2", "1"):
                    self.app.on_row_selected(
                        SimpleNamespace(row_key=SimpleNamespace(value=number))
                    )
                    self.app.render_case_details(
                        original if number == "1" else details("2"), []
                    )
                    await pilot.pause()
            first.stop.assert_called_once_with()
            for _, stale in self.timers[:-1]:
                stale()
            callback()
            self.service.state_service.mark_case_read.assert_not_called()
            self.timers[-1][1]()
            self.service.state_service.mark_case_read.assert_called_once_with(
                user="offline",
                case_number="1",
                status="Waiting on Red Hat",
                severity="3",
                last_comment_id="c1",
                last_comment_date="2026-10-15T12:00:00Z",
            )

    async def test_dwell_rerender_and_new_comments_supersede_old_version(self):
        async with self.app.run_test() as pilot:
            self.track_unread()
            self.app.selected_case_id = "1"
            original = details()
            self.app.render_case_details(original, [])
            await pilot.pause()
            self.app.action_toggle_comment_order()
            await pilot.pause()
            fresh = details(comment="c2", status="Waiting on Customer")
            self.app.render_case_details(fresh, [])
            await pilot.pause()
            for handle, callback in self.timers[:-1]:
                handle.stop.assert_called_once_with()
                callback()
            self.service.state_service.mark_case_read.assert_not_called()
            self.timers[-1][1]()
            marked = self.service.state_service.mark_case_read.call_args.kwargs
            self.assertEqual(marked["last_comment_id"], "c2")
            self.assertEqual(marked["status"], "Waiting on Customer")
            self.timers[-1][1]()
            self.service.state_service.mark_case_read.assert_called_once()

    async def test_status_only_dwell_preserves_metadata_and_clears_badges(self):
        async with self.app.run_test() as pilot:
            self.track_unread()
            current = details(status="Waiting on Customer")
            current.comments = []
            current.case.has_unread = True
            current.case.unread_count = 0
            current.case.status_changed = True
            self.app.cases = [current.case]
            self.app.selected_case_id = "1"
            self.app.render_case_details(current, [])
            await pilot.pause()
            self.assertEqual(len(self.timers), 1)
            self.timers[-1][1]()
            self.service.state_service.mark_case_read.assert_called_once_with(
                user="offline",
                case_number="1",
                status="Waiting on Customer",
                severity="3",
                last_comment_id=None,
                last_comment_date=None,
            )
            self.assertFalse(current.case.has_unread)
            self.assertFalse(current.case.status_changed)
            self.assertEqual(current.case.unread_count, 0)

    async def test_unchanged_poll_preserves_dwell_and_untracked_render_cancels(self):
        async with self.app.run_test() as pilot:
            self.track_unread()
            current = details()
            self.app.selected_case_id = "1"
            self.app.render_case_details(current, [])
            await pilot.pause()
            timer, callback = self.timers[-1]
            snapshot = self.app._begin_case_list()
            latest = current.comments[-1]
            self.assertIsNone(
                self.app._prepare_polled_details(
                    snapshot,
                    (
                        current.case.id,
                        current.case.last_modified_date,
                        current.case.status,
                        latest.id,
                        latest.created_date,
                    ),
                )
            )
            timer.stop.assert_not_called()
            self.service.state_service.get_tracked_cases.return_value = {}
            self.app.render_case_details(current, [])
            await pilot.pause()
            timer.stop.assert_called_once_with()
            self.assertEqual(len(self.timers), 1)
            callback()
            self.service.state_service.mark_case_read.assert_not_called()

    async def test_dwell_refresh_and_poll_refetch_cancel_before_io(self):
        async with self.app.run_test() as pilot:
            self.track_unread()
            self.app.selected_case_id = "1"
            for action in ("detail", "refresh", "poll"):
                with self.subTest(action=action):
                    self.app.render_case_details(details(), [])
                    await pilot.pause()
                    timer, callback = self.timers[-1]
                    with patch.object(self.app, "run_worker"):
                        if action == "detail":
                            self.app._request_case_details("1", force=True)
                        elif action == "refresh":
                            self.app.query_one("#case-detail-container").focus()
                            await pilot.pause()
                            self.app.action_refresh()
                        else:
                            snapshot = self.app._begin_case_list()
                            self.assertIsNotNone(
                                self.app._prepare_polled_details(snapshot, None)
                            )
                    timer.stop.assert_called_once_with()
                    callback()
                    self.service.state_service.mark_case_read.assert_not_called()

    async def test_list_only_refresh_preserves_displayed_version_dwell(self):
        async with self.app.run_test() as pilot:
            self.track_unread()
            self.app.selected_case_id = "1"
            self.app.render_case_details(details(), [])
            await pilot.pause()
            timer, callback = self.timers[-1]
            self.app.query_one(DataTable).focus()
            await pilot.pause()
            with patch.object(self.app, "run_worker"):
                self.app.action_refresh()
            timer.stop.assert_not_called()
            callback()
            self.service.state_service.mark_case_read.assert_called_once()

    async def test_dwell_identity_and_missing_details_guard(self):
        async with self.app.run_test() as pilot:
            self.track_unread()
            self.app.selected_case_id = "1"
            self.app.render_case_details(details(), [])
            await pilot.pause()
            callback = self.timers[-1][1]
            for current in (None, details()):
                self.app.current_details = current
                callback()
            self.service.state_service.mark_case_read.assert_not_called()

    async def test_jump_to_case_modal_navigation(self):
        from rh_support_lib.tui.app import CaseJumpModal

        async with self.app.run_test() as pilot:
            self.app.action_jump_to_case()
            await pilot.pause()
            self.assertIsInstance(self.app.screen, CaseJumpModal)

            with patch.object(self.app, "_request_case_details") as mock_req:
                self.app.screen.dismiss("04526149")
                await pilot.pause()
                self.assertEqual(self.app.selected_case_id, "04526149")
                mock_req.assert_called_once_with("04526149")

    async def test_jump_to_case_preserves_dirty_comment_draft(self):
        from rh_support_lib.tui.app import UnsavedChangesModal

        async with self.app.run_test() as pilot:
            current = details()
            self.app.selected_case_id = "1"
            self.app.render_case_details(current, [])
            await pilot.pause()
            self.app.action_add_comment()
            await pilot.pause()
            text_area = self.app.query_one(TextArea)
            text_area.text = "Unsaved draft comment"
            await pilot.pause()

            # Attempt jumping to case 2 while draft is dirty
            self.app.select_case_by_id("2")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, UnsavedChangesModal)
            self.assertEqual(self.app.selected_case_id, "1")

    async def test_case_search_modal_filters_table_and_escape_clears(self):
        from rh_support_lib.tui.app import CaseSearchModal

        async with self.app.run_test() as pilot:
            # 1. Action opens CaseSearchModal
            self.app.action_search_cases()
            await pilot.pause()
            self.assertIsInstance(self.app.screen, CaseSearchModal)

            # 2. Dismiss with search query
            matching_case = Case(
                "999",
                "Kernel panic on NVMe",
                "Waiting on Red Hat",
                "1",
                id="internal-999",
            )
            with patch.object(
                self.app.search_service, "search_cases", return_value=[matching_case]
            ) as mock_search:
                self.app.screen.dismiss("kernel panic")
                await pilot.pause()
                self.assertEqual(self.app.active_search_query, "kernel panic")
                self.assertIn("Search: 'kernel panic'", self.app.sub_title)
                mock_search.assert_called_once()
                self.assertEqual(len(self.app.cases), 1)
                self.assertEqual(self.app.cases[0].case_number, "999")

            # 3. Escape key clears search and restores full queue
            with patch.object(self.app, "_request_cases") as mock_req:
                await pilot.press("escape")
                await pilot.pause()
                self.assertIsNone(self.app.active_search_query)
                self.assertEqual(self.app.sub_title, "")
                mock_req.assert_called_once_with(force=True)

    async def test_kcs_search_modal_open_and_insert_link(self):
        from rh_support_lib.tui.app import KCSSearchModal

        async with self.app.run_test() as pilot:
            current = details()
            self.app.selected_case_id = "1"
            self.app.render_case_details(current, [])
            await pilot.pause()

            # 1. Action opens KCSSearchModal with case summary pre-filled
            self.app.action_search_kcs()
            await pilot.pause()
            self.assertIsInstance(self.app.screen, KCSSearchModal)
            self.assertEqual(self.app.screen.initial_query, "Case 1")

            # 2. Test dismiss with ("open", url, title)
            with patch("webbrowser.open") as mock_open:
                self.app.screen.dismiss(
                    (
                        "open",
                        "https://access.redhat.com/solutions/12345",
                        "Test Solution",
                    )
                )
                await pilot.pause()
                mock_open.assert_called_once_with(
                    "https://access.redhat.com/solutions/12345"
                )

            # 3. Test dismiss with ("insert", url, title)
            self.app.action_search_kcs()
            await pilot.pause()
            self.app.screen.dismiss(
                (
                    "insert",
                    "https://access.redhat.com/solutions/12345",
                    "Test Solution",
                )
            )
            await pilot.pause()
            self.assertTrue(self.app.screen.has_class("commenting"))
            editor = self.app.query_one(TextArea)
            self.assertIn(
                "[Test Solution](https://access.redhat.com/solutions/12345)",
                editor.text,
            )
