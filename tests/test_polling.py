"""Offline polling regressions using Textual's real thread dispatcher."""

import asyncio
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from textual.widgets import DataTable, TextArea

from rh_support_lib.api import RedHatAPIClient
from rh_support_lib.models import Case, CaseComment, CaseDetails
from rh_support_lib.tui.app import SupportApp


def case(number="1", modified="old"):
    return Case(
        number, "Case " + number, "Waiting on Red Hat", "3", last_modified_date=modified
    )


class PollingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = Mock()
        self.service.get_current_user.return_value = "offline"
        self.service.state_service.get_tui_layout.return_value = "vertical"
        self.service.state_service.get_tracked_cases.return_value = {}
        self.service.cache_service.get_case_details.return_value = None
        self.service.check_case_last_modified.return_value = None
        self.attachments = Mock()
        self.attachments.list_attachments.return_value = []
        for target, value in (
            ("CaseService", self.service),
            ("AttachmentService", self.attachments),
        ):
            p = patch("rh_support_lib.tui.app." + target, return_value=value)
            p.start()
            self.addCleanup(p.stop)

        def mount(app):
            app.query_one(DataTable).add_columns(
                "NUMBER", "TITLE", "SEVERITY", "STATUS"
            )

        p = patch.object(SupportApp, "on_mount", mount)
        p.start()
        self.addCleanup(p.stop)
        self.app = SupportApp(
            Mock(spec=RedHatAPIClient),
            config={
                "cache": {"directory": self.temp.name},
                "tui": {"poll_interval": 0},
            },
        )
        self.work = []
        self.release = []

    async def asyncTearDown(self):
        for event in self.release:
            event.set()
        if self.work:
            await asyncio.gather(*self.work, return_exceptions=True)

    def start(self, fn):
        task = asyncio.create_task(asyncio.to_thread(fn))
        self.work.append(task)
        return task

    async def reached(self, event):
        self.assertTrue(
            await asyncio.to_thread(event.wait, 5), "worker did not reach gate"
        )

    def gate(self, result=None, error=None):
        entered, release = threading.Event(), threading.Event()
        self.release.append(release)

        def blocked(**kwargs):
            self.assertNotEqual(threading.get_ident(), self.ui_thread)
            entered.set()
            if not release.wait(5):
                raise AssertionError("gate not released")
            if error:
                raise error
            return result

        return blocked, entered, release

    async def test_empty_poll_clears_rows(self):
        async with self.app.run_test():
            self.ui_thread = threading.get_ident()
            self.app.cases = [case()]
            self.app.populate_cases_table()
            self.service.list_cases.return_value = []
            await self.start(self.app.poll_cases_background)
            self.assertEqual(self.app.cases, [])
            self.assertEqual(self.app.query_one(DataTable).row_count, 0)
            self.service.check_case_last_modified.assert_not_called()

    async def test_poll_refreshes_active_search_instead_of_regular_queue(self):
        async with self.app.run_test():
            self.app.active_search_query = "kernel"
            matches = [case("999")]
            self.service.list_cases.return_value = [case("unrelated")]
            with patch.object(
                self.app.search_service, "search_cases", return_value=matches
            ) as search:
                await self.start(self.app.fetch_cases)
                await self.start(self.app.poll_cases_background)
                self.assertEqual(self.app.cases, matches)
                self.assertEqual(search.call_count, 2)
                self.assertEqual(search.call_args.kwargs["query"], "kernel")
                self.assertIn("Search: 'kernel'", self.app.sub_title)
                self.service.list_cases.assert_not_called()

    async def test_search_load_and_poll_preserve_following_bookmark(self):
        async with self.app.run_test():
            self.app.config["bookmarks"] = {
                "followed": {"following": True, "owner": "team"}
            }
            self.app.active_bookmark = "followed"
            self.app.active_search_query = "kernel"
            with patch.object(
                self.app.search_service, "search_cases", return_value=[]
            ) as search:
                await self.start(self.app.fetch_cases)
                await self.start(self.app.poll_cases_background)
                self.assertEqual(search.call_count, 2)
                for call in search.call_args_list:
                    self.assertTrue(call.kwargs["following"])
                    self.assertEqual(call.kwargs["owner"], "team")

    async def test_query_change_discards_old_search_completion_during_poll(self):
        async with self.app.run_test():
            self.service.list_cases.return_value = []
            self.ui_thread = threading.get_ident()
            blocked, entered, release = self.gate([case("obsolete")])
            self.app.active_search_query = "old"
            with patch.object(
                self.app.search_service, "search_cases", side_effect=blocked
            ) as search:
                pending = self.start(self.app.fetch_cases)
                await self.reached(entered)
                self.app.active_search_query = "new"
                search.side_effect = None
                search.return_value = [case("new-match")]
                await self.start(self.app.poll_cases_background)
                release.set()
                await pending
                self.assertEqual([c.case_number for c in self.app.cases], ["new-match"])
                self.assertEqual(search.call_args.kwargs["query"], "new")

    async def test_expired_or_disabled_cache_refreshes_display_and_preserves_draft(
        self,
    ):
        async with self.app.run_test() as pilot:
            for enabled in (True, False):
                with self.subTest(cache_enabled=enabled):
                    self.app.config["cache"]["enabled"] = enabled
                    self.ui_thread = threading.get_ident()
                    self.app.selected_case_id = "1"
                    self.app.render_case_details(CaseDetails(case(), "old"), [])
                    self.app.action_add_comment()
                    await pilot.pause()
                    self.app.query_one(TextArea).text = "active draft"
                    self.service.list_cases.return_value = [case(modified="new")]
                    fresh = CaseDetails(case(modified="new"), "new")
                    self.service.get_case_details.return_value = fresh
                    await self.start(self.app.poll_cases_background)
                    self.assertIs(self.app.current_details, fresh)
                    self.assertEqual(self.app.query_one(TextArea).text, "active draft")
                    self.service.cache_service.get_case_details.assert_not_called()
                    self.service.check_case_last_modified.assert_not_called()

    async def test_changed_severity_refreshes_without_waiting_for_modified_date(self):
        async with self.app.run_test():
            self.app.selected_case_id = "1"
            old = case()
            old.id = "record"
            self.app.render_case_details(CaseDetails(old, "old"), [])
            new = case()
            new.severity = "1 (Urgent)"
            self.service.list_cases.return_value = [new]
            self.service.check_case_last_modified.return_value = (
                "record",
                old.last_modified_date,
                old.status,
                "",
                "",
            )
            fresh = CaseDetails(new, "new")
            self.service.get_case_details.return_value = fresh
            await self.start(self.app.poll_cases_background)
            self.assertIs(self.app.current_details, fresh)
            self.service.check_case_last_modified.assert_not_called()

    async def test_inverted_list_completions_and_errors(self):
        async with self.app.run_test():
            self.ui_thread = threading.get_ident()
            for error in (None, RuntimeError("obsolete")):
                with self.subTest(error=error):
                    blocked, entered, release = self.gate([case("old")], error)
                    self.service.list_cases.side_effect = blocked
                    errors = Mock(wraps=self.app.show_error)
                    self.app.show_error = errors
                    old = self.start(self.app.fetch_cases)
                    await self.reached(entered)
                    self.service.list_cases.side_effect = None
                    self.service.list_cases.return_value = [case("new")]
                    await self.start(self.app.fetch_cases)
                    release.set()
                    await old
                    self.assertEqual(self.app.cases[0].case_number, "new")
                    errors.assert_not_called()

    async def test_filter_change_discards_poll(self):
        async with self.app.run_test():
            self.ui_thread = threading.get_ident()
            blocked, entered, release = self.gate([case("obsolete")])
            self.service.list_cases.side_effect = blocked
            task = self.start(self.app.poll_cases_background)
            await self.reached(entered)
            self.app.config["bookmarks"] = {"next": {"product": "RHEL"}}
            self.app.active_bookmark = "next"
            release.set()
            await task
            self.assertEqual(self.app.cases, [])

    async def test_poll_single_flight_and_ui_responsive(self):
        async with self.app.run_test() as pilot:
            self.ui_thread = threading.get_ident()
            blocked, entered, release = self.gate([])
            self.service.list_cases.side_effect = blocked
            task = self.start(self.app.poll_cases_background)
            await self.reached(entered)
            await self.start(self.app.poll_cases_background)
            self.app.trigger_background_poll()
            await pilot.pause()
            self.assertEqual(self.service.list_cases.call_count, 1)
            self.assertFalse(task.done())
            release.set()
            await task
            self.service.list_cases.side_effect = None
            self.service.list_cases.return_value = []
            await self.start(self.app.poll_cases_background)
            self.assertEqual(self.service.list_cases.call_count, 2)

    async def test_new_selection_or_detail_request_during_poll_io(self):
        async with self.app.run_test():
            self.ui_thread = threading.get_ident()
            for change_selection in (True, False):
                with self.subTest(change_selection=change_selection):
                    self.app.selected_case_id = "1"
                    displayed = CaseDetails(case(), "displayed")
                    self.app.render_case_details(displayed, [])
                    blocked, entered, release = self.gate([case(modified="new")])
                    self.service.list_cases.side_effect = blocked
                    task = self.start(self.app.poll_cases_background)
                    await self.reached(entered)
                    if change_selection:
                        self.app.selected_case_id = "2"
                    self.app._detail_generation += 1
                    release.set()
                    await task
                    self.assertIs(self.app.current_details, displayed)
                    self.service.get_case_details.assert_not_called()

    async def test_poll_detail_completion_respects_newer_requests_and_filters(self):
        async with self.app.run_test():
            self.ui_thread = threading.get_ident()
            for change in ("selection", "detail", "list", "filter"):
                with self.subTest(change=change):
                    self.app.active_bookmark = None
                    self.app.selected_case_id = "1"
                    displayed = CaseDetails(case(), "displayed")
                    self.app.render_case_details(displayed, [])
                    self.service.list_cases.return_value = [case(modified="new")]
                    blocked, entered, release = self.gate()

                    def details(*args, gate=blocked, **kwargs):
                        gate(**kwargs)
                        return CaseDetails(case(modified="new"), "obsolete")

                    self.service.get_case_details.side_effect = details
                    task = self.start(self.app.poll_cases_background)
                    await self.reached(entered)
                    # Single-flight covers detail I/O, not just list I/O.
                    count = self.service.list_cases.call_count
                    await self.start(self.app.poll_cases_background)
                    self.assertEqual(self.service.list_cases.call_count, count)
                    if change == "selection":
                        self.app.selected_case_id = "2"
                    elif change == "detail":
                        self.app._detail_generation += 1
                    elif change == "list":
                        self.service.list_cases.return_value = []
                        await self.start(self.app.fetch_cases)
                    else:
                        self.app.active_bookmark = "none"
                    release.set()
                    await task
                    self.assertIs(self.app.current_details, displayed)

    async def test_same_filter_queued_refreshes_use_scheduling_order(self):
        async with self.app.run_test():
            self.ui_thread = threading.get_ident()
            queued = []
            with patch.object(
                self.app, "run_worker", side_effect=lambda fn, **kw: queued.append(fn)
            ):
                self.app._request_cases()
                self.app._request_cases(force=True)
            self.service.list_cases.return_value = [case("new")]
            await self.start(queued[1])
            self.service.list_cases.return_value = [case("obsolete")]
            errors = Mock(wraps=self.app.show_error)
            self.app.show_error = errors
            await self.start(queued[0])
            self.assertEqual(self.app.cases[0].case_number, "new")
            errors.assert_not_called()

    async def test_new_list_success_supersedes_inflight_poll(self):
        async with self.app.run_test():
            self.ui_thread = threading.get_ident()
            blocked, entered, release = self.gate([case("obsolete")])
            self.service.list_cases.side_effect = blocked
            task = self.start(self.app.poll_cases_background)
            await self.reached(entered)
            self.service.list_cases.side_effect = None
            self.service.list_cases.return_value = [case("new")]
            await self.start(self.app.fetch_cases)
            release.set()
            await task
            self.assertEqual(self.app.cases[0].case_number, "new")

    async def test_list_error_cannot_erase_newer_detail_success(self):
        async with self.app.run_test():
            self.ui_thread = threading.get_ident()
            self.app.selected_case_id = "1"
            blocked, entered, release = self.gate(error=RuntimeError("obsolete"))
            self.service.list_cases.side_effect = blocked
            task = self.start(self.app.fetch_cases)
            await self.reached(entered)
            self.app.render_case_details(CaseDetails(case(), "new success"), [])
            errors = Mock(wraps=self.app.show_error)
            self.app.show_error = errors
            release.set()
            await task
            errors.assert_not_called()

    async def test_failed_poll_releases_single_flight_and_callbacks_run_on_ui(self):
        async with self.app.run_test():
            self.ui_thread = threading.get_ident()
            returned_thread = await self.start(
                lambda: self.app._safe_call(lambda: threading.get_ident())
            )
            self.assertEqual(returned_thread, self.ui_thread)
            self.service.list_cases.side_effect = RuntimeError("offline")
            await self.start(self.app.poll_cases_background)
            self.assertFalse(self.app._poll_in_flight)
            self.service.list_cases.side_effect = None
            self.service.list_cases.return_value = [case()]
            apply = self.app._apply_case_list
            threads = []

            def checked(*args, **kwargs):
                threads.append(threading.get_ident())
                return apply(*args, **kwargs)

            with patch.object(self.app, "_apply_case_list", checked):
                await self.start(self.app.poll_cases_background)
            self.assertEqual(threads, [self.ui_thread])
            self.assertEqual(self.app.cases[0].case_number, "1")

    async def test_invalid_filter_does_not_latch_poll_or_invalidate_requests(self):
        async with self.app.run_test():
            generation = self.app._list_generation
            with (
                patch(
                    "rh_support_lib.tui.app.build_filter_params",
                    side_effect=ValueError("invalid filter"),
                ),
                self.assertRaisesRegex(ValueError, "invalid filter"),
            ):
                self.app.trigger_background_poll()
            self.assertFalse(self.app._poll_in_flight)
            self.assertEqual(self.app._list_generation, generation)
            self.service.list_cases.return_value = []
            await self.start(self.app.poll_cases_background)
            self.service.list_cases.assert_called_once()

    async def test_selected_case_leaving_filter_still_refreshes_and_keeps_draft(self):
        async with self.app.run_test() as pilot:
            self.app.selected_case_id = "1"
            self.app.render_case_details(CaseDetails(case(), "open"), [])
            self.app.action_add_comment()
            await pilot.pause()
            self.app.query_one(TextArea).text = "still writing"
            closed = case()
            closed.id = "internal-1"
            closed.status = "Closed"
            fresh = CaseDetails(closed, "closed")
            self.service.list_cases.return_value = []
            self.service.check_case_last_modified.return_value = (
                closed.id,
                closed.last_modified_date,
                closed.status,
                "",
                "",
            )
            self.service.get_case_details.return_value = fresh
            await self.start(self.app.poll_cases_background)
            self.assertIs(self.app.current_details, fresh)
            self.assertEqual(self.app.query_one(DataTable).row_count, 0)
            self.assertEqual(self.app.query_one(TextArea).text, "still writing")
            self.service.check_case_last_modified.assert_called_once_with("1")
            self.service.get_case_details.assert_called_once_with("1", use_cache=False)

    async def test_probe_compares_displayed_case_and_latest_comment_not_cache(self):
        async with self.app.run_test():
            self.app.selected_case_id = "1"
            original = case()
            original.id = "internal-1"
            displayed = CaseDetails(
                original, "displayed", [CaseComment("c1", "body", "date1", "author")]
            )
            baseline = ("internal-1", "old", original.status, "c1", "date1")
            probes = [baseline, None]
            for index, value in enumerate(
                ("internal-2", "new", "Closed", "c2", "date2")
            ):
                changed = list(baseline)
                changed[index] = value
                probes.append(tuple(changed))
            for probe in probes:
                with self.subTest(probe=probe):
                    self.app.render_case_details(displayed, [])
                    self.service.list_cases.return_value = [case()]
                    self.service.check_case_last_modified.reset_mock()
                    self.service.check_case_last_modified.return_value = probe
                    self.service.get_case_details.reset_mock()
                    self.attachments.list_attachments.reset_mock()
                    fresh = CaseDetails(case(), "fresh")
                    self.service.get_case_details.return_value = fresh
                    await self.start(self.app.poll_cases_background)
                    self.service.check_case_last_modified.assert_called_once_with("1")
                    expected = 0 if probe == baseline else 1
                    self.assertEqual(self.service.get_case_details.call_count, expected)
                    self.assertEqual(
                        self.attachments.list_attachments.call_count, expected
                    )
                    self.assertIs(
                        self.app.current_details, displayed if not expected else fresh
                    )
            self.service.cache_service.get_case_details.assert_not_called()

    async def test_probe_is_single_flight_and_cannot_supersede_newer_requests(self):
        async with self.app.run_test() as pilot:
            self.ui_thread = threading.get_ident()
            for change in ("selection", "detail", "list", "filter"):
                with self.subTest(change=change):
                    self.app.active_bookmark = None
                    self.app.selected_case_id = "1"
                    displayed = CaseDetails(case(), "displayed")
                    self.app.render_case_details(displayed, [])
                    self.service.list_cases.return_value = [case()]
                    blocked, entered, release = self.gate()

                    def probe(*args, gate=blocked):
                        return gate()

                    self.service.check_case_last_modified.side_effect = probe
                    task = self.start(self.app.poll_cases_background)
                    await self.reached(entered)
                    count = self.service.list_cases.call_count
                    await self.start(self.app.poll_cases_background)
                    self.app.trigger_background_poll()
                    await pilot.pause()
                    self.assertFalse(task.done())
                    self.assertEqual(self.service.list_cases.call_count, count)
                    if change == "selection":
                        self.app.selected_case_id = "2"
                    elif change == "detail":
                        self.app._detail_generation += 1
                    elif change == "list":
                        await self.start(self.app.fetch_cases)
                    else:
                        self.app.active_bookmark = "none"
                    release.set()
                    await task
                    self.assertIs(self.app.current_details, displayed)
                    self.service.get_case_details.assert_not_called()
                    self.assertFalse(self.app._poll_in_flight)

    async def test_probe_compares_details_displayed_at_completion(self):
        async with self.app.run_test():
            self.ui_thread = threading.get_ident()
            self.app.selected_case_id = "1"
            self.app.render_case_details(CaseDetails(case(), "old"), [])
            self.service.list_cases.return_value = [case()]
            newer_case = case(modified="new")
            newer_case.id = "internal-1"
            newer = CaseDetails(newer_case, "newer")
            blocked, entered, release = self.gate(
                (newer_case.id, "new", newer_case.status, "", "")
            )

            def probe(*args):
                return blocked()

            self.service.check_case_last_modified.side_effect = probe
            task = self.start(self.app.poll_cases_background)
            await self.reached(entered)
            self.app.render_case_details(newer, [])
            release.set()
            await task
            self.assertIs(self.app.current_details, newer)
            self.service.get_case_details.assert_not_called()
