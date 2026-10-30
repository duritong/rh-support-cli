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

    def test_account_commands_ignore_language_bookmark_filter(self):
        config = {
            "default_bookmark": "bit_open",
            "bookmarks": {
                "bit_open": {
                    "account": "1376996",
                    "status": ["open"],
                    "language": "en",
                }
            },
        }
        # Test follow-account with language in bookmark
        with (
            patch(
                "rh_support_lib.commands.watchers.CaseService",
                return_value=self.service,
            ),
            patch.object(self.service, "list_cases", return_value=[]) as listing,
            redirect_stdout(io.StringIO()),
        ):
            cmd_follow_account(
                SimpleNamespace(account="1376996", json=True), self.client, config
            )
            self.assertEqual(listing.call_args.kwargs["account"], "1376996")

        # Test follow delegating to follow-account with default bookmark containing language
        with (
            patch(
                "rh_support_lib.commands.watchers.CaseService",
                return_value=self.service,
            ),
            patch.object(self.service, "list_cases", return_value=[]) as listing,
            redirect_stdout(io.StringIO()),
        ):
            cmd_follow(
                SimpleNamespace(account="1376996", json=True), self.client, config
            )
            self.assertEqual(listing.call_args.kwargs["account"], "1376996")

        dummy_receipt = {
            "account": "1376996",
            "user": "user",
            "status_filter": None,
            "unfollowed": [],
            "count": 0,
            "dry_run": False,
        }
        # Test unfollow-account with language in bookmark
        with (
            patch(
                "rh_support_lib.commands.watchers.CaseService",
                return_value=self.service,
            ),
            patch.object(
                self.service, "unfollow_account", return_value=dummy_receipt
            ) as unfollow,
            redirect_stdout(io.StringIO()),
        ):
            cmd_unfollow_account(
                SimpleNamespace(account="1376996", json=True), self.client, config
            )
            self.assertEqual(unfollow.call_args.kwargs["account"], "1376996")

        # Test unfollow delegating to unfollow-account with default bookmark containing language
        with (
            patch(
                "rh_support_lib.commands.watchers.CaseService",
                return_value=self.service,
            ),
            patch.object(
                self.service, "unfollow_account", return_value=dummy_receipt
            ) as unfollow,
            redirect_stdout(io.StringIO()),
        ):
            cmd_unfollow(
                SimpleNamespace(account="1376996", json=True), self.client, config
            )
            self.assertEqual(unfollow.call_args.kwargs["account"], "1376996")

    def test_follow_and_unfollow_subcommands_accept_account_tracking_flags(self):
        from rh_support_lib.main import _main

        for cmd_line in (
            [
                "follow",
                "-a",
                "1376996",
                "--severity",
                "high",
                "-b",
                "bit_open",
                "--no-default-bookmark",
            ],
            [
                "unfollow",
                "-a",
                "1376996",
                "--severity",
                "high",
                "-b",
                "bit_open",
                "--no-default-bookmark",
            ],
        ):
            with (
                self.subTest(cmd_line=cmd_line),
                patch("sys.argv", ["rh-support-cli", "--mock", *cmd_line]),
                patch("rh_support_lib.main.argcomplete", None),
                patch(
                    "rh_support_lib.main.load_config",
                    return_value={"bookmarks": {"bit_open": {"account": "1376996"}}},
                ),
                patch("rh_support_lib.main._start_mock"),
                patch("rh_support_lib.api.RedHatAPIClient"),
                patch(
                    "rh_support_lib.commands.watchers.cmd_follow_account"
                ) as follow_acc,
                patch(
                    "rh_support_lib.commands.watchers.cmd_unfollow_account"
                ) as unfollow_acc,
            ):
                _main()
                if cmd_line[0] == "follow":
                    self.assertTrue(follow_acc.called)
                else:
                    self.assertTrue(unfollow_acc.called)

    def test_get_auto_follow_accounts_resolution(self):
        # 1. Scalar string and int
        svc1 = CaseService(self.client, config={"auto_follow_accounts": "1376996"})
        self.assertEqual(
            svc1.get_auto_follow_accounts(),
            [{"account": "1376996", "status": "open", "severity": None}],
        )
        svc2 = CaseService(self.client, config={"auto_follow_account": 1376996})
        self.assertEqual(
            svc2.get_auto_follow_accounts(),
            [{"account": "1376996", "status": "open", "severity": None}],
        )

        # 2. List with bookmark expansion and deduplication
        config = {
            "bookmarks": {
                "bit_open": {
                    "account": "1376996",
                    "status": ["open"],
                    "severity": "High",
                }
            },
            "auto_follow_accounts": ["bit_open", "1376996", "999999"],
        }
        svc3 = CaseService(self.client, config=config)
        self.assertEqual(
            svc3.get_auto_follow_accounts(),
            [
                {"account": "1376996", "status": ["open"], "severity": "High"},
                {"account": "999999", "status": "open", "severity": None},
            ],
        )

        # 3. List of dict mappings
        config_dicts = {
            "auto_follow_accounts": [
                {"account": "12345", "status": "closed", "severity": "Urgent"}
            ]
        }
        svc4 = CaseService(self.client, config=config_dicts)
        self.assertEqual(
            svc4.get_auto_follow_accounts(),
            [{"account": "12345", "status": "closed", "severity": "Urgent"}],
        )

    def test_sync_auto_follows_new_account_cases(self):
        config = {
            "auto_follow_accounts": ["1376996"],
        }
        svc = CaseService(
            self.client,
            state_service=self.state,
            cache_service=self.cache,
            config=config,
        )
        svc.get_current_user = Mock(return_value="test_user")
        # Initial mock cases returned for account 1376996
        case1 = Case(
            case_number="11111",
            summary="Case 1",
            status="Waiting on Red Hat",
            severity="High",
            account_number="1376996",
        )
        case2 = Case(
            case_number="22222",
            summary="Case 2",
            status="Waiting on Red Hat",
            severity="High",
            account_number="1376996",
        )

        with (
            patch.object(svc, "list_cases", return_value=[case1, case2]),
            patch.object(
                svc,
                "get_case_details",
                return_value=CaseDetails(case=case1, description="desc", comments=[]),
            ),
        ):
            report = svc.sync_tracked_cases(username="test_user")
            self.assertEqual(report["newly_followed_count"], 2)
            self.assertEqual(set(report["newly_followed"]), {"11111", "22222"})
            self.assertEqual(report["synced_count"], 2)
            self.assertTrue(svc.state_service.is_following("11111", "test_user"))
            self.assertTrue(svc.state_service.is_following("22222", "test_user"))

            # Second sync: no new cases on account
            report2 = svc.sync_tracked_cases(username="test_user")
            self.assertEqual(report2["newly_followed_count"], 0)
            self.assertEqual(report2["newly_followed"], [])
            self.assertEqual(report2["synced_count"], 2)

            # Third sync with a 3rd new case arriving on the account
            case3 = Case(
                case_number="33333",
                summary="Case 3",
                status="Waiting on Red Hat",
                severity="High",
                account_number="1376996",
            )
            with patch.object(svc, "list_cases", return_value=[case1, case2, case3]):
                report3 = svc.sync_tracked_cases(username="test_user")
                self.assertEqual(report3["newly_followed_count"], 1)
                self.assertEqual(report3["newly_followed"], ["33333"])
                self.assertEqual(report3["synced_count"], 3)
                self.assertTrue(svc.state_service.is_following("33333", "test_user"))

    def test_sync_cli_command_reports_auto_followed_cases(self):
        from rh_support_lib.commands.tracking import cmd_sync

        config = {"auto_follow_accounts": ["1376996"]}
        args = SimpleNamespace(
            user="test_user",
            account=None,
            format="text",
            json=False,
            notify=False,
            install_systemd=False,
            simple_output=True,
            dry_run=False,
        )
        fake_report = {
            "synced_count": 5,
            "updated_cases": [],
            "new_comments": 0,
            "newly_followed": ["33333"],
            "newly_followed_count": 1,
        }
        with (
            patch("rh_support_lib.commands.tracking.CaseService") as mock_cs,
            redirect_stdout(io.StringIO()) as output,
        ):
            instance = mock_cs.return_value
            instance.sync_tracked_cases.return_value = fake_report
            cmd_sync(args, self.client, config=config)
            out = output.getvalue()
            self.assertIn("1 new case(s) auto-followed", out)
            self.assertIn("5 tracked case(s) checked", out)
