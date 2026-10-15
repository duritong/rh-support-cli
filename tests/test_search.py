import io
import unittest
from types import SimpleNamespace
from unittest import mock

from requests import ConnectionError

from rh_support_lib.errors import ReadError
from rh_support_lib.services.cases import CaseService
from rh_support_lib.services.search import SearchService


class SearchResponseTests(unittest.TestCase):
    def setUp(self):
        self.client = mock.Mock()
        self.service = SearchService(self.client, case_service=mock.Mock())

    def test_supported_response_roots_and_null_containers(self):
        document = {"id": "123", "title": "Kernel issue"}
        for payload, count in (
            ([document], 1),
            ({"response": {"docs": [document]}}, 1),
            ({"docs": [document]}, 1),
            ({"response": None, "docs": [document]}, 1),
            ({"response": {"docs": []}, "docs": [document]}, 0),
        ):
            with self.subTest(payload=payload):
                self.client.get.return_value.json.return_value = payload
                result = self.service.search_kcs("kernel")
                self.assertEqual(len(result), count)
                if count:
                    self.assertEqual(result[0].id, "123")

    def test_invalid_containers_and_transport_failures_are_not_empty_results(self):
        for payload in (
            None,
            {"response": None},
            {"response": {"docs": "malformed"}},
            {"docs": [None]},
        ):
            with self.subTest(payload=payload):
                self.client.get.return_value.json.return_value = payload
                with self.assertRaises(ReadError):
                    self.service.search_kcs("kernel")
        self.client.get.side_effect = ConnectionError("private request details")
        with self.assertRaisesRegex(ReadError, "Knowledgebase search is unavailable"):
            self.service.search_kcs("kernel")

    def test_nonpositive_search_limits_never_issue_requests(self):
        for limit in (0, -1):
            with self.subTest(limit=limit):
                self.assertEqual(self.service.search_kcs("kernel", limit=limit), [])
                self.assertEqual(
                    self.service.search_all("kernel", limit=limit),
                    {"cases": [], "kcs": []},
                )
                cases = object.__new__(CaseService)
                cases.search_local_cases = mock.Mock()
                cases.list_cases = mock.Mock()
                self.assertEqual(
                    cases.search_cases("kernel", limit=limit, local_only=True), []
                )
                self.assertEqual(cases.search_cases("kernel", limit=limit), [])
                cases.search_local_cases.assert_not_called()
                cases.list_cases.assert_not_called()
        self.client.get.assert_not_called()
        self.service.case_service.search_cases.assert_not_called()

    def test_cli_zero_limit_is_not_replaced_with_default(self):
        from rh_support_lib.commands.search import cmd_search

        for search_type in ("cases", "kcs", "all"):
            with (
                self.subTest(search_type=search_type),
                mock.patch("rh_support_lib.commands.search.SearchService") as service,
                mock.patch("sys.stdout", new_callable=io.StringIO),
            ):
                args = SimpleNamespace(
                    query="kernel", type=search_type, limit=0, format="json"
                )
                service.return_value.search_cases.return_value = []
                service.return_value.search_kcs.return_value = []
                service.return_value.search_all.return_value = {"cases": [], "kcs": []}
                cmd_search(args, self.client, {})
                method = getattr(
                    service.return_value,
                    {"cases": "search_cases", "kcs": "search_kcs", "all": "search_all"}[
                        search_type
                    ],
                )
                self.assertEqual(method.call_args.kwargs["limit"], 0)
