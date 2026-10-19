import unittest
from dataclasses import asdict
from unittest import mock

from requests import ConnectionError

from rh_support_lib.api import AuthenticationError
from rh_support_lib.errors import (
    IncompleteCaseHistoryError,
    PartialSyncError,
    ReadError,
)
from rh_support_lib.models import Case, CaseDetails, TrackedCase
from rh_support_lib.services.cases import CaseService
from tests.test_pagination import PaginationClient


class ReadFailureTests(unittest.TestCase):
    def setUp(self):
        self.client = PaginationClient()
        self.cache = mock.Mock(scope="pagination-test", enabled=False)
        self.state = mock.Mock()
        self.state.get_tracked_cases.return_value = {}
        self.service = CaseService(
            self.client, cache_service=self.cache, state_service=self.state
        )
        self.service.get_current_user = mock.Mock(return_value="test-user")

    def test_malformed_initial_envelopes_are_not_missing_cases(self):
        for data in (
            None,
            [],
            {},
            {"redhat_support_uiapi": None},
            PaginationClient.response("RedHatSupportCase", {}),
            PaginationClient.response("RedHatSupportCase", {"edges": None}),
            PaginationClient.response("RedHatSupportCase", {"edges": [{}]}),
            PaginationClient.response("RedHatSupportCase", {"edges": [{"node": {}}]}),
        ):
            with self.subTest(data=data):
                self.client.execute_graphql = mock.Mock(return_value=data)
                with self.assertRaises(ReadError):
                    self.service.get_case_details("123")
        self.cache._set_case_details.assert_not_called()

    def test_valid_empty_case_connection_is_not_found(self):
        self.client.execute_graphql = mock.Mock(
            return_value=PaginationClient.response("RedHatSupportCase", {"edges": []})
        )
        self.assertIsNone(self.service.get_case_details("123"))

    def test_status_lookup_does_not_use_stale_cache_after_failed_probe(self):
        self.cache.get_case_record.return_value = {
            "cached_at": 0,
            "case": {"status": "Waiting on Customer"},
        }
        for status in ("Closed", "Waiting on Engineering"):
            with (
                self.subTest(status=status),
                mock.patch.object(
                    self.service, "check_case_last_modified", return_value=None
                ),
                mock.patch.object(
                    self.service,
                    "get_case_details",
                    return_value=CaseDetails(
                        case=Case(
                            case_number="123",
                            summary="Case",
                            status=status,
                            severity="Normal",
                        ),
                        description="",
                    ),
                ) as details,
            ):
                self.assertEqual(self.service.get_case_status("123"), status)
                details.assert_called_once_with(
                    "123", first_comments=1, all_comments=False, use_cache=False
                )
        self.cache.get_case_record.assert_not_called()

    def test_reregistering_new_metadata_does_not_acknowledge_unseen_updates(self):
        import os
        import tempfile

        from rh_support_lib.services.state import StateService

        with tempfile.TemporaryDirectory() as directory:
            state = StateService(state_file=os.path.join(directory, "state.yaml"))
            state.add_tracked_case(
                "user",
                "123",
                status="In Progress",
                last_comment_id="old",
                last_comment_date="2026-01-01T00:00:00Z",
            )
            checkpoint = state.get_tracked_cases("user")["123"].read_checkpoint
            state.register_owned_case(
                "user",
                "123",
                status="Closed",
                severity="1",
                last_comment_id="unseen",
                last_comment_date="2026-02-01T00:00:00Z",
            )
            self.assertEqual(
                state.get_tracked_cases("user")["123"].read_checkpoint, checkpoint
            )

    def test_failed_refresh_preserves_existing_good_cache(self):
        self.cache.enabled = True
        record = {"good": "existing record"}
        self.cache.get_case_record.return_value = record
        self.cache._get_case_details_from_record.return_value = None
        self.client.fail_after = "2"
        with self.assertRaises(IncompleteCaseHistoryError):
            self.service.get_case_details("123", first_comments=2)
        self.cache._set_case_details.assert_not_called()
        self.assertEqual(record, {"good": "existing record"})

    def test_malformed_direct_page_fallback_is_transactional(self):
        execute = self.client.execute_graphql

        def malformed(query, variables, **kwargs):
            data = execute(query, variables, **kwargs)
            if "GetCaseComments" in query:
                connection = data["redhat_support_uiapi"]["query"][
                    "RedHatSupportCaseComment__c"
                ]
                connection["edges"].append({"node": None})
            return data

        self.client.execute_graphql = malformed
        details = self.service.get_case_details("123", first_comments=2)
        self.assertEqual([c.id for c in details.comments], [str(i) for i in range(7)])

    def test_disappearing_fallback_case_fails_history(self):
        execute = self.client.execute_graphql

        def disappear(query, variables, **kwargs):
            if variables.get("after"):
                if "GetCaseComments" in query:
                    raise ConnectionError("https://private/token")
                return PaginationClient.response("RedHatSupportCase", {"edges": []})
            return execute(query, variables, **kwargs)

        self.client.execute_graphql = disappear
        with self.assertRaises(IncompleteCaseHistoryError) as error:
            self.service.get_case_details("123", first_comments=2)
        self.assertNotIn("private", str(error.exception))

    def test_malformed_required_pages_fail_without_cache_writes(self):
        execute = self.client.execute_graphql

        def malformed(query, variables, **kwargs):
            data = execute(query, variables, **kwargs)
            if variables.get("after"):
                if "GetCaseComments" in query:
                    return PaginationClient.response(
                        "RedHatSupportCaseComment__c", {"edges": [None]}
                    )
                node = data["redhat_support_uiapi"]["query"]["RedHatSupportCase"][
                    "edges"
                ][0]["node"]
                node["CaseComments__r"]["edges"].append({"node": None})
            return data

        self.client.execute_graphql = malformed
        with self.assertRaises(IncompleteCaseHistoryError):
            self.service.get_case_details("123", first_comments=2)
        self.cache._set_case_details.assert_not_called()

    def test_finite_prefix_is_valid_with_unknown_completion(self):
        self.client.omit_page_info = True
        details = self.service.get_case_details("123", first_comments=2, max_comments=2)
        self.assertEqual(len(details.comments), 2)

    def test_initial_transport_failure_is_safe(self):
        self.client.execute_graphql = mock.Mock(
            side_effect=ConnectionError("https://private/token")
        )
        with self.assertRaises(ReadError) as raised:
            self.service.get_case_details("123")
        self.assertNotIn("private", str(raised.exception))

    def test_authentication_is_never_wrapped_or_fallback(self):
        execute = self.client.execute_graphql
        error = AuthenticationError("Authentication required.")

        def fail(query, variables, **kwargs):
            if "GetCaseComments" in query:
                raise error
            return execute(query, variables, **kwargs)

        self.client.execute_graphql = mock.Mock(side_effect=fail)
        with self.assertRaises(AuthenticationError) as raised:
            self.service.get_case_details("123", first_comments=2)
        self.assertIs(raised.exception, error)
        self.assertEqual(self.client.execute_graphql.call_count, 2)

    def test_remote_search_failure_is_not_local_success(self):
        self.service.list_cases = mock.Mock(side_effect=ReadError("Unavailable."))
        self.service.search_local_cases = mock.Mock(return_value=[])
        with self.assertRaises(ReadError):
            self.service.search_cases("query")
        self.service.search_local_cases.assert_not_called()

    def test_list_and_product_pages_require_explicit_completion(self):
        self.client.omit_page_info = True
        for method in (self.service.list_cases, self.service.get_active_products):
            with self.subTest(method=method.__name__), self.assertRaises(ReadError):
                method(all_pages=True, use_cache=False)
        self.cache.set_case_list.assert_not_called()
        self.cache.set_products.assert_not_called()

    def test_malformed_list_and_product_envelopes_fail(self):
        self.client.execute_graphql = mock.Mock(return_value={})
        for method in (self.service.list_cases, self.service.get_active_products):
            with self.subTest(method=method.__name__), self.assertRaises(ReadError):
                method(use_cache=False)

    def test_live_followed_failures_do_not_fabricate_cases(self):
        self.state.get_followed_cases.return_value = {"123": TrackedCase("123")}
        for failure in (
            ReadError("Unavailable."),
            ConnectionError("private URL"),
            None,
        ):
            with self.subTest(failure=failure):
                self.service.get_case_details = mock.Mock(
                    side_effect=failure if isinstance(failure, Exception) else None,
                    return_value=None,
                )
                with self.assertRaises(ReadError):
                    self.service.list_followed_cases()
                self.assertEqual(
                    len(self.service.list_followed_cases(fetch_live=False)), 1
                )
        self.state.batch_update_latest_known.assert_not_called()

    def test_partial_sync_persists_only_successes_and_raises_report(self):
        tracked = {number: TrackedCase(number) for number in ("123", "456", "789")}
        self.state.get_tracked_cases.return_value = tracked
        before = {number: asdict(case) for number, case in tracked.items()}
        details = CaseDetails(
            case=Case(
                case_number="123", summary="Case", status="Open", severity="Normal"
            ),
            description="",
        )

        def fetch(number, **kwargs):
            if number == "456":
                raise IncompleteCaseHistoryError("Unavailable.")
            return details if number == "123" else None

        self.service.get_case_details = mock.Mock(side_effect=fetch)
        with self.assertRaises(PartialSyncError) as raised:
            self.service.sync_tracked_cases()
        self.assertEqual(raised.exception.report["synced_count"], 1)
        self.assertEqual(raised.exception.failures, ("456", "789"))
        updates = self.state.batch_update_latest_known.call_args.kwargs["case_updates"]
        self.assertEqual([update["case_number"] for update in updates], ["123"])
        self.assertEqual(
            {number: asdict(case) for number, case in tracked.items()}, before
        )

    def test_authentication_propagates_from_probe_live_list_and_sync(self):
        error = AuthenticationError("Authentication required.")
        self.client.execute_graphql = mock.Mock(side_effect=error)
        with self.assertRaises(AuthenticationError):
            self.service.check_case_last_modified("123")
        tracked = {"123": TrackedCase("123")}
        self.state.get_followed_cases.return_value = tracked
        self.state.get_tracked_cases.return_value = tracked
        self.service.get_case_details = mock.Mock(side_effect=error)
        for method in (
            self.service.list_followed_cases,
            self.service.sync_tracked_cases,
        ):
            with (
                self.subTest(method=method.__name__),
                self.assertRaises(AuthenticationError),
            ):
                method()

    def test_unresolved_internal_id_is_not_invented(self):
        self.cache.get_case_record.return_value = None
        self.service.check_case_last_modified = mock.Mock(return_value=None)
        self.service.get_case_details = mock.Mock(return_value=None)
        with self.assertRaises(ReadError):
            self.service.get_case_internal_id("123")


class ReadPresentationTests(unittest.TestCase):
    def test_show_distinguishes_missing_case_from_unavailable_reads(self):
        import io
        from types import SimpleNamespace

        from rh_support_lib.commands.show_case import cmd_show

        for failure in (
            None,
            IncompleteCaseHistoryError("History incomplete"),
            ConnectionError("private transport details"),
        ):
            with self.subTest(failure=failure):
                with (
                    mock.patch(
                        "rh_support_lib.commands.show_case.CaseService"
                    ) as service,
                    mock.patch("sys.stdout", new_callable=io.StringIO) as output,
                    self.assertRaises(SystemExit) as caught,
                ):
                    if failure is None:
                        service.return_value.get_case_details.return_value = None
                    else:
                        service.return_value.get_case_details.side_effect = failure
                    cmd_show(SimpleNamespace(case="123", format="json"), mock.Mock())
                self.assertEqual(output.getvalue(), "")
                if failure is None:
                    self.assertIn("not found", str(caught.exception))
                else:
                    self.assertNotIn("not found", str(caught.exception))
                    self.assertNotIn("private transport details", str(caught.exception))

    def test_unavailable_attachments_do_not_return_an_empty_successful_case_view(self):
        import io
        from types import SimpleNamespace

        from rh_support_lib.commands.show_case import cmd_show

        with (
            mock.patch("rh_support_lib.commands.show_case.CaseService") as service,
            mock.patch(
                "rh_support_lib.services.attachments.AttachmentService"
            ) as attachments,
            mock.patch("sys.stdout", new_callable=io.StringIO) as output,
            self.assertRaisesRegex(SystemExit, "Attachments.*unavailable"),
        ):
            service.return_value.get_case_details.return_value = CaseDetails(
                Case("123", "Case", "Open", "Low"), "description"
            )
            attachments.return_value.list_attachments.side_effect = ReadError(
                "unavailable"
            )
            cmd_show(SimpleNamespace(case="123", format="json"), mock.Mock())
        self.assertEqual(output.getvalue(), "")
        service.return_value.state_service.mark_case_read.assert_not_called()

    def test_attachment_list_invalid_and_unavailable_are_not_empty(self):
        from rh_support_lib.services.attachments import AttachmentService

        client = mock.Mock()
        service = AttachmentService(client)
        for payload in (None, {}, [None], [{}]):
            with self.subTest(payload=payload):
                client.get.return_value.json.return_value = payload
                with self.assertRaises(ReadError):
                    service.list_attachments("123")
        client.get.return_value.json.return_value = []
        self.assertEqual(service.list_attachments("123"), [])
        client.get.side_effect = ConnectionError("private transport details")
        with self.assertRaisesRegex(ReadError, "Attachment listing is unavailable"):
            service.list_attachments("123")

    def test_partial_sync_cli_reports_preserved_successes_with_nonzero_status(self):
        import io
        from types import SimpleNamespace

        from rh_support_lib.commands.tracking import cmd_sync

        with (
            mock.patch("rh_support_lib.commands.tracking.CaseService") as service,
            mock.patch("sys.stdout", new_callable=io.StringIO) as output,
            self.assertRaisesRegex(SystemExit, "Successful updates were preserved"),
        ):
            service.return_value.sync_tracked_cases.side_effect = PartialSyncError(
                {"synced_count": 1}, ["failed"]
            )
            cmd_sync(SimpleNamespace(format="json", user="user"), mock.Mock())
        self.assertEqual(output.getvalue(), "")
