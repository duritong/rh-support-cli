import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from rh_support_lib.api import LegacyAPIClient
from rh_support_lib.models import Case, CaseDetails, TrackedCase
from rh_support_lib.services.cases import CaseService


class FilterContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = mock.patch.dict(
            os.environ, {"XDG_CACHE_HOME": self.directory.name, "RH_NO_CACHE": "0"}
        )
        environment.start()
        self.addCleanup(environment.stop)
        state = mock.Mock()
        state.get_tracked_cases.return_value = {}
        self.service = CaseService(
            LegacyAPIClient("test-token"),
            graphql_url="https://example.test/graphql",
            state_service=state,
        )
        self.a = Case(
            "1",
            "diagnostic",
            "In Progress",
            "2 (High)",
            product="OCP",
            owner="team-a",
            account_number="7",
        )
        self.b = Case(
            "2",
            "diagnostic",
            "Closed",
            "Low",
            product="RHEL",
            owner="team-b",
            account_number="8",
        )

    def test_list_cache_distinguishes_product_and_batch_size(self):
        self.service._list_cases_graphql = mock.Mock(return_value=[self.a])
        for product, first in (("OCP", 1), ("RHEL", 1), ("OCP", 2)):
            self.service.list_cases(product=product, first=first)
        self.assertEqual(self.service._list_cases_graphql.call_count, 3)
        self.service.list_cases(product="OCP", first=2)
        self.assertEqual(self.service._list_cases_graphql.call_count, 3)

    def test_local_and_merged_search_honor_owner(self):
        for case in (self.a, self.b):
            self.service.cache_service.set_case_details(
                case.case_number, CaseDetails(case, "diagnostic")
            )
        self.assertEqual(
            [
                case.case_number
                for case in self.service.search_cases(
                    "diagnostic", owner="team-a", local_only=True
                )
            ],
            ["1"],
        )
        self.service.list_cases = mock.Mock(return_value=[])
        self.assertEqual(
            [
                case.case_number
                for case in self.service.search_cases("diagnostic", owner="team-a")
            ],
            ["1"],
        )

    def test_followed_results_honor_combined_filters(self):
        self.service.state_service.get_followed_cases.return_value = {
            case.case_number: TrackedCase(case.case_number) for case in (self.a, self.b)
        }
        self.service.get_case_details = mock.Mock(
            side_effect=lambda number, **kwargs: CaseDetails(
                self.a if number == "1" else self.b, ""
            )
        )
        result = self.service.list_followed_cases(
            account="7",
            status=["In Progress"],
            severity=["2"],
            owner="team-a",
            product="OCP",
        )
        self.assertEqual([case.case_number for case in result], ["1"])

    def test_followed_search_applies_query_and_filters_before_limit(self):
        from rh_support_lib.services.search import SearchService

        unrelated = [
            Case(
                str(100 + index),
                "Unrelated",
                "In Progress",
                "2 (High)",
                product="OCP",
                owner="team-a",
                account_number="7",
            )
            for index in range(60)
        ]
        cases = {case.case_number: case for case in unrelated + [self.b, self.a]}
        self.service.state_service.get_followed_cases.return_value = {
            number: TrackedCase(number) for number in cases
        }
        self.service.get_case_details = mock.Mock(
            side_effect=lambda number, **kwargs: CaseDetails(cases[number], "")
        )
        self.service.list_cases = mock.Mock(
            side_effect=AssertionError("Must not search general queue")
        )
        search = SearchService(mock.Mock(), case_service=self.service)
        result = search.search_cases(
            "diagnostic",
            following=True,
            limit=1,
            account="7",
            status=["In Progress"],
            severity=["2"],
            product="OCP",
            owner="team-a",
        )
        self.assertEqual([case.case_number for case in result], ["1"])
        self.service.list_cases.assert_not_called()

    def test_local_followed_search_uses_only_local_identity_and_tracking(self):
        from rh_support_lib.services.search import SearchService

        self.service.get_current_user = mock.Mock(return_value="offline")
        self.service.state_service.get_followed_cases.return_value = {
            "1": TrackedCase("1")
        }
        self.service.get_case_details = mock.Mock(
            side_effect=AssertionError("Live read")
        )
        search = SearchService(mock.Mock(), case_service=self.service)
        result = search.search_cases("1", following=True, local_only=True)
        self.assertEqual([case.case_number for case in result], ["1"])
        self.service.get_current_user.assert_called_once_with(local_only=True)
        self.service.get_case_details.assert_not_called()

    def test_cli_list_forwards_bookmark_product_and_following_filters(self):
        from rh_support_lib.commands.list_cases import cmd_list

        config = {
            "default_bookmark": "team",
            "bookmarks": {
                "team": {
                    "product": "OCP",
                    "owner": "team-a",
                    "status": "in_progress",
                    "severity": "2",
                    "account": "7",
                }
            },
        }
        for following in (False, True):
            with self.subTest(following=following):
                args = SimpleNamespace(format="json", following=following)
                with (
                    mock.patch(
                        "rh_support_lib.commands.list_cases.CaseService"
                    ) as service,
                    mock.patch("sys.stdout", new_callable=io.StringIO),
                ):
                    service.return_value.list_cases.return_value = []
                    service.return_value.list_followed_cases.return_value = []
                    cmd_list(args, mock.Mock(), config)
                method = (
                    service.return_value.list_followed_cases
                    if following
                    else service.return_value.list_cases
                )
                filters = method.call_args.kwargs
                self.assertEqual(filters["product"], "OCP")
                self.assertEqual(filters["owner"], "team-a")
                self.assertEqual(filters["status"], ["In Progress"])
                self.assertEqual(filters["severity"], ["2 (High)"])

    def test_combined_search_preserves_case_filters(self):
        from rh_support_lib.services.search import SearchService

        service = SearchService(mock.Mock(), case_service=self.service)
        with (
            mock.patch.object(service, "search_cases", return_value=[]) as cases,
            mock.patch.object(service, "search_kcs", return_value=[]),
        ):
            service.search_all(
                "diagnostic",
                account="7",
                owner="team-a",
                status=["Closed"],
                severity=["2"],
            )
        self.assertEqual(cases.call_args.kwargs["account"], "7")
        self.assertEqual(cases.call_args.kwargs["owner"], "team-a")
        self.assertEqual(cases.call_args.kwargs["status"], ["Closed"])
        self.assertEqual(cases.call_args.kwargs["severity"], ["2"])

    def test_cli_combined_search_forwards_filters(self):
        from rh_support_lib.commands.search import cmd_search

        args = SimpleNamespace(
            query="diagnostic",
            type="all",
            account="7",
            owner="team-a",
            status=["in_progress"],
            severity=["2"],
            format="json",
        )
        with (
            mock.patch("rh_support_lib.commands.search.SearchService") as service,
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            service.return_value.search_all.return_value = {"cases": [], "kcs": []}
            cmd_search(args, mock.Mock(), {})
        filters = service.return_value.search_all.call_args.kwargs
        self.assertEqual(filters["account"], "7")
        self.assertEqual(filters["owner"], "team-a")
        self.assertEqual(filters["status"], ["In Progress"])
        self.assertEqual(filters["severity"], ["2 (High)"])

    def test_tui_preserves_product_in_bookmark_parameters(self):
        from rh_support_lib.tui.app import build_filter_params

        config = {
            "default_bookmark": "team",
            "bookmarks": {
                "team": {"product": "OCP", "following": True, "owner": "team-a"}
            },
        }
        filters = build_filter_params(config)
        self.assertEqual(filters["product"], "OCP")
        self.assertEqual(filters["owner"], "team-a")
        self.assertTrue(filters["following"])

    def test_tui_followed_fetch_and_poll_forward_filters(self):
        import contextlib

        from rh_support_lib.tui.app import SupportApp

        config = {
            "default_bookmark": "team",
            "bookmarks": {
                "team": {"product": "OCP", "following": True, "owner": "team-a"}
            },
        }
        with (
            mock.patch("rh_support_lib.tui.app.CaseService"),
            mock.patch("rh_support_lib.tui.app.AttachmentService"),
        ):
            app = SupportApp("test-token", config=config, layout="horizontal")
        app.case_service.list_followed_cases.return_value = []
        app.track_job = lambda label: contextlib.nullcontext()
        app._safe_call = lambda fn, *args: fn(*args)
        app.call_from_thread = app._safe_call
        app.populate_cases_table = mock.Mock()
        app.update_table_rows_preserve_cursor = mock.Mock()
        app.fetch_cases()
        app.poll_cases_background()
        self.assertEqual(app.case_service.list_followed_cases.call_count, 2)
        for call in app.case_service.list_followed_cases.call_args_list:
            self.assertEqual(call.kwargs["product"], "OCP")
            self.assertEqual(call.kwargs["owner"], "team-a")
