"""Focused tests for case listing, display, search, and pagination."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

from tests.support.mock_server import MockRedHatHandler, MockServerTestCase


class TestCliCases(MockServerTestCase):
    def test_list_cases(self):
        """Test listing cases"""
        # Test basic list command
        result = self.run_cli(["list", "--account", "12345", "--status", "redhat"])

        if result.returncode != 0:
            print("\nSTDOUT:", result.stdout)
            print("STDERR:", result.stderr)

        self.assertEqual(result.returncode, 0)
        # Check for headers
        self.assertIn("NUMBER", result.stdout)
        self.assertIn("TITLE", result.stdout)
        self.assertIn("OWNER", result.stdout)
        # Check for data
        self.assertIn("FILTER_TEST", result.stdout)
        self.assertIn("AUTH=DEFAULT", result.stdout)
        self.assertIn("ACC=12345", result.stdout)
        self.assertIn("STAT=Waiting on Red Hat", result.stdout)

    def test_link_command(self):
        """Test the link subcommand (no auth required)"""
        # Unset token to prove auth is skipped
        env_no_token = self.env.copy()
        if "REDHAT_SUPPORT_OFFLINE_TOKEN" in env_no_token:
            del env_no_token["REDHAT_SUPPORT_OFFLINE_TOKEN"]

        cmd = [sys.executable, self.cli_path, "link", "--case", "12345"]
        result = subprocess.run(
            cmd, env=env_no_token, capture_output=True, text=True, check=False
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("https://access.redhat.com/support/cases/12345", result.stdout)

    def test_show_case(self):
        """Test showing case details with colors (default)"""
        result = self.run_cli(["show", "-c", "12345", "--no-pager"])

        if result.returncode != 0:
            print("Failed Show Case Test STDOUT:", result.stdout)
            print("Failed Show Case Test STDERR:", result.stderr)

        self.assertEqual(result.returncode, 0)
        self.assertIn("CASE: 12345", result.stdout)
        self.assertIn("TITLE:", result.stdout)
        self.assertIn("Test Case Details", result.stdout)
        self.assertIn("DESCRIPTION:", result.stdout)
        self.assertIn("This is a detailed description.", result.stdout)
        self.assertIn("Comment 1", result.stdout)
        self.assertIn("Comment 2", result.stdout)
        self.assertIn("(Private (Internal))", result.stdout)

        # Check for ANSI escape codes (colors)
        self.assertIn("\033[", result.stdout)

    def test_show_case_renders_details_when_attachments_fail_in_text_mode(self):
        """Test that show renders case details with a warning when attachments fail in text mode."""
        MockRedHatHandler.fail_attachments = True
        try:
            # Text mode renders case details and warns on stderr
            result = self.run_cli(["show", "-c", "12345", "--no-pager"])
            self.assertEqual(result.returncode, 0)
            self.assertIn("CASE: 12345", result.stdout)
            self.assertIn("Test Case Details", result.stdout)
            self.assertIn("ATTACHMENTS", result.stdout)
            self.assertIn("unavailable", result.stdout)
            self.assertIn(
                "Warning: Attachments for case 12345 are unavailable",
                result.stderr,
            )
            self.assertIn("Generate a new Red Hat offline token", result.stderr)
            self.assertIn("api.graphql", result.stderr)

            # JSON mode fails closed to prevent emitting incomplete structured data
            result_json = self.run_cli(["show", "-c", "12345", "--format", "json"])
            self.assertNotEqual(result_json.returncode, 0)
            self.assertEqual(result_json.stdout, "")
            self.assertIn("Generate a new Red Hat offline token", result_json.stderr)
        finally:
            MockRedHatHandler.fail_attachments = False

    def test_show_case_incident_export(self):
        """Test incident shift-handoff export in markdown, slack, and json formats."""

        # 1. Default markdown export
        res_md = self.run_cli(["show", "-c", "12345", "--export"])
        self.assertEqual(res_md.returncode, 0)
        self.assertIn("Red Hat Case #12345", res_md.stdout)
        self.assertIn(
            "**Portal Link**: https://access.redhat.com/support/cases/12345",
            res_md.stdout,
        )
        self.assertIn("**Status**:", res_md.stdout)
        self.assertIn("**Next Step**:", res_md.stdout)
        # 2. Slack formatted export
        res_slack = self.run_cli(["show", "-c", "12345", "--export", "slack"])
        self.assertEqual(res_slack.returncode, 0)
        self.assertIn(
            "*<https://access.redhat.com/support/cases/12345|Red Hat Case #12345>*",
            res_slack.stdout,
        )
        self.assertIn("• *Status*:", res_slack.stdout)
        self.assertIn("• *Next Step*:", res_slack.stdout)

        # 3. JSON formatted export
        res_json = self.run_cli(["show", "-c", "12345", "--export", "json"])
        self.assertEqual(res_json.returncode, 0)
        data = json.loads(res_json.stdout)
        self.assertEqual(data["case_number"], "12345")
        self.assertEqual(
            data["portal_url"], "https://access.redhat.com/support/cases/12345"
        )
        self.assertIn("next_step", data)
        self.assertIn("attachments", data)

    def test_simple_output(self):
        """Test showing case details with simple output (no colors)"""
        # Global args must come before subcommand
        result = self.run_cli(["--simple-output", "show", "-c", "12345", "--no-pager"])
        if result.returncode != 0:
            print("Failed Simple Output Test STDOUT:", result.stdout)
            print("Failed Simple Output Test STDERR:", result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertIn("CASE: 12345", result.stdout)
        # Check NO ANSI escape codes
        self.assertNotIn("\033[", result.stdout)

    def test_bookmarks(self):
        """Test bookmark functionality"""
        # Create config file
        config_content = """
default_bookmark: "my_team"
bookmarks:
  my_team:
    account: "12345"
    status: ["Waiting on Red Hat"]
  my_cases:
    owner: "jdoe"
    status: ["open"]
"""
        with open("test_config.yaml", "w") as f:
            f.write(config_content)

        try:
            # 1. Default bookmark (my_team)
            # Should filter by account 12345 and status Waiting on Red Hat
            result = self.run_cli(["--config-file", "test_config.yaml", "list"])
            self.assertEqual(result.returncode, 0)
            self.assertIn("ACC=12345", result.stdout)
            self.assertIn("STAT=Waiting on Red Hat", result.stdout)

            # 2. --no-default-bookmark
            result = self.run_cli(
                ["--config-file", "test_config.yaml", "list", "--no-default-bookmark"]
            )
            self.assertEqual(result.returncode, 0)
            # Should NOT have filters
            self.assertNotIn("ACC=12345", result.stdout)
            self.assertNotIn("STAT=Waiting on Red Hat", result.stdout)

            # 3. Explicit Bookmark (my_cases)
            result = self.run_cli(
                [
                    "--config-file",
                    "test_config.yaml",
                    "list",
                    "--bookmark",
                    "my_cases",
                    "--no-default-bookmark",
                ]
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("OWN=jdoe", result.stdout)
            self.assertIn("STAT=Waiting on Red Hat,Waiting on Customer", result.stdout)

            # 4. Override (Default + Explicit Status)
            # Default sets account=12345, status="Waiting on Red Hat"
            # Override status="Closed"
            result = self.run_cli(
                ["--config-file", "test_config.yaml", "list", "--status", "Closed"]
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("ACC=12345", result.stdout)  # From bookmark
            self.assertIn("STAT=Closed", result.stdout)  # From CLI
            self.assertNotIn("STAT=Waiting on Red Hat", result.stdout)

        finally:
            if os.path.exists("test_config.yaml"):
                os.remove("test_config.yaml")

    def test_status_filter_map_new_crm(self):
        """Test that STATUS_FILTER_MAP maps open, customer, and redhat to new CRM GraphQL statuses"""

        from rh_support_lib.constants import STATUS_FILTER_MAP

        # 1. Verify mapping entries contain new CRM statuses
        open_statuses = [s.strip() for s in STATUS_FILTER_MAP["open"].split(",")]
        self.assertIn("Waiting on Customer Action Required", open_statuses)
        self.assertIn("In Progress", open_statuses)
        self.assertIn("Waiting on Engineering", open_statuses)
        self.assertIn("Needs New Owner", open_statuses)

        customer_statuses = [
            s.strip() for s in STATUS_FILTER_MAP["customer"].split(",")
        ]
        self.assertIn("Waiting on Customer Action Required", customer_statuses)

        redhat_statuses = [s.strip() for s in STATUS_FILTER_MAP["redhat"].split(",")]
        self.assertIn("In Progress", redhat_statuses)
        self.assertIn("Waiting on Engineering", redhat_statuses)
        self.assertIn("Needs New Owner", redhat_statuses)

        # 2. Test CLI list with --status open transmits new CRM statuses in filter
        self.env["COLUMNS"] = "400"
        result_open = self.run_cli(["list", "--status", "open"])
        self.assertEqual(result_open.returncode, 0)
        self.assertIn("Waiting on Customer Action Required", result_open.stdout)
        self.assertIn("In Progress", result_open.stdout)
        # 3. Test config bookmark with status: ["open"]
        temp_home = tempfile.mkdtemp()
        try:
            config_file = os.path.join(temp_home, "config.yaml")
            with open(config_file, "w") as f:
                f.write(
                    'bookmarks:\n  sample_open:\n    account: "999999"\n    status: ["open"]\n'
                )
            result_bm = self.run_cli(
                [
                    "--config-file",
                    config_file,
                    "list",
                    "--bookmark",
                    "sample_open",
                    "--no-default-bookmark",
                ]
            )
            self.assertEqual(result_bm.returncode, 0)
            self.assertIn("ACC=999999", result_bm.stdout)
            self.assertIn("Waiting on Customer Action Required", result_bm.stdout)
            self.assertIn("In Progress", result_bm.stdout)
        finally:
            import shutil

            shutil.rmtree(temp_home)

        # 4. Test statuses module helpers (determine_reply_status, choices, colors)
        from rh_support_lib.constants import COLORS
        from rh_support_lib.statuses import (
            determine_reply_status,
            get_status_choices_for_case,
            get_status_color,
        )

        self.assertEqual(
            determine_reply_status("Waiting on Customer Action Required"),
            "In Progress",
        )
        self.assertEqual(determine_reply_status("Waiting on Customer"), "In Progress")
        self.assertEqual(
            determine_reply_status("Waiting on Red Hat"),
            "Waiting on Red Hat",
        )
        self.assertEqual(
            determine_reply_status("In Progress"),
            "In Progress",
        )
        self.assertEqual(
            determine_reply_status("Waiting on Engineering"),
            "Waiting on Engineering",
        )
        self.assertEqual(determine_reply_status("Closed"), "Closed")
        self.assertIsNone(determine_reply_status(None))

        # Choices for standard case
        choices = get_status_choices_for_case("Waiting on Red Hat")
        self.assertIn(("Waiting on Red Hat", "Waiting on Red Hat"), choices)
        self.assertIn(
            (
                "Waiting on Customer Action Required",
                "Waiting on Customer Action Required",
            ),
            choices,
        )

        # Choices for custom/unusual status dynamically includes it
        custom_choices = get_status_choices_for_case("Custom Under Investigation")
        self.assertIn(
            ("Custom Under Investigation", "Custom Under Investigation"), custom_choices
        )

        # Colors
        self.assertEqual(get_status_color("Waiting on Red Hat"), COLORS.YELLOW)
        self.assertEqual(get_status_color("In Progress"), COLORS.YELLOW)
        self.assertEqual(
            get_status_color("Waiting on Customer Action Required"), COLORS.GREEN
        )
        self.assertEqual(get_status_color("Waiting on Engineering"), COLORS.MAGENTA)
        self.assertEqual(get_status_color("Closed"), COLORS.GREY)
        self.assertIsNone(get_status_color(None))

    def test_case_comments_auto_exhaustion_pagination(self):
        """Test that get_case_details automatically exhausts multi-page comments."""
        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.cases import CaseService

        api_client = LegacyAPIClient("mock_access_token_123")
        case_service = CaseService(api_client, graphql_url=f"{self.base_url}/graphql")

        # 1. Fetch case 888888 with all_comments=True (default) -> should fetch page 1 and page 2
        details = case_service.get_case_details(
            "888888", first_comments=1, all_comments=True
        )
        self.assertIsNotNone(details)
        self.assertEqual(len(details.comments), 2)
        self.assertEqual(details.comments[0].body, "Comment on page 1")
        self.assertEqual(details.comments[1].body, "Comment on page 2")

        # 2. Fetch case 888888 with all_comments=False, max_comments=1 -> should fetch only page 1
        details_single = case_service.get_case_details(
            "888888", first_comments=1, all_comments=False, max_comments=1
        )
        self.assertIsNotNone(details_single)
        self.assertEqual(len(details_single.comments), 1)
        self.assertEqual(details_single.comments[0].body, "Comment on page 1")

    def test_case_list_pagination_all_and_limit(self):
        """Test that list_cases and CLI --all and --limit handle multi-page case pagination."""
        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.cases import CaseService

        api_client = LegacyAPIClient("mock_access_token_123")
        case_service = CaseService(api_client, graphql_url=f"{self.base_url}/graphql")

        # 1. Direct CaseService list_cases with all_pages=True
        cases_all = case_service.list_cases(account="PAGINATE", all_pages=True)
        self.assertEqual(len(cases_all), 2)
        case_numbers = [c.case_number for c in cases_all]
        self.assertIn("CASE_PAGE1", case_numbers)
        self.assertIn("CASE_PAGE2", case_numbers)

        # 2. Direct CaseService list_cases with limit=1
        cases_limited = case_service.list_cases(account="PAGINATE", limit=1)
        self.assertEqual(len(cases_limited), 1)
        self.assertEqual(cases_limited[0].case_number, "CASE_PAGE1")

        # 3. CLI list --all
        res_cli_all = self.run_cli(
            ["--simple-output", "list", "--account", "PAGINATE", "--all"]
        )
        self.assertEqual(res_cli_all.returncode, 0)
        self.assertIn("CASE_PAGE1", res_cli_all.stdout)
        self.assertIn("CASE_PAGE2", res_cli_all.stdout)

        # 4. CLI list --limit 1
        res_cli_lim = self.run_cli(
            ["--simple-output", "list", "--account", "PAGINATE", "--limit", "1"]
        )
        self.assertEqual(res_cli_lim.returncode, 0)
        self.assertIn("CASE_PAGE1", res_cli_lim.stdout)
        self.assertNotIn("CASE_PAGE2", res_cli_lim.stdout)

    def test_active_products_pagination(self):
        """Test that get_active_products loops through pages when all_pages=True."""
        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.cases import CaseService

        api_client = LegacyAPIClient("mock_access_token_123")
        case_service = CaseService(api_client, graphql_url=f"{self.base_url}/graphql")

        # Request with first=1, all_pages=True -> mock returns page 1 (RHEL) then page 2 (OpenShift)
        products = case_service.get_active_products(first=1, all_pages=True)
        self.assertEqual(len(products), 2)
        product_names = [p.name for p in products]
        self.assertIn("Red Hat Enterprise Linux", product_names)
        self.assertIn("Red Hat OpenShift Container Platform", product_names)

        # Request with first=1, all_pages=False -> returns only page 1
        products_single = case_service.get_active_products(first=1, all_pages=False)
        self.assertEqual(len(products_single), 1)
        self.assertEqual(products_single[0].name, "Red Hat Enterprise Linux")

    def test_json_and_yaml_list_output(self):
        """Test listing cases in machine-readable JSON and YAML format without logging noise."""

        import yaml

        # 1. JSON output
        res_json = self.run_cli(["list", "--json"])
        self.assertEqual(res_json.returncode, 0)
        self.assertNotIn("Authenticating...", res_json.stdout)
        self.assertNotIn("Fetching cases...", res_json.stdout)
        data = json.loads(res_json.stdout)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        self.assertIn("case_number", data[0])
        self.assertIn("status", data[0])

        # 2. YAML output
        res_yaml = self.run_cli(["list", "--format", "yaml"])
        self.assertEqual(res_yaml.returncode, 0)
        self.assertNotIn("Authenticating...", res_yaml.stdout)
        data_yaml = yaml.safe_load(res_yaml.stdout)
        self.assertIsInstance(data_yaml, list)
        self.assertIn("case_number", data_yaml[0])

    def test_json_and_yaml_show_output(self):
        """Test showing case details in machine-readable JSON and YAML format."""

        import yaml

        # 1. JSON output
        res_json = self.run_cli(["show", "-c", "12345", "--json"])
        self.assertEqual(res_json.returncode, 0)
        self.assertNotIn("Authenticating...", res_json.stdout)
        self.assertNotIn("Fetching case", res_json.stdout)
        data = json.loads(res_json.stdout)
        self.assertIsInstance(data, dict)
        self.assertIn("case", data)
        self.assertIn("description", data)
        self.assertIn("comments", data)
        self.assertIn("attachments", data)
        self.assertEqual(data["case"]["case_number"], "12345")

        # 2. YAML output
        res_yaml = self.run_cli(["show", "-c", "12345", "--format", "yaml"])
        self.assertEqual(res_yaml.returncode, 0)
        data_yaml = yaml.safe_load(res_yaml.stdout)
        self.assertIsInstance(data_yaml, dict)
        self.assertIn("case", data_yaml)
        self.assertIn("comments", data_yaml)

    def test_case_details_cache_hit_and_lightweight_probe(self):
        """Test that repeated 'show' commands use the lightweight probe and serve from local cache."""
        initial_details_count = MockRedHatHandler.details_query_count
        initial_probe_count = MockRedHatHandler.probe_query_count

        # First call: cache miss, executes full details query
        res1 = self.run_cli(["show", "-c", "12345"])
        self.assertEqual(res1.returncode, 0)
        self.assertIn("12345", res1.stdout)
        self.assertEqual(
            MockRedHatHandler.details_query_count, initial_details_count + 1
        )

        # Verify cache file exists
        cached_file = os.path.join(self.case_cache().cases_dir, "12345.json")
        self.assertTrue(os.path.isfile(cached_file))

        # Second call: cache hit! Details query count does NOT increase, lightweight probe query count does
        res2 = self.run_cli(["show", "-c", "12345"])
        self.assertEqual(res2.returncode, 0)
        self.assertIn("12345", res2.stdout)
        self.assertEqual(
            MockRedHatHandler.details_query_count, initial_details_count + 1
        )
        self.assertEqual(MockRedHatHandler.probe_query_count, initial_probe_count + 1)

        # Third call with --no-cache: bypasses cache and increments details query count
        res3 = self.run_cli(["show", "-c", "12345", "--no-cache"])
        self.assertEqual(res3.returncode, 0)
        self.assertEqual(
            MockRedHatHandler.details_query_count, initial_details_count + 2
        )

    def test_case_list_cache_hit_and_no_cache(self):
        """Test that repeated 'list' commands hit local cache within TTL and --no-cache bypasses it."""
        initial_list_count = MockRedHatHandler.list_query_count

        # First call: cache miss
        res1 = self.run_cli(["list"])
        self.assertEqual(res1.returncode, 0)
        self.assertEqual(MockRedHatHandler.list_query_count, initial_list_count + 1)

        # Second call: hits cache (list_query_count remains identical)
        res2 = self.run_cli(["list"])
        self.assertEqual(res2.returncode, 0)
        self.assertEqual(MockRedHatHandler.list_query_count, initial_list_count + 1)

        # Third call with --no-cache: fetches from server
        res3 = self.run_cli(["list", "--no-cache"])
        self.assertEqual(res3.returncode, 0)
        self.assertEqual(MockRedHatHandler.list_query_count, initial_list_count + 2)

    def test_case_details_cache_revalidates_on_new_remote_comment(self):
        """Test that a new comment on the remote server triggers revalidation and updates cached case."""
        # 1. Warm cache
        res1 = self.run_cli(["show", "-c", "12345"])
        self.assertEqual(res1.returncode, 0)
        initial_details_count = MockRedHatHandler.details_query_count

        # 2. Server receives new comment_3
        MockRedHatHandler.latest_mock_comment_id = "comment_3"
        MockRedHatHandler.latest_mock_comment_date = "2026-10-09T10:00:00Z"

        # 3. Running show -c 12345 automatically detects the new comment and re-fetches!
        res2 = self.run_cli(["show", "-c", "12345"])
        self.assertEqual(res2.returncode, 0)
        self.assertEqual(
            MockRedHatHandler.details_query_count, initial_details_count + 1
        )


class TestCaseLookupQuery(unittest.TestCase):
    def test_lookup_query_uses_every_declared_variable(self):
        from rh_support_lib.services.cases import GQL_RESOLVE_CASE_LOOKUP_DATA

        signature, _, selection = GQL_RESOLVE_CASE_LOOKUP_DATA.partition("{")
        for variable in re.findall(r"\$(\w+)\s*:", signature):
            with self.subTest(variable=variable):
                self.assertRegex(selection, rf"\${variable}\b")
