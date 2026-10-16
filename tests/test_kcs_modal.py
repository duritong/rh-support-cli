"""Mounted regressions for knowledgebase search and case/draft ownership."""

import unittest
from unittest.mock import Mock, patch

from textual.widgets import DataTable

from rh_support_lib.models import KCSSearchResult
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
