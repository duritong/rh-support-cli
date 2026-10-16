"""Mounted regressions for knowledgebase search and case/draft ownership."""

import asyncio
import threading
import unittest
from unittest.mock import Mock, patch

from rich.text import Text
from textual.widgets import DataTable, Input, TextArea

from rh_support_lib.api import AuthenticationError
from rh_support_lib.errors import ReadError
from rh_support_lib.models import KCSSearchResult
from rh_support_lib.tui.app import KCSSearchModal
from tests import test_tui_actions as fixtures


class KcsModalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.TuiActionTests.asyncSetUp(self)
        self.app.search_service = Mock()
        self.result = KCSSearchResult(
            "123", "Solution", view_uri="https://access.redhat.com/solutions/123"
        )
        self.app.search_service.search_kcs.return_value = [self.result]

    async def test_enter_on_solution_does_not_select_it_as_a_case(self):
        async with self.app.run_test() as pilot:
            self.app.selected_case_id = "1"
            self.app.render_case_details(fixtures.details(), [])
            await pilot.pause()
            with (
                patch("webbrowser.open") as browser,
                patch.object(self.app, "_request_case_details") as request,
            ):
                self.app.action_search_kcs()
                await pilot.pause()
                self.app.screen.query_one("#kcs-results-table", DataTable).focus()
                await pilot.press("enter")
                await pilot.pause()
                browser.assert_called_once_with(self.result.url)
                self.assertEqual(self.app.selected_case_id, "1")
                request.assert_not_called()

    async def test_search_is_responsive_and_ignores_superseded_results(self):
        entered, release = threading.Event(), threading.Event()
        threads = []

        def search(query, **kwargs):
            threads.append(threading.get_ident())
            if query == "first":
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Test did not release first search")
                return [KCSSearchResult("old", "Old")]
            return [self.result]

        self.app.search_service.search_kcs.side_effect = search
        try:
            async with self.app.run_test() as pilot:
                modal = KCSSearchModal(self.app.search_service, initial_query="first")
                self.app.push_screen(modal)
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                self.assertNotEqual(threads[0], threading.get_ident())
                field = modal.query_one(Input)
                field.value = "next"
                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(modal.results, [self.result])
                release.set()
                await self.app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(modal.results, [self.result])
        finally:
            release.set()

    async def test_search_completion_after_close_does_not_touch_case_view(self):
        entered, release = threading.Event(), threading.Event()

        def search(**kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release search")
            return [self.result]

        self.app.search_service.search_kcs.side_effect = search
        try:
            async with self.app.run_test() as pilot:
                modal = KCSSearchModal(self.app.search_service, initial_query="first")
                self.app.push_screen(modal)
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                with patch.object(modal, "query_one", wraps=modal.query_one) as query:
                    modal.dismiss(None)
                    await pilot.pause()
                    query.reset_mock()
                    release.set()
                    await self.app.workers.wait_for_complete()
                    await pilot.pause()
                    query.assert_not_called()
                self.assertNotIsInstance(self.app.screen, KCSSearchModal)
        finally:
            release.set()

    async def test_rejected_insertion_does_not_leak_into_a_later_case_draft(self):
        async with self.app.run_test() as pilot:
            self.app.selected_case_id = "1"
            self.app.render_case_details(fixtures.details(), [])
            await pilot.pause()
            self.app.action_add_comment()
            await pilot.pause()
            self.app.action_exit_commenting()
            self.app.selected_case_id = "2"
            self.app.append_to_comment_draft("case 2 solution")
            await pilot.pause()
            self.assertEqual(self.app.query_one(TextArea).text, "")
            self.app.selected_case_id = "3"
            self.app.render_case_details(fixtures.details("3"), [])
            await pilot.pause()
            self.app.action_add_comment()
            await pilot.pause()
            self.assertEqual(self.app.query_one(TextArea).text, "")

    async def test_deferred_insertion_cannot_modify_a_replaced_draft(self):
        async with self.app.run_test() as pilot:
            self.app.selected_case_id = "1"
            self.app.render_case_details(fixtures.details(), [])
            await pilot.pause()
            self.app.action_add_comment()
            await pilot.pause()
            callbacks = []
            with patch.object(
                self.app, "call_after_refresh", side_effect=callbacks.append
            ):
                self.app.append_to_comment_draft("old solution")
            self.app._comment_generation += 1
            self.app.query_one(TextArea).text = "new draft"
            callbacks[0]()
            self.assertEqual(self.app.query_one(TextArea).text, "new draft")

    async def test_kcs_metadata_is_literal_not_rich_markup(self):
        self.result.title = "Failure [/dev/sda]"
        self.result.product = "RHEL [bold]literal[/bold]"
        self.result.document_kind = "[/]"
        async with self.app.run_test() as pilot:
            modal = KCSSearchModal(self.app.search_service, initial_query="disk error")
            self.app.push_screen(modal)
            await pilot.pause()
            await self.app.workers.wait_for_complete()
            await pilot.pause()
            row = modal.query_one(DataTable).get_row_at(0)
            self.assertEqual(
                row,
                [
                    Text(self.result.document_kind),
                    Text(self.result.title),
                    Text(self.result.product),
                ],
            )

    async def test_empty_results_keep_query_brackets_literal(self):
        self.app.search_service.search_kcs.return_value = []
        async with self.app.run_test() as pilot:
            modal = KCSSearchModal(self.app.search_service, initial_query="[/]")
            self.app.push_screen(modal)
            await pilot.pause()
            await self.app.workers.wait_for_complete()
            await pilot.pause()
            row = modal.query_one(DataTable).get_row_at(0)
            self.assertEqual(row[1], Text("No KCS solutions found for '[/]'."))

    async def test_results_and_controls_fit_standard_terminal_sizes(self):
        self.app.search_service.search_kcs.return_value = [
            KCSSearchResult(str(index), f"Solution {index}") for index in range(20)
        ]
        async with self.app.run_test() as pilot:
            for width, height in ((80, 24), (120, 40)):
                await pilot.resize_terminal(width, height)
                callback = Mock()
                modal = KCSSearchModal(self.app.search_service, initial_query="query")
                self.app.push_screen(modal, callback)
                await pilot.pause()
                await self.app.workers.wait_for_complete()
                await pilot.pause()
                table = modal.query_one(DataTable)
                self.assertGreaterEqual(table.content_region.height, 4)
                self.assertTrue(modal.region.contains_region(table.region))
                self.assertTrue(
                    modal.region.contains_region(
                        modal.query_one("#modal-buttons").region
                    )
                )
                await pilot.click("#insert-btn")
                await pilot.pause()
                callback.assert_called_once_with(
                    ("insert", "https://access.redhat.com/solutions/0", "Solution 0")
                )

    async def test_failed_search_is_distinct_from_empty_and_can_be_retried(self):
        async with self.app.run_test() as pilot:
            for error in (
                ReadError("SECRET"),
                AuthenticationError("SECRET"),
                RuntimeError("SECRET"),
            ):
                self.app.search_service.search_kcs.side_effect = error
                modal = KCSSearchModal(self.app.search_service, initial_query="query")
                self.app.push_screen(modal)
                await pilot.pause()
                await self.app.workers.wait_for_complete()
                await pilot.pause()
                message = str(modal.query_one(DataTable).get_row_at(0)[1])
                self.assertIn("failed", message.lower())
                self.assertNotIn("No KCS solutions", message)
                self.assertNotIn("SECRET", message)
                self.assertEqual(modal.results, [])
                self.app.search_service.search_kcs.side_effect = None
                modal.query_one(Input).focus()
                await pilot.press("enter")
                await self.app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(modal.results, [self.result])
                modal.dismiss(None)
                await pilot.pause()
