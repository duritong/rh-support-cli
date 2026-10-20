"""Focused tests for case tracking, following, and unread checkpoints."""

from __future__ import annotations

import json
import os
import shutil
import tempfile

from tests.support.mock_server import MockRedHatHandler, MockServerTestCase


class TestCliTracking(MockServerTestCase):
    def test_follow_and_unfollow_commands(self):
        """Test follow and unfollow CLI commands for single and multiple cases"""
        expected_user = self.env.get("REDHAT_SUPPORT_USERNAME") or self.env.get(
            "USER", "jdeveloper"
        )
        # 1. Follow single case via flag
        result_follow1 = self.run_cli(["follow", "-c", "12345"])
        self.assertEqual(result_follow1.returncode, 0)
        self.assertIn(
            f"Success: Added '{expected_user}' as watcher to Case #12345.",
            result_follow1.stdout,
        )

        # 2. Query following via 'following' and 'list --following'
        res_following = self.run_cli(["following"])
        self.assertEqual(res_following.returncode, 0)
        self.assertIn("12345", res_following.stdout)
        res_list_following = self.run_cli(["list", "--following"])
        self.assertEqual(res_list_following.returncode, 0)
        self.assertIn("12345", res_list_following.stdout)

        # 3. Follow multiple cases via positional args and custom user
        result_follow_multi = self.run_cli(
            ["follow", "1001", "1002", "--user", "custom_user"]
        )
        self.assertEqual(result_follow_multi.returncode, 0)
        self.assertIn(
            "Adding 'custom_user' as watcher on 2 case(s)...",
            result_follow_multi.stdout,
        )
        self.assertIn(
            "Success: Added 'custom_user' as watcher to Case #1001.",
            result_follow_multi.stdout,
        )
        self.assertIn(
            "Success: Added 'custom_user' as watcher to Case #1002.",
            result_follow_multi.stdout,
        )

        # 4. Query custom user following
        res_custom_following = self.run_cli(["following", "--user", "custom_user"])
        self.assertEqual(res_custom_following.returncode, 0)
        self.assertIn("1001", res_custom_following.stdout)
        self.assertIn("1002", res_custom_following.stdout)

        # 5. Unfollow single case
        result_unfollow1 = self.run_cli(["unfollow", "-c", "12345"])
        self.assertEqual(result_unfollow1.returncode, 0)
        self.assertIn(
            f"Success: Removed '{expected_user}' from watchers on Case #12345.",
            result_unfollow1.stdout,
        )

        # 6. Verify 12345 is no longer followed
        res_after_unfollow = self.run_cli(["following"])
        self.assertEqual(res_after_unfollow.returncode, 0)
        self.assertIn("No cases found.", res_after_unfollow.stdout)

        # 7. Unfollow multiple cases via positional args
        result_unfollow_multi = self.run_cli(
            ["unfollow", "1001", "1002", "--user", "custom_user"]
        )
        self.assertEqual(result_unfollow_multi.returncode, 0)
        self.assertIn(
            "Removing 'custom_user' from watchers on 2 case(s)...",
            result_unfollow_multi.stdout,
        )
        self.assertIn(
            "Success: Removed 'custom_user' from watchers on Case #1001.",
            result_unfollow_multi.stdout,
        )
        self.assertIn(
            "Success: Removed 'custom_user' from watchers on Case #1002.",
            result_unfollow_multi.stdout,
        )

        # 8. Follow with no arguments fails gracefully
        result_err = self.run_cli(["follow"])
        self.assertNotEqual(result_err.returncode, 0)
        self.assertIn(
            "Please specify at least one case number",
            result_err.stderr,
        )

    def test_following_bookmark_and_sync(self):
        """Test using following bookmark in config and follow --sync command"""

        import yaml

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        orig_state = self.env.get("XDG_STATE_HOME")
        self.env["HOME"] = temp_home
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_home, ".config")
        self.env["XDG_STATE_HOME"] = os.path.join(temp_home, ".local", "state")
        try:
            config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
            os.makedirs(config_dir, exist_ok=True)
            config_file = os.path.join(config_dir, "config.yaml")
            with open(config_file, "w") as f:
                yaml.dump(
                    {
                        "bookmarks": {
                            "my-watched": {
                                "following": True,
                            }
                        }
                    },
                    f,
                )

            # 1. Follow a case
            res_f = self.run_cli(["follow", "12345"])
            self.assertEqual(res_f.returncode, 0)

            # 2. Query list using bookmark
            res_bk = self.run_cli(["list", "-b", "my-watched"])
            self.assertEqual(res_bk.returncode, 0)
            self.assertIn("12345", res_bk.stdout)

            # 3. Test follow --sync command
            res_sync = self.run_cli(["follow", "--sync"])
            self.assertEqual(res_sync.returncode, 0)
            self.assertIn("Sync complete.", res_sync.stdout)
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            if orig_state:
                self.env["XDG_STATE_HOME"] = orig_state
            shutil.rmtree(temp_home)

    def test_state_service_direct(self):
        """Test StateService directly for atomic persistence, following, sync, pinned, tags, and recent"""

        from rh_support_lib.services.state import StateService

        temp_dir = tempfile.mkdtemp()
        try:
            state_file = os.path.join(temp_dir, "state.yaml")
            svc = StateService(state_file=state_file)

            # 1. Followed Cases
            self.assertFalse(svc.is_following("12345", "testuser"))
            svc.add_followed_case(
                "12345", "testuser", summary="Test Case", account_number="9999"
            )
            self.assertTrue(svc.is_following("12345", "testuser"))

            followed = svc.get_followed_cases("testuser")
            self.assertEqual(len(followed), 1)
            self.assertEqual(followed["12345"].summary, "Test Case")
            self.assertEqual(followed["12345"].account_number, "9999")

            # Remove
            self.assertTrue(svc.remove_followed_case("12345", "testuser"))
            self.assertFalse(svc.is_following("12345", "testuser"))
            self.assertFalse(svc.remove_followed_case("12345", "testuser"))

            # 2. Sync Checkpoints
            svc.update_sync_info("testuser", "9999", "2026-08-27T10:00:00Z", 15)
            sync_info = svc.get_sync_info("testuser", "9999")
            self.assertEqual(sync_info.get("last_synced_at"), "2026-08-27T10:00:00Z")
            self.assertEqual(sync_info.get("last_scanned_case_count"), 15)

            # 3. Pinned Cases
            svc.set_pinned_cases("testuser", ["1001", "1002"])
            self.assertEqual(svc.get_pinned_cases("testuser"), ["1001", "1002"])

            # 4. Recent Cases
            svc.add_recent_case("testuser", "1001", {"summary": "Recent 1"})
            recent = svc.get_recent_cases("testuser")
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["case_number"], "1001")

            # 5. Tags
            svc.set_tags("testuser", "1001", ["ocp", "prod"])
            self.assertEqual(svc.get_tags("testuser", "1001"), ["ocp", "prod"])
        finally:
            shutil.rmtree(temp_dir)

    def test_state_service_tracked_and_checkpoints(self):
        """Test StateService tracking, checkpoints, unread counts, and auto-registration."""

        from rh_support_lib.services.state import StateService

        temp_dir = tempfile.mkdtemp()
        try:
            state_file = os.path.join(temp_dir, "state.yaml")
            svc = StateService(state_file=state_file)
            # 1. Follow a case
            svc.add_tracked_case(
                "testuser",
                "12345",
                relationship="followed",
                summary="Case 12345",
                status="Waiting on Red Hat",
            )
            self.assertTrue(svc.is_following("12345", "testuser"))
            tc = svc.get_tracked_cases("testuser").get("12345")
            self.assertIsNotNone(tc)
            self.assertEqual(tc.relationship, "followed")
            self.assertEqual(tc.case_number, "12345")

            # 2. Update latest known remote state (new comment and status change)
            svc.update_latest_known(
                user="testuser",
                case_number="12345",
                status="Waiting on Customer Action Required",
                severity="1 (Urgent)",
                latest_comment_id="comm_002",
                latest_comment_date="2026-10-08T12:00:00Z",
                latest_comment_author="Red Hat Support",
                latest_comment_snippet="Please provide logs",
                last_modified_date="2026-10-08T12:00:00Z",
                new_comment_count=1,
            )
            tc = svc.get_tracked_cases("testuser").get("12345")
            self.assertTrue(tc.has_unread)
            self.assertTrue(tc.status_changed)
            self.assertEqual(tc.unread_count, 1)

            # 3. Mark read
            svc.mark_case_read("testuser", "12345")
            tc = svc.get_tracked_cases("testuser").get("12345")
            self.assertFalse(tc.has_unread)
            self.assertFalse(tc.status_changed)
            self.assertEqual(tc.unread_count, 0)
            self.assertEqual(
                tc.read_checkpoint.status, "Waiting on Customer Action Required"
            )
            self.assertEqual(tc.read_checkpoint.last_read_comment_id, "comm_002")

            # 4. Auto-register owned case
            svc.register_owned_case(
                "testuser", "99999", summary="My Bug", status="In Progress"
            )
            owned = svc.get_owned_cases("testuser")
            self.assertIn("99999", owned)
            self.assertEqual(owned["99999"].relationship, "owned")
        finally:
            shutil.rmtree(temp_dir)

    def test_updates_and_show_checkpoint(self):
        """Test updates CLI command and show command with visual checkpoint divider and status banner."""

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        orig_state = self.env.get("XDG_STATE_HOME")
        self.env["HOME"] = temp_home
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_home, ".config")
        self.env["XDG_STATE_HOME"] = os.path.join(temp_home, ".local", "state")
        try:
            # 1. Follow case 12345
            res_f = self.run_cli(["follow", "12345"])
            self.assertEqual(res_f.returncode, 0)

            # 2. Manually set older read checkpoint in state.yaml to simulate unread comments and status change
            state_path = os.path.join(
                temp_home, ".local", "state", "rh-support-cli", "state.yaml"
            )
            user = self.env.get("REDHAT_SUPPORT_USERNAME") or self.env.get(
                "USER", "jdeveloper"
            )
            from rh_support_lib.services.state import StateService

            svc = StateService(state_file=state_path)
            svc.add_tracked_case(
                user, "12345", relationship="followed", summary="Test Case"
            )
            # Checkpoint with older date and old status
            state_data = svc.load_state()
            u_state = svc._get_user_state(state_data, user)
            u_state["tracked_cases"]["12345"]["read_checkpoint"] = {
                "last_read_comment_date": "2023-01-01T00:00:00Z",
                "last_read_comment_id": "old_comm",
                "status": "Waiting on Customer Action Required",
                "marked_read_at": "2023-01-01T00:00:00Z",
            }
            u_state["tracked_cases"]["12345"]["latest_known"] = {
                "last_comment_date": "2026-08-20T10:00:00Z",
                "last_comment_id": "c1",
                "status": "Waiting on Red Hat",
                "status_changed": True,
                "unread_comment_count": 2,
                "latest_comment_author": "Red Hat Support",
                "latest_comment_snippet": "We are investigating",
            }
            svc.save_state(state_data)

            # 3. Run 'updates' command
            res_upd = self.run_cli(["updates", "--simple-output"])
            self.assertEqual(res_upd.returncode, 0)
            self.assertIn("Case #12345", res_upd.stdout)
            self.assertIn("Status Changed", res_upd.stdout)
            self.assertIn("new comment(s)", res_upd.stdout)

            # 4. Run 'show -c 12345' with simple output: verify status diff banner and checkpoint divider
            res_show = self.run_cli(
                ["show", "-c", "12345", "--simple-output", "--no-pager"]
            )
            self.assertEqual(res_show.returncode, 0)
            self.assertIn("⚡ Status Changed:", res_show.stdout)
            self.assertIn(
                "Waiting on Customer Action Required -> Waiting on Red Hat",
                res_show.stdout,
            )
            self.assertIn("Last read checkpoint", res_show.stdout)

            # 5. Mark read via 'show -c 12345 --mark-read'
            res_mark = self.run_cli(
                ["show", "-c", "12345", "--mark-read", "--simple-output", "--no-pager"]
            )
            self.assertEqual(res_mark.returncode, 0)
            self.assertIn("Marked Case #12345 as read.", res_mark.stdout)

            # 6. Verify subsequent updates command reports no unread updates
            res_upd_clean = self.run_cli(["updates", "--simple-output"])
            self.assertEqual(res_upd_clean.returncode, 0)
            self.assertIn(
                "No unread updates across tracked cases.", res_upd_clean.stdout
            )
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            if orig_state:
                self.env["XDG_STATE_HOME"] = orig_state
            shutil.rmtree(temp_home)

    def test_state_service_xdg_state_path_resolution(self):
        """Test StateService XDG state path resolution and fallback mechanisms."""
        from unittest.mock import patch

        from rh_support_lib.services.state import StateService

        # 1. Defaults to ~/.local/state/rh-support-cli/state.yaml
        temp_fresh = tempfile.mkdtemp()
        try:
            with patch.dict(os.environ, {"HOME": temp_fresh}, clear=True):
                resolved = StateService._resolve_state_path()
                self.assertTrue(
                    resolved.endswith(
                        os.path.join(".local", "state", "rh-support-cli", "state.yaml")
                    )
                )
        finally:
            shutil.rmtree(temp_fresh)
        # 2. Honors XDG_STATE_HOME
        with patch.dict(os.environ, {"XDG_STATE_HOME": "/custom/state"}):
            resolved = StateService._resolve_state_path()
            self.assertEqual(resolved, "/custom/state/rh-support-cli/state.yaml")

        # 3. Honors explicit state_file and RH_SUPPORT_STATE
        with patch.dict(os.environ, {"RH_SUPPORT_STATE": "/custom/env/state.yaml"}):
            resolved = StateService._resolve_state_path()
            self.assertEqual(resolved, "/custom/env/state.yaml")
        resolved = StateService._resolve_state_path(state_file="/explicit/state.yaml")
        self.assertEqual(resolved, "/explicit/state.yaml")

        # 4. Automatic migration from legacy config dir if legacy state file exists
        temp_dir = tempfile.mkdtemp()
        try:
            legacy_dir = os.path.join(temp_dir, "config", "rh-support-cli")
            os.makedirs(legacy_dir, exist_ok=True)
            legacy_file = os.path.join(legacy_dir, "state.yaml")
            with open(legacy_file, "w") as f:
                f.write("version: 1\n")
            target_state_dir = os.path.join(temp_dir, "state")
            with patch.dict(
                os.environ,
                {
                    "XDG_CONFIG_HOME": os.path.join(temp_dir, "config"),
                    "XDG_STATE_HOME": target_state_dir,
                },
                clear=True,
            ):
                expected_migrated = os.path.join(
                    target_state_dir, "rh-support-cli", "state.yaml"
                )
                resolved = StateService._resolve_state_path()
                self.assertEqual(resolved, expected_migrated)
                self.assertTrue(os.path.exists(expected_migrated))
                with open(expected_migrated) as f:
                    self.assertEqual(f.read(), "version: 1\n")
                self.assertFalse(os.path.exists(legacy_file))
        finally:
            shutil.rmtree(temp_dir)

    def test_sync_and_updates_detection_and_concurrency(self):
        """Test that sync detects remote updates and new comments, preserving all cases under concurrency."""

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        orig_state = self.env.get("XDG_STATE_HOME")
        self.env["HOME"] = temp_home
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_home, ".config")
        self.env["XDG_STATE_HOME"] = os.path.join(temp_home, ".local", "state")
        try:
            # 1. Follow multiple cases (including case with leading zero '04526149')
            case_list = ["04526149"] + [f"100{i}" for i in range(10)]
            res_follow = self.run_cli(["follow"] + case_list)
            self.assertEqual(res_follow.returncode, 0)

            # Initial sync: everything is already up to date with checkpoint
            res_sync1 = self.run_cli(["sync"])
            self.assertEqual(res_sync1.returncode, 0)
            self.assertIn("0 updated", res_sync1.stdout)
            self.assertIn("0 new comment(s)", res_sync1.stdout)

            # Verify 'updates' reports no unread updates
            res_upd1 = self.run_cli(["updates"])
            self.assertEqual(res_upd1.returncode, 0)
            self.assertIn("No unread updates across tracked cases.", res_upd1.stdout)

            # 2. Remote server receives a new comment for case 04526149 with millisecond ISO timestamp
            MockRedHatHandler.latest_mock_comment_id = "comment_remote_new"
            MockRedHatHandler.latest_mock_comment_date = "2026-10-12T12:30:00.500+0000"

            # 3. Run sync: should detect the 1 update on case 04526149!
            res_sync2 = self.run_cli(["sync"])
            self.assertEqual(res_sync2.returncode, 0)
            self.assertIn("1 updated", res_sync2.stdout)
            self.assertIn("1 new comment(s)", res_sync2.stdout)

            # 4. Run updates: should clearly list case 04526149 and its new comment
            res_upd2 = self.run_cli(["updates"])
            self.assertEqual(res_upd2.returncode, 0)
            self.assertIn("04526149", res_upd2.stdout)
            self.assertIn("1 new comment(s)", res_upd2.stdout)

            # 5. Verify all 11 cases are preserved in state.yaml without race conditions
            from rh_support_lib.services.state import StateService

            state_path = os.path.join(
                temp_home, ".local", "state", "rh-support-cli", "state.yaml"
            )
            svc = StateService(state_file=state_path)
            user = svc.load_state()["users"]
            current_user = next(iter(user.keys()))
            tracked = svc.get_tracked_cases(current_user)
            self.assertEqual(len(tracked), 11)
            self.assertIn("04526149", tracked)
            self.assertEqual(tracked["04526149"].latest_known.unread_comment_count, 1)

            # 6. Mark read advances checkpoint
            res_mark = self.run_cli(["updates", "--mark-read"])
            self.assertEqual(res_mark.returncode, 0)

            res_upd3 = self.run_cli(["updates"])
            self.assertIn("No unread updates across tracked cases.", res_upd3.stdout)
        finally:
            MockRedHatHandler.latest_mock_comment_id = "comment_2"
            MockRedHatHandler.latest_mock_comment_date = "2023-01-03T10:00:00Z"
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            if orig_state:
                self.env["XDG_STATE_HOME"] = orig_state
            shutil.rmtree(temp_home)

    def test_add_tracked_case_preserves_checkpoints(self):
        """Test that re-registering or following an existing case preserves checkpoints."""

        from rh_support_lib.services.state import StateService

        temp_dir = tempfile.mkdtemp()
        try:
            state_file = os.path.join(temp_dir, "state.yaml")
            svc = StateService(state_file=state_file)

            # Track initial case with checkpoint
            svc.add_tracked_case(
                user="testuser",
                case_number="04526149",
                relationship="followed",
                summary="Initial Summary",
                status="Waiting on Red Hat",
                last_comment_id="comm_1",
                last_comment_date="2026-10-10T00:00:00Z",
            )

            tc = svc.get_tracked_cases("testuser")["04526149"]
            self.assertEqual(tc.read_checkpoint.status, "Waiting on Red Hat")
            self.assertEqual(tc.read_checkpoint.last_read_comment_id, "comm_1")
            self.assertEqual(
                tc.read_checkpoint.last_read_comment_date, "2026-10-10T00:00:00Z"
            )

            # Re-register case as owned (e.g. from auto-tracking list)
            svc.register_owned_case(
                user="testuser",
                case_number="04526149",
                summary="Updated Summary",
                status="",  # status omitted
                last_comment_id="",
                last_comment_date="",
            )

            tc2 = svc.get_tracked_cases("testuser")["04526149"]
            # Relationship merged to 'both'
            self.assertEqual(tc2.relationship, "both")
            self.assertEqual(tc2.summary, "Updated Summary")
            # Existing checkpoints preserved!
            self.assertEqual(tc2.read_checkpoint.status, "Waiting on Red Hat")
            self.assertEqual(tc2.read_checkpoint.last_read_comment_id, "comm_1")
            self.assertEqual(
                tc2.read_checkpoint.last_read_comment_date, "2026-10-10T00:00:00Z"
            )
        finally:
            shutil.rmtree(temp_dir)

    def test_follow_account_command(self):
        """Test follow-account command: default open cases, --all, --status, and --dry-run."""
        MockRedHatHandler.mock_account_cases["ACCT123"] = [
            {
                "case_number": "2001",
                "summary": "First open issue",
                "status": "Waiting on Red Hat",
                "severity": "2 (High)",
            },
            {
                "case_number": "2002",
                "summary": "Second open issue",
                "status": "Waiting on Customer Action Required",
                "severity": "3 (Normal)",
            },
            {
                "case_number": "2003",
                "summary": "Resolved issue",
                "status": "Closed",
                "severity": "4 (Low)",
            },
        ]
        try:
            # 1. Dry run on account
            res_dry = self.run_cli(["follow-account", "ACCT123", "--dry-run"])
            self.assertEqual(res_dry.returncode, 0)
            self.assertIn("[Dry-run] Would follow 2 case(s)", res_dry.stdout)
            self.assertIn("Case #2001", res_dry.stdout)
            self.assertIn("Case #2002", res_dry.stdout)
            self.assertNotIn("Case #2003", res_dry.stdout)

            # Verify nothing tracked yet
            res_following0 = self.run_cli(["following"])
            self.assertNotIn("2001", res_following0.stdout)

            # 2. Default follow-account (open cases only)
            res_follow = self.run_cli(["follow-account", "ACCT123"])
            self.assertEqual(res_follow.returncode, 0)
            self.assertIn(
                "Success: Followed 2 case(s) for account ACCT123", res_follow.stdout
            )
            self.assertIn("Followed Case #2001", res_follow.stdout)
            self.assertIn("Followed Case #2002", res_follow.stdout)

            # Verify both are now followed, but not 2003
            res_following1 = self.run_cli(["following"])
            self.assertIn("2001", res_following1.stdout)
            self.assertIn("2002", res_following1.stdout)
            self.assertNotIn("2003", res_following1.stdout)

            # 3. Running again reports already followed
            res_follow_repeat = self.run_cli(["follow-account", "ACCT123"])
            self.assertEqual(res_follow_repeat.returncode, 0)
            self.assertIn(
                "0 case(s) for account ACCT123 (2 already followed)",
                res_follow_repeat.stdout,
            )
            self.assertIn("Already following Case #2001", res_follow_repeat.stdout)

            # 4. Follow with --all includes closed cases
            res_all = self.run_cli(["follow-account", "ACCT123", "--all"])
            self.assertEqual(res_all.returncode, 0)
            self.assertIn("Followed Case #2003", res_all.stdout)
            self.assertIn(
                "Success: Followed 1 case(s) for account ACCT123 (2 already followed)",
                res_all.stdout,
            )

            # 5. Test JSON output format
            res_json = self.run_cli(["follow-account", "ACCT123", "--all", "--json"])
            self.assertEqual(res_json.returncode, 0)
            data = json.loads(res_json.stdout)
            self.assertEqual(data["account"], "ACCT123")
            self.assertTrue(data["success"])
            self.assertIn("2001", data["already_followed"])
            self.assertIn("2003", data["already_followed"])

            # 6. Test unfollow-account with --status closed
            res_unfollow_closed = self.run_cli(
                ["unfollow-account", "ACCT123", "--status", "closed"]
            )
            self.assertEqual(res_unfollow_closed.returncode, 0)
            self.assertIn("Success: Unfollowed 1 case(s)", res_unfollow_closed.stdout)
            self.assertIn("Unfollowed Case #2003", res_unfollow_closed.stdout)

            # Verify 2001 and 2002 are still followed, 2003 is gone
            res_following2 = self.run_cli(["following"])
            self.assertIn("2001", res_following2.stdout)
            self.assertIn("2002", res_following2.stdout)
            self.assertNotIn("2003", res_following2.stdout)

            # 7. Test unfollow-account dry run
            res_unfollow_dry = self.run_cli(
                ["unfollow-account", "ACCT123", "--dry-run"]
            )
            self.assertEqual(res_unfollow_dry.returncode, 0)
            self.assertIn("[Dry-run] Would unfollow 2 case(s)", res_unfollow_dry.stdout)

            # 8. Unfollow all remaining cases for account
            res_unfollow_all = self.run_cli(["unfollow-account", "ACCT123"])
            self.assertEqual(res_unfollow_all.returncode, 0)
            self.assertIn("Success: Unfollowed 2 case(s)", res_unfollow_all.stdout)

            res_following3 = self.run_cli(["following"])
            self.assertNotIn("2001", res_following3.stdout)
            self.assertNotIn("2002", res_following3.stdout)

            # 9. Test delegation from follow --account and unfollow --account
            res_del_follow = self.run_cli(["follow", "--account", "ACCT123"])
            self.assertEqual(res_del_follow.returncode, 0)
            self.assertIn("Followed 2 case(s)", res_del_follow.stdout)

            res_del_unfollow = self.run_cli(["unfollow", "--account", "ACCT123"])
            self.assertEqual(res_del_unfollow.returncode, 0)
            self.assertIn("Unfollowed 2 case(s)", res_del_unfollow.stdout)
        finally:
            MockRedHatHandler.mock_account_cases.pop("ACCT123", None)

    def test_case_service_follow_and_unfollow_account_validation(self):
        from unittest.mock import Mock

        from rh_support_lib.services.cases import CaseService

        service = CaseService(Mock())
        with self.assertRaises(ValueError):
            service.follow_account(account="")
        with self.assertRaises(ValueError):
            service.unfollow_account(account="")
