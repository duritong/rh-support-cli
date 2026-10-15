import os
import tempfile
import unittest
from dataclasses import asdict
from unittest import mock

from rh_support_lib.errors import IncompleteCaseHistoryError, ReadError
from rh_support_lib.services.cache import CacheService
from rh_support_lib.services.cases import CaseService
from rh_support_lib.services.state import StateService


class PaginationClient:
    """Offline, ascending connection with controllable page and cursor failures."""

    def __init__(self, count=7):
        self.count = count
        self.calls = []
        self.fail_after = None
        self.repeat_cursor = False
        self.oversend = False
        self.fail_direct = False
        self.omit_page_info = False

    def get_cache_scope(self, url):
        return "pagination-test"

    def execute_graphql(self, query, variables, **kwargs):
        self.calls.append((query, dict(variables)))
        if "CheckCaseLastModified" in query:
            node = self.case_node()
            node["CaseComments__r"] = {
                "edges": [{"node": self.comment(self.count - 1)}] if self.count else []
            }
            return self.response("RedHatSupportCase", {"edges": [{"node": node}]})
        after = variables.get("after")
        if after is not None and after == self.fail_after:
            raise RuntimeError("page unavailable")
        if self.fail_direct and "GetCaseComments" in query:
            raise RuntimeError("direct comment query unavailable")
        first = variables["first"]
        if first < 0:
            raise AssertionError("negative batch")
        start = int(after) if after else 0
        stop = min(self.count, start + (4 if self.oversend else first))
        connection = {
            "edges": [{"node": self.comment(i)} for i in range(start, stop)],
            "pageInfo": {
                "hasNextPage": stop < self.count,
                "endCursor": (after or "0") if self.repeat_cursor else str(stop),
            },
        }
        if self.omit_page_info:
            connection.pop("pageInfo")
        # Bound broken implementations so cursor-loop tests cannot hang.
        if len(self.calls) > 15:
            raise AssertionError("pagination did not terminate")
        if "GetCaseAndComments" in query:
            node = self.case_node()
            node["CaseComments__r"] = connection
            return self.response("RedHatSupportCase", {"edges": [{"node": node}]})
        if "GetCaseComments" in query:
            return self.response("RedHatSupportCaseComment__c", connection)
        if "GetActiveProducts" in query:
            return self.response("RedHatSupportProduct2", connection)
        return self.response("RedHatSupportCase", connection)

    @staticmethod
    def response(field, connection):
        return {"redhat_support_uiapi": {"query": {field: connection}}}

    @staticmethod
    def case_node():
        return {
            "Id": "case-id",
            "CaseNumber__c": {"value": "123"},
            "LastModifiedDate": {"value": "unchanged"},
            "Status": {"value": "Open"},
        }

    @staticmethod
    def comment(i):
        return {
            "Id": str(i),
            "Body__c": {"value": f"comment {i}"},
            "CreatedDate": {"value": f"2026-01-01T00:00:{i:02d}Z"},
        }


class PaginationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.environment = mock.patch.dict(os.environ, {"RH_NO_CACHE": "0"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = PaginationClient()
        self.cache = CacheService(cache_dir=directory.name, scope="pagination-test")
        self.service = CaseService(
            self.client,
            state_service=StateService(
                state_file=os.path.join(directory.name, "state")
            ),
            cache_service=self.cache,
        )
        self.service.get_current_user = mock.Mock(return_value="test-user")

    def fetch(self, **kwargs):
        return self.service.get_case_details("123", **kwargs)

    def test_first_batch_respects_cap(self):
        details = self.fetch(first_comments=5, all_comments=False, max_comments=2)
        self.assertEqual(len(details.comments), 2)
        self.assertEqual(self.client.calls[0][1]["first"], 2)

    def test_all_comments_with_cap_never_requests_negative_batch(self):
        details = self.fetch(first_comments=5, all_comments=True, max_comments=2)
        self.assertEqual(len(details.comments), 2)
        self.assertEqual(len(self.client.calls), 1)

    def test_later_batch_respects_cap_even_with_all_comments(self):
        details = self.fetch(first_comments=2, max_comments=5)
        self.assertEqual(len(details.comments), 5)
        self.assertEqual([v["first"] for _, v in self.client.calls], [2, 2, 1])

    def test_max_comments_extends_single_batch_mode(self):
        details = self.fetch(first_comments=2, all_comments=False, max_comments=5)
        self.assertEqual(len(details.comments), 5)

    def test_fallback_pagination_obeys_cap(self):
        self.client.fail_direct = True
        details = self.fetch(first_comments=2, max_comments=5)
        self.assertEqual(len(details.comments), 5)
        self.assertEqual([v["first"] for _, v in self.client.calls], [2, 2, 2, 1, 1])
        self.assertFalse(
            self.cache.get_case_record("123")["_comment_coverage"]["complete"]
        )

    def test_cap_at_terminal_page_records_complete_coverage(self):
        self.fetch(first_comments=2, max_comments=7)
        self.assertEqual(
            self.cache.get_case_record("123")["_comment_coverage"],
            {"complete": True, "available_prefix": 7},
        )

    def test_missing_page_info_is_not_complete(self):
        self.client.omit_page_info = True
        with self.assertRaises(IncompleteCaseHistoryError):
            self.fetch(first_comments=2)
        self.assertIsNone(self.cache.get_case_record("123"))
        self.fetch(first_comments=2, all_comments=False)
        self.assertEqual(
            self.cache.get_case_record("123")["_comment_coverage"],
            {"complete": False, "available_prefix": 2},
        )

    def test_invalid_limits_fail_before_network(self):
        for options in (
            {"first_comments": 0},
            {"first_comments": -1},
            {"max_comments": -1},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.fetch(**options)
        self.assertEqual(self.client.calls, [])

    def test_zero_cap_fetches_case_without_comments(self):
        details = self.fetch(max_comments=0)
        self.assertEqual(details.comments, [])
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.calls[0][1]["first"], 0)

    def test_oversized_server_pages_are_sliced(self):
        self.client.oversend = True
        details = self.fetch(first_comments=2, all_comments=False)
        self.assertEqual(len(details.comments), 2)
        self.assertFalse(
            self.cache.get_case_record("123")["_comment_coverage"]["complete"]
        )

    def test_full_cache_hit_obeys_options_without_mutating_record(self):
        full = self.fetch(first_comments=2)
        record = self.cache.get_case_record("123")
        for options, expected in (
            ({"first_comments": 2, "all_comments": False}, 2),
            ({"first_comments": 2, "max_comments": 4}, 4),
            ({"max_comments": 0}, 0),
            ({}, 7),
        ):
            with self.subTest(options=options):
                self.client.calls.clear()
                self.assertEqual(len(self.fetch(**options).comments), expected)
                self.assertEqual(len(self.client.calls), 1)
                self.assertIn("CheckCaseLastModified", self.client.calls[0][0])
                self.assertEqual(self.cache.get_case_record("123"), record)
        self.assertEqual(len(full.comments), 7)
        self.assertNotIn("_comment_coverage", asdict(full))

    def test_instant_tui_cache_requires_complete_thread_coverage(self):
        self.fetch(first_comments=2, all_comments=False)
        self.assertIsNone(
            self.cache.get_case_details("123", ttl=0, require_complete=True)
        )
        self.fetch()
        cached = self.cache.get_case_details("123", ttl=0, require_complete=True)
        self.assertEqual(len(cached[0].comments), 7)

    def test_case_list_caps_oversized_pages_and_zero_skips_requests(self):
        self.assertEqual(self.service.list_cases(limit=0), [])
        self.assertEqual(self.client.calls, [])
        self.client.oversend = True
        self.assertEqual(
            len(self.service.list_cases(first=1, limit=1, use_cache=False)), 1
        )
        self.assertEqual(len(self.service.list_cases(first=1, use_cache=False)), 1)

    def test_latest_editor_context_is_selected_from_the_full_thread(self):
        import io
        from types import SimpleNamespace

        from rh_support_lib.commands.actions import cmd_comment

        args = SimpleNamespace(
            case="123",
            template=None,
            file=None,
            edit=False,
            status=None,
            include_previous_comments=2,
        )
        with (
            mock.patch(
                "rh_support_lib.commands.actions.CaseService", return_value=self.service
            ),
            mock.patch(
                "rh_support_lib.commands.actions.open_editor",
                return_value=("new comment", None),
            ) as editor,
            mock.patch.object(self.service, "add_comment"),
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            cmd_comment(args, mock.Mock())
        context = editor.call_args.kwargs["header_content"]
        self.assertIn("comment 5", context)
        self.assertIn("comment 6", context)
        self.assertNotIn("comment 0", context)

    def test_corrupt_cache_timestamp_is_a_miss(self):
        self.fetch()
        record = self.cache.get_case_record("123")
        record["cached_at"] = "not-a-timestamp"
        self.cache._write_json_atomic(self.cache._case_path("123"), record)
        self.client.calls.clear()
        self.assertEqual(len(self.fetch().comments), 7)
        self.assertTrue(
            any("GetCaseAndComments" in query for query, _ in self.client.calls)
        )

    def test_cache_slicing_does_not_mutate_shared_details(self):
        full = self.fetch()
        with mock.patch.object(
            self.cache,
            "_get_case_details_from_record",
            return_value=(full, [], "unchanged"),
        ):
            limited = self.fetch(first_comments=2, all_comments=False)
        self.assertEqual(len(limited.comments), 2)
        self.assertEqual(len(full.comments), 7)
        self.assertIsNot(limited, full)

    def test_invalid_coverage_is_not_reused(self):
        self.fetch()
        record = self.cache.get_case_record("123")
        for coverage in ("invalid", {}, {"complete": True, "available_prefix": 99}):
            with self.subTest(coverage=coverage):
                record["_comment_coverage"] = coverage
                self.cache._write_json_atomic(self.cache._case_path("123"), record)
                self.client.calls.clear()
                self.fetch()
                self.assertTrue(
                    any("GetCaseAndComments" in q for q, _ in self.client.calls)
                )

    def test_partial_cache_is_not_trusted_as_full_even_with_matching_probe(self):
        self.fetch(first_comments=2, all_comments=False)
        self.service.check_case_last_modified = mock.Mock(
            return_value=("case-id", "unchanged", "Open", "", "")
        )
        self.client.calls.clear()
        self.assertEqual(len(self.fetch(first_comments=2).comments), 7)
        self.assertTrue(any("GetCaseAndComments" in q for q, _ in self.client.calls))

    def test_prefix_cache_can_satisfy_smaller_request(self):
        self.fetch(first_comments=4, all_comments=False)
        self.service.check_case_last_modified = mock.Mock(
            return_value=("case-id", "unchanged", "Open", "", "")
        )
        self.client.calls.clear()
        self.assertEqual(
            len(self.fetch(first_comments=2, all_comments=False).comments), 2
        )
        self.assertEqual(self.client.calls, [])

    def test_legacy_cache_without_coverage_is_refetched(self):
        details = self.fetch(first_comments=2, all_comments=False)
        self.cache.set_case_details("123", details)
        self.service.check_case_last_modified = mock.Mock(
            return_value=("case-id", "unchanged", "Open", "", "")
        )
        self.client.calls.clear()
        self.assertEqual(len(self.fetch().comments), 7)
        self.assertTrue(any("GetCaseAndComments" in q for q, _ in self.client.calls))

    def test_failed_middle_page_raises_without_caching_partial_history(self):
        self.client.fail_after = "2"
        with self.assertRaises(IncompleteCaseHistoryError):
            self.fetch(first_comments=2)
        self.assertIsNone(self.cache.get_case_record("123"))
        self.client.fail_after = None
        self.service.check_case_last_modified = mock.Mock(
            return_value=("case-id", "unchanged", "Open", "", "")
        )
        self.assertEqual(len(self.fetch(first_comments=2).comments), 7)

    def test_complete_coverage_includes_empty_thread(self):
        self.client.count = 0
        self.fetch()
        self.assertEqual(
            self.cache.get_case_record("123")["_comment_coverage"],
            {"complete": True, "available_prefix": 0},
        )

    def test_nonadvancing_comment_cursor_raises(self):
        self.client.repeat_cursor = True
        with self.assertRaises(IncompleteCaseHistoryError):
            self.fetch(first_comments=1)
        self.assertIsNone(self.cache.get_case_record("123"))

    def test_case_and_product_cursor_loops_raise(self):
        self.client.repeat_cursor = True
        for method in (self.service.list_cases, self.service.get_active_products):
            with self.subTest(method=method.__name__):
                self.client.calls.clear()
                with self.assertRaisesRegex(ReadError, "cursor"):
                    method(first=1, all_pages=True, use_cache=False)

    def test_multistep_comment_cursor_cycle_raises(self):
        execute = self.client.execute_graphql
        cursors = iter(("1", "2", "1"))

        def cycle(query, variables, **kwargs):
            data = execute(query, variables, **kwargs)
            container = data["redhat_support_uiapi"]["query"]
            connection = (
                container["RedHatSupportCase"]["edges"][0]["node"]["CaseComments__r"]
                if "GetCaseAndComments" in query
                else container["RedHatSupportCaseComment__c"]
            )
            connection["pageInfo"]["endCursor"] = next(cursors, "1")
            return data

        self.client.execute_graphql = cycle
        with self.assertRaises(IncompleteCaseHistoryError):
            self.fetch(first_comments=1)


if __name__ == "__main__":
    unittest.main()
