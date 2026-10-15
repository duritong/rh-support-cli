"""Offline regressions for comment mutation outcomes and safe recovery."""

import unittest
from unittest.mock import Mock

from requests import Timeout

from rh_support_lib.errors import CommentOutcomeUnknownError, PartialCommentError
from rh_support_lib.models import CaseComment
from rh_support_lib.services.cases import (
    GQL_CREATE_CASE_COMMENT,
    GQL_UPDATE_CASE,
    CaseService,
)


class CommentOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.get_cache_scope.return_value = "offline-scope"
        self.cache = Mock(scope="offline-scope")
        self.service = CaseService(
            self.client, cache_service=self.cache, state_service=Mock()
        )
        self.posted = {
            "redhat_support_uiapi": {
                "CaseComment__cCreate": {
                    "RedHatSupportRecord": {
                        "Id": "comment-id",
                        "CreatedDate": {"value": "2026-10-15"},
                    }
                }
            }
        }
        self.updated = {
            "redhat_support_uiapi": {
                "CaseUpdate": {"RedHatSupportRecord": {"Id": "case-id"}}
            }
        }

    def post(self, status=None):
        return self.service.add_comment(
            "12345",
            "Private body",
            is_public=False,
            status=status,
            case_internal_id="case-id",
        )

    def assert_comment(self, comment):
        self.assertIsInstance(comment, CaseComment)
        self.assertEqual(comment.id, "comment-id")
        self.assertEqual(comment.body, "Private body")
        self.assertEqual(comment.created_date, "2026-10-15")
        self.assertFalse(comment.is_public)
        self.assertTrue(comment.is_customer)

    def test_comment_only_returns_comment_and_invalidates(self):
        self.client.execute_graphql.return_value = self.posted
        self.assert_comment(self.post())
        self.client.execute_graphql.assert_called_once()
        self.cache.invalidate_case.assert_called_once_with("12345")

    def test_comment_and_status_success_do_not_retry_mutations(self):
        self.client.execute_graphql.side_effect = [self.posted, self.updated]
        self.assert_comment(self.post("closed"))
        calls = self.client.execute_graphql.call_args_list
        self.assertEqual(
            [call.args[0] for call in calls],
            [
                GQL_CREATE_CASE_COMMENT,
                GQL_UPDATE_CASE,
            ],
        )
        self.assertEqual(
            calls[1].args[1]["input"]["RedHatSupportCase"]["Status"], "Closed"
        )
        self.cache.invalidate_case.assert_called_with("12345")

    def test_status_failures_preserve_confirmed_comment_and_invalidate(self):
        for failure in (
            RuntimeError("secret payload"),
            Timeout("secret URL"),
            KeyboardInterrupt(),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.client.reset_mock()
                self.cache.reset_mock()
                self.client.execute_graphql.side_effect = [self.posted, failure]
                with self.assertRaises(PartialCommentError) as caught:
                    self.post("closed")
                error = caught.exception
                self.assertEqual(error.case_number, "12345")
                self.assertEqual(error.requested_status, "closed")
                self.assert_comment(error.comment)
                self.assertFalse(error.status_confirmed)
                self.assertFalse(error.cache_invalidation_failed)
                self.assertEqual(
                    str(error),
                    "Comment posted, but the requested status change was not confirmed. "
                    "Do not repost the comment. Inspect the case in the portal, then change status only.",
                )
                self.assertIsNone(error.__cause__)
                self.cache.invalidate_case.assert_called_once_with("12345")
                self.assertEqual(self.client.execute_graphql.call_count, 2)

    def test_cache_failure_is_typed_not_an_unsent_failure(self):
        for status in (None, "closed"):
            with self.subTest(status=status):
                self.client.reset_mock()
                self.cache.reset_mock()
                self.client.execute_graphql.side_effect = [self.posted, self.updated]
                self.cache.invalidate_case.side_effect = OSError("secret cache path")
                with self.assertRaises(PartialCommentError) as caught:
                    self.post(status)
                error = caught.exception
                self.assert_comment(error.comment)
                self.assertEqual(error.requested_status, status)
                self.assertTrue(error.cache_invalidation_failed)
                # update_case invalidates after its mutation, so its cache failure
                # conservatively leaves status unconfirmed to this caller.
                self.assertEqual(error.status_confirmed, status is None)
                self.assertIn("Comment posted", str(error))
                self.assertIn("Do not repost", str(error))
                self.assertNotIn("secret", str(error))
                self.assertEqual(
                    self.client.execute_graphql.call_count, 2 if status else 1
                )

    def test_status_and_cache_failure_preserve_both_outcomes(self):
        self.client.execute_graphql.side_effect = [self.posted, Timeout("secret")]
        self.cache.invalidate_case.side_effect = RuntimeError("secret cache")
        with self.assertRaises(PartialCommentError) as caught:
            self.post("closed")
        self.assert_comment(caught.exception.comment)
        self.assertFalse(caught.exception.status_confirmed)
        self.assertTrue(caught.exception.cache_invalidation_failed)
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(self.client.execute_graphql.call_count, 2)

    def test_final_cache_failure_retains_confirmed_status(self):
        self.client.execute_graphql.side_effect = [self.posted, self.updated]
        self.cache.invalidate_case.side_effect = [None, OSError("secret path")]
        with self.assertRaises(PartialCommentError) as caught:
            self.post("closed")
        error = caught.exception
        self.assert_comment(error.comment)
        self.assertTrue(error.status_confirmed)
        self.assertTrue(error.cache_invalidation_failed)
        self.assertEqual(error.requested_status, "closed")
        self.assertIn("cache invalidation was not confirmed", str(error))
        self.assertNotIn("status change was not confirmed", str(error))
        self.assertNotIn("secret", str(error))
        self.assertEqual(self.client.execute_graphql.call_count, 2)

    def test_cache_interrupt_preserves_confirmed_comment(self):
        self.client.execute_graphql.return_value = self.posted
        self.cache.invalidate_case.side_effect = KeyboardInterrupt()
        with self.assertRaises(PartialCommentError) as caught:
            self.post()
        self.assert_comment(caught.exception.comment)
        self.assertTrue(caught.exception.cache_invalidation_failed)
        self.client.execute_graphql.assert_called_once()

    def test_malformed_comment_result_blocks_status_and_is_not_retried(self):
        records = [
            {},
            {"Id": None},
            {"Id": ""},
            {"Id": " \t"},
            {"Id": 123},
            {"Id": True},
            {"Id": []},
            {"Id": {}},
        ]
        responses = [
            None,
            [],
            {},
            {"redhat_support_uiapi": None},
            {"redhat_support_uiapi": {"CaseComment__cCreate": []}},
        ]
        responses += [
            {
                "redhat_support_uiapi": {
                    "CaseComment__cCreate": {"RedHatSupportRecord": record}
                }
            }
            for record in records + [None, []]
        ]
        for response in responses:
            with self.subTest(response=response):
                self.client.reset_mock()
                self.cache.reset_mock()
                self.client.execute_graphql.return_value = response
                with self.assertRaises(CommentOutcomeUnknownError) as caught:
                    self.post("closed")
                self.assertEqual(caught.exception.case_number, "12345")
                self.assertEqual(
                    str(caught.exception),
                    "Comment outcome is unknown. Verify the case in the portal. "
                    "Do not repost the comment.",
                )
                self.client.execute_graphql.assert_called_once()
                self.cache.invalidate_case.assert_not_called()

    def test_unconfirmed_post_exception_is_not_retried_or_followed_by_status(self):
        failure = Timeout("post failed")
        self.client.execute_graphql.side_effect = failure
        with self.assertRaises(Timeout) as caught:
            self.post("closed")
        self.assertIs(caught.exception, failure)
        self.client.execute_graphql.assert_called_once()
        self.cache.invalidate_case.assert_not_called()

    def test_empty_body_does_not_mutate(self):
        with self.assertRaises(ValueError):
            self.service.add_comment("12345", " \n", status="closed")
        self.client.execute_graphql.assert_not_called()
