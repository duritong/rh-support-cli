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
from rh_support_lib.services.cache import CacheService
from rh_support_lib.services.cases import CaseService
from rh_support_lib.services.search import SearchService
from rh_support_lib.tui.app import (
    BookmarkModal,
    SupportApp,
    TemplateModal,
    build_filter_params,
)


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

    async def test_jumped_case_composer_uses_loaded_status_outside_queue(self):
        from textual.widgets import Input

        async with self.app.run_test() as pilot:
            self.app.cases = [details("1").case]
            with patch.object(self.app, "_request_case_details"):
                self.app.action_jump_to_case()
                await pilot.pause()
                self.app.screen.query_one(Input).value = "99"
                await pilot.press("enter")
                await pilot.pause()
            for status, expected in (
                ("Closed", "Closed"),
                ("Waiting on Red Hat", "Waiting on Red Hat"),
                ("Waiting on Customer Action Required", "In Progress"),
            ):
                with self.subTest(status=status):
                    self.app.render_case_details(details("99", status=status), [])
                    await pilot.pause()
                    self.app.action_add_comment()
                    await pilot.pause()
                    self.assertEqual(self.app.query_one(Select).value, expected)
                    self.app.action_exit_commenting()

    async def test_tui_comment_submission_status_transitions(self):
        """Verify TUI execute_comment_submission only applies status when transitioning or explicitly changed."""
        async with self.app.run_test() as pilot:
            self.app.cases = [details("1").case]
            await pilot.pause()

            # 1. Case is Waiting on Red Hat, target status matches -> status=None passed to add_comment
            self.app.selected_case_id = "1"
            self.app.render_case_details(details("1", status="Waiting on Red Hat"), [])
            await pilot.pause()
            with patch.object(self.app.case_service, "add_comment") as mock_add:
                self.app.execute_comment_submission(
                    "1",
                    "comment body",
                    "Waiting on Red Hat",
                    self.app._comment_generation,
                )
                mock_add.assert_called_once_with(
                    "1", "comment body", is_public=True, status=None
                )

            # 2. Case is Waiting on Customer, target status is In Progress -> status="In Progress"
            self.app.selected_case_id = "1"
            self.app.render_case_details(
                details("1", status="Waiting on Customer Action Required"), []
            )
            await pilot.pause()
            with patch.object(self.app.case_service, "add_comment") as mock_add:
                self.app.execute_comment_submission(
                    "1", "reply body", "In Progress", self.app._comment_generation
                )
                mock_add.assert_called_once_with(
                    "1", "reply body", is_public=True, status="In Progress"
                )

            # 3. Case is Waiting on Red Hat, user explicitly chose Closed -> status="Closed"
            self.app.selected_case_id = "1"
            self.app.render_case_details(details("1", status="Waiting on Red Hat"), [])
            await pilot.pause()
            with patch.object(self.app.case_service, "add_comment") as mock_add:
                self.app.execute_comment_submission(
                    "1", "close body", "Closed", self.app._comment_generation
                )
                mock_add.assert_called_once_with(
                    "1", "close body", is_public=True, status="Closed"
                )

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

    async def test_real_list_cache_does_not_claim_remote_freshness(self):
        client = Mock(spec=RedHatAPIClient)
        client.get_cache_scope.return_value = "test-scope"
        client.get_token.return_value = "offline-token"
        cache = CacheService(cache_dir=self.temp, scope="test-scope")
        service = CaseService(client, cache_service=cache)
        self.app.case_service = service
        with patch.object(
            service, "_list_cases_graphql", return_value=[details().case]
        ) as remote:
            service.list_cases(
                **{
                    k: v
                    for k, v in self.app._begin_case_list()[1].items()
                    if k != "following"
                }
            )
            remote.reset_mock()
            async with self.app.run_test() as pilot:
                self.app.fetch_cases()
                await pilot.pause()
                remote.assert_not_called()
                chip = self.app.query_one("#chip-freshness")
                self.assertIn("Cached", str(chip.render()))
                self.assertIsNone(self.app.last_refreshed_at)
                self.app.fetch_cases(force=True)
                await pilot.pause()
                remote.assert_called_once()
                self.assertIsNotNone(self.app.last_refreshed_at)
                self.assertIn("Refreshed:", str(chip.render()))
                last_good = self.app.last_refreshed_at
                self.app.fetch_cases()
                self.assertIn("Cached", str(chip.render()))
                self.assertEqual(self.app.last_refreshed_at, last_good)
                old = self.app._begin_case_list()
                old_cases = self.app._read_case_list(old, force=True)
                current = self.app._begin_case_list()
                self.app._read_case_list(current)
                self.assertIsNot(old[8], current[8])
                self.assertEqual(old[8], {"source": "live"})
                self.assertEqual(current[8], {"source": "cache"})
                self.app._apply_case_list(old, old_cases)
                self.assertIn("Cached", str(chip.render()))
                remote.side_effect = RuntimeError("offline")
                self.app.fetch_cases(force=True)
                self.assertEqual(self.app.last_refreshed_at, last_good)
                self.assertIn("Cached", str(chip.render()))
                self.assertEqual(self.app.cases[0].case_number, "1")
                remote.side_effect = None
                remote.reset_mock()
                self.app.poll_cases_background()
                remote.assert_called_once()
                self.assertIn("Refreshed:", str(chip.render()))
                self.assertFalse(self.app._poll_in_flight)

    async def test_search_mixed_and_followed_local_provenance(self):
        client = Mock(spec=RedHatAPIClient)
        client.get_cache_scope.return_value = "test-scope"
        client.get_token.return_value = "offline-token"
        service = CaseService(client)
        self.app.case_service = service
        self.app.search_service = SearchService(client, case_service=service)
        service.cache_service._set_case_details("2", details("2"))
        with patch.object(
            service, "_list_cases_graphql", return_value=[details().case]
        ):
            async with self.app.run_test():
                self.app.active_search_query = "Case"
                self.app.fetch_cases()
                self.assertEqual(len(self.app.cases), 2)
                self.assertIn(
                    "Mixed", str(self.app.query_one("#chip-freshness").render())
                )
                self.app.active_search_query = None
                self.app.config["bookmarks"]["followed"] = {"following": True}
                self.app.active_bookmark = "followed"
                last_good = self.app.last_refreshed_at
                with patch.object(
                    service.state_service, "get_followed_cases", return_value={}
                ):
                    self.app.fetch_cases(force=True)
                self.assertEqual(self.app.last_refreshed_at, last_good)
                self.assertIn(
                    "Local", str(self.app.query_one("#chip-freshness").render())
                )
                followed = TrackedCase(case_number="1", relationship="followed")
                with (
                    patch.object(
                        service.state_service,
                        "get_followed_cases",
                        return_value={"1": followed},
                    ),
                    patch.object(
                        service, "_get_case_details_graphql", return_value=details()
                    ) as remote,
                ):
                    self.app.fetch_cases(force=True)
                    remote.assert_called_once()
                self.assertIn(
                    "Refreshed:", str(self.app.query_one("#chip-freshness").render())
                )

    async def test_scope_chip_matches_resolved_defaults_and_search_clear(self):
        for defaults, no_default, explicit, expected, expected_params in (
            ("team", False, None, "Bookmark: team (default)", {"status": ["Closed"]}),
            (
                ["team", "product"],
                False,
                None,
                "Bookmarks: team, product (default)",
                {"status": ["Closed"], "product": "RHEL"},
            ),
            ("team", True, None, "Bookmark: None", {"status": None}),
            (
                "team",
                False,
                "product",
                "Bookmark: product",
                {"status": None, "product": "RHEL"},
            ),
            ("team", False, "none", "Bookmark: None", {"status": None}),
        ):
            with self.subTest(
                defaults=defaults, no_default=no_default, explicit=explicit
            ):
                app = SupportApp(
                    Mock(spec=RedHatAPIClient),
                    config={
                        "default_bookmark": defaults,
                        "bookmarks": {
                            "team": {"status": "Closed"},
                            "product": {"product": "RHEL"},
                        },
                    },
                    bookmark=explicit,
                    no_default_bookmark=no_default,
                )
                async with app.run_test() as pilot:
                    chip = app.query_one("#chip-scope")
                    self.assertEqual(str(chip.render()), expected)
                    params = build_filter_params(
                        app.config, app.active_bookmark, app.no_default_bookmark
                    )
                    for key, value in expected_params.items():
                        self.assertEqual(params[key], value)
                    app.active_search_query = "[bold]kernel[/bold]"
                    app._sync_header_status()
                    self.assertEqual(
                        str(chip.render()), 'Search: "[bold]kernel[/bold]"'
                    )
                    with patch.object(app, "_request_cases"):
                        app.action_clear_search()
                        await pilot.pause()
                    self.assertEqual(str(chip.render()), expected)

    async def test_scope_chip_explicit_selection_clear_and_literal_metadata(self):
        self.app.config["default_bookmark"] = "team"
        self.app.config["bookmarks"]["[bold]literal[/bold]"] = {"product": "RHEL"}
        self.app.user_identity = "[red]offline[/red]"
        async with self.app.run_test() as pilot:
            self.assertEqual(
                str(self.app.query_one("#chip-identity").render()),
                "User: [red]offline[/red]",
            )
            with patch.object(self.app, "_request_cases"):
                for value, expected in (
                    ("[bold]literal[/bold]", "Bookmark: [bold]literal[/bold]"),
                    ("none", "Bookmark: None"),
                ):
                    self.app.action_select_bookmark()
                    await pilot.pause()
                    self.app.screen.query_one(Select).value = value
                    await pilot.click("#select-btn")
                    await pilot.pause()
                    self.assertEqual(
                        str(self.app.query_one("#chip-scope").render()), expected
                    )
                    self.app.active_search_query = "[red]query[/red]"
                    self.app.action_clear_search()
                    self.assertEqual(
                        str(self.app.query_one("#chip-scope").render()), expected
                    )

    async def test_tui_header_status_chips_update_on_scope_and_freshness(self):
        """Test visible identity, scope, and freshness chips in the TUI header."""
        from textual.widgets import Label

        async with self.app.run_test() as pilot:
            # 1. Inspect initial chip presence
            ident_chip = self.app.query_one("#chip-identity", Label)
            scope_chip = self.app.query_one("#chip-scope", Label)
            fresh_chip = self.app.query_one("#chip-freshness", Label)

            self.assertIn("User:", str(ident_chip.render()))
            self.assertEqual(str(scope_chip.render()), "Bookmark: None")
            self.assertIn("Last live: Never", str(fresh_chip.render()))

            # 2. Scope chip updates immediately when search query changes
            self.app.active_search_query = "kernel panic"
            self.app._sync_header_status()
            await pilot.pause()
            self.assertEqual(str(scope_chip.render()), 'Search: "kernel panic"')

            # 3. Scope chip restores when search is cleared
            self.app.active_search_query = None
            self.app._sync_header_status()
            await pilot.pause()
            self.assertEqual(str(scope_chip.render()), "Bookmark: None")

            # 4. Scope chip updates when bookmark changes
            self.app.active_bookmark = "team"
            self.app._sync_header_status()
            await pilot.pause()
            self.assertEqual(str(scope_chip.render()), "Bookmark: team")

            # 5. Freshness chip updates when case list is applied
            snapshot = self.app._begin_case_list()
            snapshot[8].update(source="live")
            self.app._apply_case_list(snapshot, [details().case])
            await pilot.pause()
            self.assertIn("Refreshed:", str(fresh_chip.render()))
            self.assertNotEqual(str(fresh_chip.render()), "Refreshed: Never")
