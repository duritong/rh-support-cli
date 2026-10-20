import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rh_support_lib.api import RedHatAPIClient
from rh_support_lib.commands.watchers import (
    cmd_follow,
    cmd_follow_account,
    cmd_unfollow,
    cmd_unfollow_account,
)
from rh_support_lib.models import Case, CaseDetails
from rh_support_lib.services.cache import CacheService
from rh_support_lib.services.cases import CaseService
from rh_support_lib.services.state import StateService
from rh_support_lib.statuses import expand_status_filter


class AccountTrackingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, {"RH_NO_CACHE": "0"})
        env.start()
        self.addCleanup(env.stop)
        self.client = Mock(spec=RedHatAPIClient)
        self.client.get_cache_scope.return_value = "account-test"
        self.state = StateService(state_file=os.path.join(directory.name, "state.yaml"))
        self.cache = CacheService(cache_dir=directory.name, scope="account-test")
        self.service = CaseService(
            self.client, state_service=self.state, cache_service=self.cache
        )
        self.service.get_current_user = Mock(return_value="user")

    def test_per_case_dry_run_preserves_tracking_state(self):
        self.state.add_tracked_case("user", "existing")
        before = self.state.load_state()
        for command, number in ((cmd_follow, "new"), (cmd_unfollow, "existing")):
            with (
                self.subTest(command=command.__name__),
                patch(
                    "rh_support_lib.commands.watchers.CaseService",
                    return_value=self.service,
                ),
                patch.object(self.service, "get_case_details", return_value=None),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.state.save_state(before)
                command(
                    SimpleNamespace(
                        cases=[number], case=[number], user="user", dry_run=True
                    ),
                    self.client,
                )
                self.assertEqual(self.state.load_state(), before)
                self.assertIn("[Dry-run]", output.getvalue())
                self.assertIn(number, output.getvalue())
        self.client.execute_graphql.assert_not_called()

    def test_account_dry_run_does_not_register_owned_cases(self):
        self.state.add_tracked_case("user", "followed")
        before = self.state.load_state()
        cases = [
            Case(
                case_number=number,
                summary="Owned case",
                status="In Progress",
                severity="High",
                account_number="A",
                owner="user",
            )
            for number in ("new", "followed")
        ]
        self.client.execute_graphql.return_value = {
            "redhat_support_uiapi": {
                "query": {
                    "RedHatSupportCase": {
                        "edges": [{"node": {"Id": c.case_number}} for c in cases],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }
        for user in ("user", "other-user"):
            with (
                self.subTest(user=user),
                patch.object(self.service, "_normalize_case_node", side_effect=cases),
            ):
                self.state.save_state(before)
                receipt = self.service.follow_account("A", username=user, dry_run=True)
                self.assertEqual(self.state.load_state(), before)
                self.assertEqual(receipt["total_matching"], 2)

    def test_unfollow_account_correlates_incomplete_state_with_local_cache(self):
        for number, account, status in (
            ("closed", "A", "Closed"),
            ("open", "A", "In Progress"),
            ("other", "B", "Closed"),
        ):
            self.state.add_tracked_case("user", number)
            with patch("rh_support_lib.services.cache.time.time", return_value=1):
                self.cache.set_case_details(
                    number,
                    CaseDetails(
                        case=Case(
                            case_number=number,
                            summary=number,
                            status=status,
                            severity="High",
                            account_number=account,
                        ),
                        description="",
                    ),
                )
        self.state.add_tracked_case("user", "uncached")
        before = self.state.load_state()
        for status, expected in (
            (None, {"closed", "open"}),
            ("closed", {"closed"}),
            ("open", {"open"}),
        ):
            with self.subTest(status=status):
                receipt = self.service.unfollow_account(
                    "A", status=status, username="user", dry_run=True
                )
                self.assertEqual(set(receipt["unfollowed"]), expected)
                self.assertEqual(self.state.load_state(), before)

        receipt = self.service.unfollow_account("A", status="closed", username="user")
        self.assertEqual(receipt["unfollowed"], ["closed"])
        self.assertEqual(
            set(self.state.get_followed_cases("user")), {"open", "other", "uncached"}
        )
        self.client.execute_graphql.assert_not_called()

    def test_follow_account_merges_bookmark_filters_and_cli_overrides(self):
        config = {
            "default_bookmark": "account",
            "bookmarks": {
                "account": {"account": "A", "status": "closed"},
                "team": {"severity": "high", "user": "bookmark-user"},
            },
        }
        for overrides, account, status, severity, user in (
            ({}, "A", "closed", "high", "bookmark-user"),
            ({"account_pos": "B"}, "B", "closed", "high", "bookmark-user"),
            (
                {
                    "account": "C",
                    "status": "open",
                    "severity": "low",
                    "user": "cli-user",
                },
                "C",
                "open",
                "low",
                "cli-user",
            ),
            ({"all": True}, "A", "all", "high", "bookmark-user"),
        ):
            args = SimpleNamespace(**({"bookmark": ["team"], "json": True} | overrides))
            with (
                self.subTest(overrides=overrides),
                patch(
                    "rh_support_lib.commands.watchers.CaseService",
                    return_value=self.service,
                ),
                patch.object(self.service, "list_cases", return_value=[]) as listing,
                redirect_stdout(io.StringIO()) as output,
            ):
                cmd_follow_account(args, self.client, config)
                self.assertEqual(listing.call_args.kwargs["account"], account)
                self.assertEqual(
                    listing.call_args.kwargs["status"], expand_status_filter(status)
                )
                self.assertEqual(listing.call_args.kwargs["severity"], severity)
                receipt = json.loads(output.getvalue())
                self.assertEqual(receipt["user"], user)
                self.assertEqual(receipt["status_filter"], status)

    def test_unfollow_account_bookmark_does_not_remove_outside_filters(self):
        config = {
            "bookmarks": {
                "closed": {
                    "account": "A",
                    "status": "closed",
                    "severity": "high",
                    "user": "bookmark-user",
                }
            }
        }
        for number, status, severity in (
            ("matching", "Closed", "High"),
            ("open", "In Progress", "High"),
            ("normal", "Closed", "Normal"),
        ):
            self.state.add_tracked_case(
                "bookmark-user",
                number,
                account_number="A",
                status=status,
                severity=severity,
            )
        self.state.add_tracked_case(
            "user", "other-user", account_number="A", status="Closed", severity="High"
        )
        with (
            patch(
                "rh_support_lib.commands.watchers.CaseService",
                return_value=self.service,
            ),
            redirect_stdout(io.StringIO()) as output,
        ):
            cmd_unfollow_account(
                SimpleNamespace(bookmark=["closed"], json=True), self.client, config
            )
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt["unfollowed"], ["matching"])
        self.assertEqual(
            set(self.state.get_followed_cases("bookmark-user")), {"open", "normal"}
        )
        self.assertTrue(self.state.is_following("other-user", "user"))

    def test_account_commands_reject_unsupported_bookmark_filters(self):
        for command in (cmd_follow_account, cmd_unfollow_account):
            with (
                self.subTest(command=command.__name__),
                patch(
                    "rh_support_lib.commands.watchers.CaseService",
                    return_value=self.service,
                ),
                patch.object(self.service, "follow_account") as follow,
                patch.object(self.service, "unfollow_account") as unfollow,
                redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(SystemExit, "Unsupported.*product"),
            ):
                command(
                    SimpleNamespace(account="A", bookmark=["product"]),
                    self.client,
                    {"bookmarks": {"product": {"product": "RHEL"}}},
                )
            follow.assert_not_called()
            unfollow.assert_not_called()
