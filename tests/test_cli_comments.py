"""Focused tests for case commenting, status updates, and markdown formatting."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.support.mock_server import MockRedHatHandler, MockServerTestCase


class TestCliComments(MockServerTestCase):
    def test_comment_from_file(self):
        """Test posting a comment from a file"""
        with open("test_comment.txt", "w") as f:
            f.write("This is a test comment body.")

        try:
            result = self.run_cli(
                [
                    "comment",
                    "-c",
                    "01234567",
                    "-f",
                    "test_comment.txt",
                    "-s",
                    "customer",
                ]
            )
            if result.returncode != 0:
                print("\nSTDOUT:", result.stdout)
                print("STDERR:", result.stderr)

            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Comment added.", result.stdout)
            self.assertIn(
                "Success: Case status updated to 'Waiting on Customer Action Required'.",
                result.stdout,
            )
        finally:
            if os.path.exists("test_comment.txt"):
                os.remove("test_comment.txt")

    def test_comment_update_status_only(self):
        """Test verifying the default status behavior or specific status flags"""
        # Note: The CLI currently requires a comment body even if just updating status?
        # Looking at code:
        #   if args.file: ...
        #   else: comment_body = open_editor(...)
        #   if not comment_body: abort
        # So we MUST provide a comment body to update status.

        with open("status_update.txt", "w") as f:
            f.write("Updating status.")

        try:
            result = self.run_cli(
                ["comment", "-c", "01234567", "-f", "status_update.txt", "-s", "closed"]
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Case status updated to 'Closed'.", result.stdout)
        finally:
            if os.path.exists("status_update.txt"):
                os.remove("status_update.txt")

    def test_comment_no_status_change_flag(self):
        """Test that --no-status-change and --keep-status post comments without altering case status."""
        with open("no_change_comment.txt", "w") as f:
            f.write("Informational update without status transition.")

        try:
            # 1. Test --no-status-change flag
            MockRedHatHandler.last_update_payload = None
            result1 = self.run_cli(
                [
                    "comment",
                    "-c",
                    "01234567",
                    "-f",
                    "no_change_comment.txt",
                    "--no-status-change",
                ]
            )
            self.assertEqual(result1.returncode, 0)
            self.assertIn("Success: Comment added.", result1.stdout)
            self.assertNotIn("Case status updated to", result1.stdout)
            self.assertIsNone(MockRedHatHandler.last_update_payload)

            # 2. Test --keep-status alias
            MockRedHatHandler.last_update_payload = None
            result2 = self.run_cli(
                [
                    "comment",
                    "-c",
                    "01234567",
                    "-f",
                    "no_change_comment.txt",
                    "--keep-status",
                ]
            )
            self.assertEqual(result2.returncode, 0)
            self.assertIn("Success: Comment added.", result2.stdout)
            self.assertNotIn("Case status updated to", result2.stdout)
            self.assertIsNone(MockRedHatHandler.last_update_payload)
        finally:
            if os.path.exists("no_change_comment.txt"):
                os.remove("no_change_comment.txt")

    def test_comment_status_none_or_keep(self):
        """Test that passing -s none or --status keep avoids changing case status."""
        with open("status_none_comment.txt", "w") as f:
            f.write("Update with explicit status none.")

        try:
            # 1. -s none
            MockRedHatHandler.last_update_payload = None
            result1 = self.run_cli(
                [
                    "comment",
                    "-c",
                    "01234567",
                    "-f",
                    "status_none_comment.txt",
                    "-s",
                    "none",
                ]
            )
            self.assertEqual(result1.returncode, 0)
            self.assertIn("Success: Comment added.", result1.stdout)
            self.assertNotIn("Case status updated to", result1.stdout)
            self.assertIsNone(MockRedHatHandler.last_update_payload)

            # 2. --status keep
            MockRedHatHandler.last_update_payload = None
            result2 = self.run_cli(
                [
                    "comment",
                    "-c",
                    "01234567",
                    "-f",
                    "status_none_comment.txt",
                    "--status",
                    "keep",
                ]
            )
            self.assertEqual(result2.returncode, 0)
            self.assertIn("Success: Comment added.", result2.stdout)
            self.assertNotIn("Case status updated to", result2.stdout)
            self.assertIsNone(MockRedHatHandler.last_update_payload)
        finally:
            if os.path.exists("status_none_comment.txt"):
                os.remove("status_none_comment.txt")

    def test_comment_default_does_not_switch_status_on_waiting_on_red_hat(self):
        """Verify default comment submission does NOT mutate status when case is Waiting on Red Hat."""
        test_file = "redhat_status_comment.txt"
        with open(test_file, "w") as f:
            f.write("Informational update on case already waiting on Red Hat.")

        try:
            MockRedHatHandler.last_update_payload = None
            MockRedHatHandler.mock_case_statuses["01234567"] = "Waiting on Red Hat"
            result = self.run_cli(["comment", "-c", "01234567", "-f", test_file])
            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Comment added.", result.stdout)
            self.assertNotIn("Case status updated to", result.stdout)
            self.assertIsNone(MockRedHatHandler.last_update_payload)
        finally:
            MockRedHatHandler.mock_case_statuses.pop("01234567", None)
            if os.path.exists(test_file):
                os.remove(test_file)

    def test_comment_default_switches_status_when_waiting_on_customer(self):
        """Verify default comment submission transitions to In Progress when case is Waiting on Customer."""
        test_file = "customer_reply_comment.txt"
        with open(test_file, "w") as f:
            f.write("Customer replying with requested diagnostic info.")

        try:
            MockRedHatHandler.last_update_payload = None
            MockRedHatHandler.mock_case_statuses["01234567"] = (
                "Waiting on Customer Action Required"
            )
            result = self.run_cli(["comment", "-c", "01234567", "-f", test_file])
            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Comment added.", result.stdout)
            self.assertIn(
                "Success: Case status updated to 'In Progress'.", result.stdout
            )
            self.assertIsNotNone(MockRedHatHandler.last_update_payload)
            patch = MockRedHatHandler.last_update_payload.get("RedHatSupportCase", {})
            self.assertEqual(patch.get("Status"), "In Progress")
        finally:
            MockRedHatHandler.mock_case_statuses.pop("01234567", None)
            if os.path.exists(test_file):
                os.remove(test_file)

    def test_comment_default_does_not_switch_status_on_waiting_on_engineering(
        self,
    ):
        """Verify default comment submission does NOT mutate status when case is Waiting on Engineering."""
        test_file = "engineering_comment.txt"
        with open(test_file, "w") as f:
            f.write("Customer following up on engineering case.")

        try:
            MockRedHatHandler.last_update_payload = None
            MockRedHatHandler.mock_case_statuses["01234567"] = "Waiting on Engineering"
            result = self.run_cli(["comment", "-c", "01234567", "-f", test_file])
            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Comment added.", result.stdout)
            self.assertNotIn("Case status updated to", result.stdout)
            self.assertIsNone(MockRedHatHandler.last_update_payload)
        finally:
            MockRedHatHandler.mock_case_statuses.pop("01234567", None)
            if os.path.exists(test_file):
                os.remove(test_file)

    def test_comment_default_does_not_switch_status_on_closed(self):
        """Verify default comment submission does NOT mutate status when case is Closed."""
        test_file = "closed_comment.txt"
        with open(test_file, "w") as f:
            f.write("Customer adding follow-up note on closed case.")

        try:
            MockRedHatHandler.last_update_payload = None
            MockRedHatHandler.mock_case_statuses["01234567"] = "Closed"
            result = self.run_cli(["comment", "-c", "01234567", "-f", test_file])
            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Comment added.", result.stdout)
            self.assertNotIn("Case status updated to", result.stdout)
            self.assertIsNone(MockRedHatHandler.last_update_payload)
        finally:
            MockRedHatHandler.mock_case_statuses.pop("01234567", None)
            if os.path.exists(test_file):
                os.remove(test_file)

    def test_close_case_command(self):
        """Test close subcommand: posts closing comment and sets status to Closed"""
        with open("close_comment.txt", "w") as f:
            f.write("Closing case, issue is resolved.")
        try:
            result = self.run_cli(
                ["close", "-c", "01234567", "-f", "close_comment.txt"]
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Comment added.", result.stdout)
            self.assertIn("Success: Case status updated to 'Closed'.", result.stdout)
        finally:
            if os.path.exists("close_comment.txt"):
                os.remove("close_comment.txt")

    def test_comment_from_template(self):
        """Test posting a comment using a template with dynamic variables and status overrides"""

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        self.env["HOME"] = temp_home

        try:
            # Create templates directory
            templates_dir = os.path.join(
                temp_home, ".config", "rh-support-cli", "templates"
            )
            os.makedirs(templates_dir, exist_ok=True)

            # Write template file
            template_path = os.path.join(templates_dir, "ping_case.yaml")
            with open(template_path, "w") as f:
                f.write(
                    "status: customer\n"
                    "comment: |\n"
                    "  Ping support for {{ environment }} environment by {{ user_name }}.\n"
                )

            # Execute comment using template
            result = self.run_cli(
                [
                    "comment",
                    "-c",
                    "01234567",
                    "-t",
                    "ping_case",
                    "--template-var",
                    "environment=Production",
                    "--template-var",
                    "user_name=Alice",
                ]
            )

            if result.returncode != 0:
                print("\nSTDOUT:", result.stdout)
                print("STDERR:", result.stderr)

            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Comment added.", result.stdout)
            self.assertIn(
                "Success: Case status updated to 'Waiting on Customer Action Required'.",
                result.stdout,
            )
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            else:
                del self.env["HOME"]
            shutil.rmtree(temp_home)

    def test_comment_failure_preservation(self):
        """Test that the temporary comment file is preserved if the API call fails"""
        # 1. Create a fake editor script (shell script)
        fake_editor_path = os.path.abspath("fake_editor.sh")
        with open(fake_editor_path, "w") as f:
            f.write("#!/bin/sh\n")
            f.write('echo "Preserved Comment Body" >> "$1"\n')

        os.chmod(fake_editor_path, 0o755)

        # 2. Set EDITOR to the fake script
        env_with_editor = self.env.copy()
        env_with_editor["EDITOR"] = fake_editor_path

        try:
            # 3. Run comment command on a failing case ID
            cmd = [sys.executable, self.cli_path, "comment", "-c", "FAIL_COMMENT"]
            result = subprocess.run(
                cmd, env=env_with_editor, capture_output=True, text=True, check=False
            )

            # 4. Expect failure
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("Comment submission was not confirmed", result.stderr)

            # 5. Extract path and verify existence
            import re

            match = re.search(
                r"Comment recovery copy preserved at: (.+)", result.stderr
            )
            self.assertTrue(
                match, f"Could not find preserved file path in output: {result.stderr}"
            )
            preserved_path = match.group(1).strip()

            self.assertTrue(
                os.path.exists(preserved_path), "Preserved file does not exist"
            )

            # 6. Verify content
            with open(preserved_path, "r") as f:
                content = f.read()
            self.assertIn("Preserved Comment Body", content)

            # Cleanup
            if os.path.exists(preserved_path):
                os.remove(preserved_path)

        finally:
            if os.path.exists(fake_editor_path):
                os.remove(fake_editor_path)

    def test_comment_with_context(self):
        """Test that previous comments are included in the editor context"""
        # 1. Create a fake editor script to capture the file content
        fake_editor_path = os.path.abspath("context_check_editor.sh")
        captured_content_path = os.path.abspath("captured_content.txt")

        with open(fake_editor_path, "w") as f:
            f.write("#!/bin/sh\n")
            f.write(f'cat "$1" > {captured_content_path}\n')
            # Write a dummy comment so it doesn't abort
            f.write('echo "New Comment" > "$1"\n')

        os.chmod(fake_editor_path, 0o755)

        env_with_editor = self.env.copy()
        env_with_editor["EDITOR"] = fake_editor_path

        try:
            # Test Case 1: Default behavior (include 1 comment)
            # We need to target a case ID that returns comments. Mock handles any ID but checks paths.
            # /cases/12345/comments returns 2 comments in mock.
            cmd = [sys.executable, self.cli_path, "comment", "-c", "12345"]
            result = subprocess.run(
                cmd, env=env_with_editor, capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0)

            with open(captured_content_path, "r") as f:
                content = f.read()

            self.assertIn("--- PREVIOUS COMMENTS ---", content)
            self.assertIn("Comment 2", content)  # Last comment
            self.assertNotIn(
                "Comment 1", content
            )  # First comment (should be excluded by default limit 1)

            # Test Case 2: Include 2 comments
            cmd = [
                sys.executable,
                self.cli_path,
                "comment",
                "-c",
                "12345",
                "--include-previous-comments",
                "2",
            ]
            result = subprocess.run(
                cmd, env=env_with_editor, capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0)

            with open(captured_content_path, "r") as f:
                content = f.read()

            self.assertIn("Comment 1", content)
            self.assertIn("Comment 2", content)

            # Test Case 3: Disable comments
            cmd = [
                sys.executable,
                self.cli_path,
                "comment",
                "-c",
                "12345",
                "--include-previous-comments",
                "0",
            ]
            result = subprocess.run(
                cmd, env=env_with_editor, capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0)

            with open(captured_content_path, "r") as f:
                content = f.read()
            self.assertNotIn("--- PREVIOUS COMMENTS ---", content)

        finally:
            if os.path.exists(fake_editor_path):
                os.remove(fake_editor_path)
            if os.path.exists(captured_content_path):
                os.remove(captured_content_path)

    def test_open_editor_stripping(self):
        """Test that open_editor correctly strips only leading comments"""
        from rh_support_lib.utils import open_editor

        fake_editor_path = os.path.abspath("stripping_editor.sh")
        with open(fake_editor_path, "w") as f:
            f.write("#!/bin/sh\n")
            f.write('echo "Real Comment" >> "$1"\n')
            f.write('echo "# Indented Code" >> "$1"\n')

        os.chmod(fake_editor_path, 0o755)

        with mock.patch.dict(os.environ, {"EDITOR": fake_editor_path}):
            try:
                content, path = open_editor(
                    "123", "Red Hat", header_content="Previous\nComment"
                )

                # Verify content
                self.assertTrue(content.startswith("Real Comment"))
                self.assertIn(
                    "# Indented Code", content
                )  # Should preserve this as it's after content
                self.assertNotIn("# Enter comment", content)  # Should strip header

            finally:
                if "path" in locals() and os.path.exists(path):
                    os.remove(path)
                if os.path.exists(fake_editor_path):
                    os.remove(fake_editor_path)

    def test_file_comment_stripping(self):
        """Test that cmd_comment strips headers when reading from a file"""
        # Create a file with a header and content
        file_path = "retry_comment.tmp"
        with open(file_path, "w") as f:
            f.write("# Header 1\n")
            f.write("# Header 2\n")
            f.write(
                "\n"
            )  # Empty line is NOT a comment, so it should STOP stripping here
            f.write("Actual Content\n")
            f.write("# Not a header\n")

        try:
            # We don't need to run the full CLI to test the library logic if we want to be fast,
            # but running the CLI as a subprocess confirms the integration.
            # Using a mock server is already set up.

            result = self.run_cli(["comment", "-c", "12345", "--file", file_path])
            self.assertEqual(result.returncode, 0)

            payload = MockRedHatHandler.last_comment_payload
            self.assertIsNotNone(payload)
            body = payload.get("commentBody") or payload.get("body", "")

            from rh_support_lib.utils import clean_comment_body

            self.assertIn("Header 1", body)
            self.assertIn("Header 2", body)
            self.assertEqual(
                clean_comment_body(body),
                "# Header 1\n\n# Header 2\n\nActual Content\n\n# Not a header",
            )
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    def test_comment_with_edit_flag(self):
        """Test that --edit opens editor with file content"""
        fake_editor_path = os.path.abspath("edit_flag_editor.sh")
        captured_content_path = os.path.abspath("captured_edit_content.txt")
        file_input_path = os.path.abspath("input_for_edit.txt")

        # 1. Create input file
        with open(file_input_path, "w") as f:
            f.write("# Ignored Header\n")
            f.write("Initial Content\n")

        # 2. Fake editor: Cat file to capture path, verify content, then write new content
        with open(fake_editor_path, "w") as f:
            f.write("#!/bin/sh\n")
            f.write(f'cat "$1" > {captured_content_path}\n')
            # Simulate user editing the content
            f.write('echo "Edited Content" > "$1"\n')

        os.chmod(fake_editor_path, 0o755)

        env_with_editor = self.env.copy()
        env_with_editor["EDITOR"] = fake_editor_path

        try:
            # Run with --file and --edit
            cmd = [
                sys.executable,
                self.cli_path,
                "comment",
                "-c",
                "12345",
                "--file",
                file_input_path,
                "--edit",
            ]
            result = subprocess.run(
                cmd, env=env_with_editor, capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0)

            # Verify editor was opened with stripped content
            with open(captured_content_path, "r") as f:
                content = f.read()
            self.assertIn("Initial Content", content)
            self.assertIn(
                "# Ignored Header", content
            )  # Preserved as legitimate Markdown heading in imported file
            self.assertIn(
                "# Enter comment", content
            )  # New headers added by open_editor

            # Verify payload submitted was the EDITED content
            from rh_support_lib.utils import clean_comment_body

            payload = MockRedHatHandler.last_comment_payload
            self.assertEqual(
                clean_comment_body(payload.get("commentBody")), "Edited Content"
            )

        finally:
            if os.path.exists(fake_editor_path):
                os.remove(fake_editor_path)
            if os.path.exists(captured_content_path):
                os.remove(captured_content_path)
            if os.path.exists(file_input_path):
                os.remove(file_input_path)

    def test_clean_comment_body_and_private_detection(self):
        """Test cleaning wrapping legacy divs, HTML to markdown, and private comment detection"""
        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.cases import CaseService
        from rh_support_lib.utils import clean_comment_body

        # 1. Direct clean_comment_body tests
        # Plaintext in legacy-comment div
        c1 = '<div class="legacy-comment legacy-comment-plaintext">NetworkPolicy: https://access.redhat.com/solutions/7101361</div>'
        self.assertEqual(
            clean_comment_body(c1),
            "NetworkPolicy: https://access.redhat.com/solutions/7101361",
        )

        # Arbitrary div classes and attributes
        c2 = '<div class="custom-note extra" id="c99">Important information</div>'
        self.assertEqual(clean_comment_body(c2), "Important information")

        # HTML formatted legacy comment (like Case 999888 Comment 1)
        c3 = (
            "&lt;div class=&quot;legacy-comment legacy-comment-markdown&quot;&gt;"
            "&lt;h2 id=&quot;consultant-engaged&quot;&gt;Consultant Engaged&lt;/h2&gt;\n"
            "&lt;p&gt;This case has been flagged with Consulting Engagement.&lt;/p&gt;\n"
            "&lt;h3&gt;Contact:&lt;/h3&gt;\n"
            "&lt;p&gt;&lt;strong&gt;Name:&lt;/strong&gt; Jane Consultant&lt;br&gt;"
            "&lt;a href=&quot;mailto:consultant@example.com&quot;&gt;consultant@example.com&lt;/a&gt;&lt;/p&gt;\n"
            "&lt;/div&gt;"
        )
        cleaned3 = clean_comment_body(c3)
        self.assertNotIn("<div", cleaned3)
        self.assertNotIn("</div>", cleaned3)
        self.assertIn("## Consultant Engaged", cleaned3)
        self.assertIn("### Contact:", cleaned3)
        self.assertIn("**Name:**", cleaned3)
        self.assertIn("consultant@example.com", cleaned3)

        # Double-escaped entities
        c4 = "&lt;div class=&quot;legacy-comment legacy-comment-plaintext&quot;&gt;&amp;gt; quoted text&lt;/div&gt;"
        self.assertEqual(clean_comment_body(c4), "> quoted text")

        # 2. Private comment detection in CaseService
        api_client = LegacyAPIClient("mock_access_token_123")
        case_service = CaseService(api_client, graphql_url=f"{self.base_url}/graphql")

        # CaseService._get_case_details_graphql with internal note markers
        mock_gql_data = {
            "redhat_support_uiapi": {
                "query": {
                    "RedHatSupportCase": {
                        "edges": [
                            {
                                "node": {
                                    "Id": "500TestGql",
                                    "CaseNumber__c": {"value": "998877"},
                                    "Subject": {"value": "GQL Comment Privacy Test"},
                                    "CaseComments__r": {
                                        "pageInfo": {"hasNextPage": False},
                                        "edges": [
                                            {
                                                "node": {
                                                    "Id": "c_pub",
                                                    "Body__c": {
                                                        "value": '<div class="legacy-comment legacy-comment-plaintext">Hello customer</div>'
                                                    },
                                                    "Visibility__c": {
                                                        "value": "Public",
                                                        "displayValue": "Public",
                                                    },
                                                    "CreatedBy": {
                                                        "Name": {
                                                            "value": "Support Specialist"
                                                        }
                                                    },
                                                    "CreatedDate": {
                                                        "value": "2026-01-01T10:00:00Z"
                                                    },
                                                }
                                            },
                                            {
                                                "node": {
                                                    "Id": "c_priv_marker",
                                                    "Body__c": {
                                                        "value": '<div class="legacy-comment legacy-comment-plaintext">/* INTERNAL NOTE */\n\nTaking ownership</div>'
                                                    },
                                                    "Visibility__c": {
                                                        "value": "Private",
                                                        "displayValue": "Private",
                                                    },
                                                    "CreatedBy": {
                                                        "Name": {
                                                            "value": "Alex Support"
                                                        }
                                                    },
                                                    "CreatedDate": {
                                                        "value": "2026-01-01T11:00:00Z"
                                                    },
                                                }
                                            },
                                            {
                                                "node": {
                                                    "Id": "c_priv_no_marker",
                                                    "Body__c": {
                                                        "value": '<div class="legacy-comment legacy-comment-plaintext">NetworkPolicy: https://access.redhat.com/solutions/7101361</div>'
                                                    },
                                                    "Visibility__c": {
                                                        "value": "Private",
                                                        "displayValue": "Private",
                                                    },
                                                    "CreatedBy": {
                                                        "Name": {
                                                            "value": "Jane Consultant"
                                                        }
                                                    },
                                                    "CreatedDate": {
                                                        "value": "2026-01-01T12:00:00Z"
                                                    },
                                                }
                                            },
                                        ],
                                    },
                                }
                            }
                        ]
                    }
                }
            }
        }

        from unittest.mock import patch

        with patch.object(api_client, "execute_graphql", return_value=mock_gql_data):
            details = case_service.get_case_details("998877")
            self.assertIsNotNone(details)
            self.assertEqual(len(details.comments), 3)

            # Comment 1: Public, stripped div, author from CreatedBy
            self.assertTrue(details.comments[0].is_public)
            self.assertEqual(details.comments[0].created_by, "Support Specialist")
            self.assertEqual(details.comments[0].body, "Hello customer")
            self.assertEqual(details.comments[0].visibility, "Public")

            # Comment 2: Private via Visibility__c field
            self.assertFalse(details.comments[1].is_public)
            self.assertEqual(details.comments[1].created_by, "Alex Support")
            self.assertEqual(details.comments[1].visibility, "Private")
            self.assertNotIn("<div", details.comments[1].body)
            self.assertIn("Taking ownership", details.comments[1].body)

            # Comment 3: Private via Visibility__c even with NO internal marker in text
            self.assertFalse(details.comments[2].is_public)
            self.assertEqual(details.comments[2].created_by, "Jane Consultant")
            self.assertEqual(details.comments[2].visibility, "Private")
            self.assertEqual(
                details.comments[2].body,
                "NetworkPolicy: https://access.redhat.com/solutions/7101361",
            )

    def test_show_case_comment_ordering_and_newest_first(self):
        """Test comment ordering flags (--newest-first, --order, --oldest-first) and config defaults."""
        import shutil

        import yaml

        from rh_support_lib.services.state import StateService

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        orig_state = self.env.get("XDG_STATE_HOME")
        self.env["HOME"] = temp_home
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_home, ".config")
        self.env["XDG_STATE_HOME"] = os.path.join(temp_home, ".local", "state")

        try:
            # Setup tracking with older checkpoint on case 1001 so some comments are unread
            state_path = os.path.join(
                temp_home, ".local", "state", "rh-support-cli", "state.yaml"
            )
            user = self.env.get("REDHAT_SUPPORT_USERNAME") or self.env.get(
                "USER", "jdeveloper"
            )
            svc = StateService(state_file=state_path)
            svc.add_tracked_case(
                user, "1001", relationship="followed", summary="Test Case"
            )
            state_data = svc.load_state()
            u_state = svc._get_user_state(state_data, user)
            u_state["tracked_cases"]["1001"]["read_checkpoint"] = {
                "last_read_comment_date": "2023-01-02T10:00:00Z",
                "last_read_comment_id": "comment_1",
                "status": "Waiting on Red Hat",
                "marked_read_at": "2023-01-02T10:00:00Z",
            }
            svc.save_state(state_data)

            # 1. Default (newest-first, matches portal default)
            res_default = self.run_cli(
                ["show", "-c", "1001", "--simple-output", "--no-pager"]
            )
            self.assertEqual(res_default.returncode, 0)
            self.assertIn("COMMENTS (2) [newest first]:", res_default.stdout)
            self.assertIn("▲ 1 unread above", res_default.stdout)

            # Verify comment order: in newest-first default, Comment 2 comes before Comment 1
            idx_c2 = res_default.stdout.find("Comment 2")
            idx_c1 = res_default.stdout.find("Comment 1")
            self.assertTrue(idx_c2 != -1 and idx_c1 != -1)
            self.assertLess(idx_c2, idx_c1)

            # 2. Flag --oldest-first: comments chronological and divider points below
            res_oldest = self.run_cli(
                [
                    "show",
                    "-c",
                    "1001",
                    "--oldest-first",
                    "--simple-output",
                    "--no-pager",
                ]
            )
            self.assertEqual(res_oldest.returncode, 0)
            self.assertIn("COMMENTS (2) [oldest first]:", res_oldest.stdout)
            self.assertIn("▼ 1 unread below", res_oldest.stdout)
            idx_old_c1 = res_oldest.stdout.find("Comment 1")
            idx_old_c2 = res_oldest.stdout.find("Comment 2")
            self.assertTrue(idx_old_c1 != -1 and idx_old_c2 != -1)
            self.assertLess(idx_old_c1, idx_old_c2)

            # 3. Flag --order asc: same as oldest-first
            res_asc = self.run_cli(
                [
                    "show",
                    "-c",
                    "1001",
                    "--order",
                    "asc",
                    "--simple-output",
                    "--no-pager",
                ]
            )
            self.assertEqual(res_asc.returncode, 0)
            self.assertIn("COMMENTS (2) [oldest first]:", res_asc.stdout)

            # 4. JSON format with default order
            res_json = self.run_cli(["show", "-c", "1001", "--format", "json"])
            self.assertEqual(res_json.returncode, 0)
            parsed = json.loads(res_json.stdout)
            self.assertEqual(parsed.get("order"), "newest")
            self.assertIn("Comment 2", parsed["comments"][0]["body"])

            # 5. Config file default comments_order: oldest
            os.makedirs(
                os.path.join(temp_home, ".config", "rh-support-cli"), exist_ok=True
            )
            config_file = os.path.join(
                temp_home, ".config", "rh-support-cli", "config.yaml"
            )
            with open(config_file, "w") as f:
                yaml.safe_dump({"comments_order": "oldest"}, f)

            res_cfg = self.run_cli(
                ["show", "-c", "1001", "--simple-output", "--no-pager"]
            )
            self.assertEqual(res_cfg.returncode, 0)
            self.assertIn("COMMENTS (2) [oldest first]:", res_cfg.stdout)

            # Explicit --newest-first overrides config default
            res_override = self.run_cli(
                [
                    "show",
                    "-c",
                    "1001",
                    "--newest-first",
                    "--simple-output",
                    "--no-pager",
                ]
            )
            self.assertEqual(res_override.returncode, 0)
            self.assertIn("COMMENTS (2) [newest first]:", res_override.stdout)
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            if orig_state:
                self.env["XDG_STATE_HOME"] = orig_state
            shutil.rmtree(temp_home)

    def test_case_cache_invalidation_on_comment(self):
        """Test that commenting on a case invalidates its cached details."""
        # 1. Warm cache
        res_show1 = self.run_cli(["show", "-c", "12345"])
        self.assertEqual(res_show1.returncode, 0)
        cached_file = os.path.join(self.case_cache().cases_dir, "12345.json")
        self.assertTrue(os.path.isfile(cached_file))

        # 2. Add comment
        comment_file = os.path.join(self.test_cache_dir, "c.txt")
        with open(comment_file, "w") as f:
            f.write("New test comment text")
        res_comm = self.run_cli(["comment", "-c", "12345", "-f", comment_file])
        self.assertEqual(res_comm.returncode, 0)

        # Cache file should be removed upon mutation!
        self.assertFalse(os.path.isfile(cached_file))

    def test_is_comment_newer_iso_parsing(self):
        """Test ISO 8601 parsing across various timezone and fractional second formats."""
        from rh_support_lib.utils import is_comment_newer, parse_iso_datetime

        # 1. Parsing ISO formats
        dt_z = parse_iso_datetime("2026-10-12T08:00:00Z")
        dt_ms_z = parse_iso_datetime("2026-10-12T08:00:00.123Z")
        dt_offset = parse_iso_datetime("2026-10-12T08:00:00.123+0000")
        dt_colon = parse_iso_datetime("2026-10-12T08:00:00.123+00:00")

        self.assertIsNotNone(dt_z)
        self.assertIsNotNone(dt_ms_z)
        self.assertIsNotNone(dt_offset)
        self.assertIsNotNone(dt_colon)

        self.assertEqual(dt_ms_z, dt_offset)
        self.assertEqual(dt_ms_z, dt_colon)
        self.assertTrue(dt_ms_z > dt_z)

        # 2. is_comment_newer comparing different ISO timestamp formats
        # Comment with fractional seconds is newer than checkpoint without fractional seconds
        self.assertTrue(
            is_comment_newer(
                created_date="2026-10-12T08:00:00.123Z",
                comment_id="c_new",
                checkpoint_date="2026-10-12T08:00:00Z",
                checkpoint_id="c_old",
            )
        )
        self.assertTrue(
            is_comment_newer(
                created_date="2026-10-12T08:00:00.123+0000",
                comment_id="c_new",
                checkpoint_date="2026-10-12T08:00:00Z",
                checkpoint_id="c_old",
            )
        )
        # Older comment is not newer
        self.assertFalse(
            is_comment_newer(
                created_date="2026-10-12T07:59:59.999Z",
                comment_id="c_old",
                checkpoint_date="2026-10-12T08:00:00Z",
                checkpoint_id="c_cur",
            )
        )
        # Empty checkpoint date and id -> all comments are unread
        self.assertTrue(
            is_comment_newer(
                created_date="2026-10-12T08:00:00Z",
                comment_id="c_1",
                checkpoint_date="",
                checkpoint_id="",
            )
        )
        # Checkpoint id comparison when date is absent
        self.assertTrue(
            is_comment_newer(
                created_date="",
                comment_id="c_2",
                checkpoint_date="",
                checkpoint_id="c_1",
            )
        )
        self.assertFalse(
            is_comment_newer(
                created_date="",
                comment_id="c_1",
                checkpoint_date="",
                checkpoint_id="c_1",
            )
        )

    def test_comment_close_create_formatting_modes(self):
        """Test --format options (markdown, plain, html, raw) on comment, close, and create."""
        # 1. Test comment with default (markdown): soft breaks and code blocks
        with open("test_md_comment.txt", "w") as f:
            f.write("Line 1\nLine 2\n\n```bash\necho hello\n```")

        try:
            res_md = self.run_cli(
                ["comment", "-c", "12345", "-f", "test_md_comment.txt"]
            )
            self.assertEqual(res_md.returncode, 0)
            payload_md = MockRedHatHandler.last_comment_payload
            self.assertIn("<p>Line 1<br />\nLine 2</p>", payload_md["commentBody"])
            self.assertIn("<pre><code", payload_md["commentBody"])

            # 2. Test comment with --format plain: escapes entities and wraps lines in <p> and <br />
            with open("test_plain_comment.txt", "w") as f:
                f.write("Plain line 1\nPlain line 2\n\nParagraph 2 with <tag>")

            res_plain = self.run_cli(
                [
                    "comment",
                    "-c",
                    "12345",
                    "-f",
                    "test_plain_comment.txt",
                    "--format",
                    "plain",
                ]
            )
            self.assertEqual(res_plain.returncode, 0)
            payload_plain = MockRedHatHandler.last_comment_payload
            self.assertIn(
                "<p>Plain line 1<br />Plain line 2</p>", payload_plain["commentBody"]
            )
            self.assertIn("&lt;tag&gt;", payload_plain["commentBody"])

            # 3. Test comment with --format raw: sends verbatim string
            res_raw = self.run_cli(
                [
                    "comment",
                    "-c",
                    "12345",
                    "-f",
                    "test_plain_comment.txt",
                    "--format",
                    "raw",
                ]
            )
            self.assertEqual(res_raw.returncode, 0)
            payload_raw = MockRedHatHandler.last_comment_payload
            self.assertEqual(
                payload_raw["commentBody"],
                "Plain line 1\nPlain line 2\n\nParagraph 2 with <tag>",
            )

            # 4. Test comment with --format html: sends verbatim HTML
            with open("test_html_comment.txt", "w") as f:
                f.write("<p>Custom HTML</p><br/>")
            res_html = self.run_cli(
                [
                    "comment",
                    "-c",
                    "12345",
                    "-f",
                    "test_html_comment.txt",
                    "--format",
                    "html",
                ]
            )
            self.assertEqual(res_html.returncode, 0)
            payload_html = MockRedHatHandler.last_comment_payload
            self.assertEqual(payload_html["commentBody"], "<p>Custom HTML</p><br/>")

            # 5. Test close with --format plain
            res_close = self.run_cli(
                [
                    "close",
                    "-c",
                    "12345",
                    "-f",
                    "test_plain_comment.txt",
                    "--format",
                    "plain",
                ]
            )
            self.assertEqual(res_close.returncode, 0)
            payload_close = MockRedHatHandler.last_comment_payload
            self.assertIn(
                "<p>Plain line 1<br />Plain line 2</p>", payload_close["commentBody"]
            )

            # 6. Test create with --format markdown (default)
            with open("test_create_desc.txt", "w") as f:
                f.write("Description line 1\nDescription line 2")

            res_create = self.run_cli(
                [
                    "create",
                    "--summary",
                    "Formatted Case",
                    "--product",
                    "RHEL",
                    "--version",
                    "9.0",
                    "--description-file",
                    "test_create_desc.txt",
                    "--severity",
                    "Normal",
                    "--type",
                    "Standard",
                ],
                input="y\n",
            )
            self.assertEqual(res_create.returncode, 0)
            payload_create = MockRedHatHandler.last_case_payload
            self.assertIn(
                "<p>Description line 1<br />\nDescription line 2</p>",
                payload_create.get("Description", ""),
            )
        finally:
            for p in (
                "test_md_comment.txt",
                "test_plain_comment.txt",
                "test_html_comment.txt",
                "test_create_desc.txt",
            ):
                if os.path.exists(p):
                    os.remove(p)


class TestCommentContentFidelity(unittest.TestCase):
    def test_literal_markup_and_entities_in_html(self):
        from rh_support_lib.utils import clean_comment_body

        for source, expected in (
            (
                "<pre><code>&lt;root&gt;value&lt;/root&gt;</code></pre>",
                "```\n<root>value</root>\n```",
            ),
            ("<p>Use <code>&lt;b&gt;x&lt;/b&gt;</code></p>", "Use `<b>x</b>`"),
            ("<pre><code>&amp;lt;root&amp;gt;</code></pre>", "```\n&lt;root&gt;\n```"),
            ("<p>Literal &lt;b&gt;text&lt;/b&gt;</p>", "Literal <b>text</b>"),
            ("<div>&lt;b&gt;text&lt;/b&gt;</div>", "<b>text</b>"),
        ):
            with self.subTest(source=source):
                self.assertEqual(clean_comment_body(source), expected)

    def test_preformatted_whitespace_and_markdown_roundtrip(self):
        from rh_support_lib.utils import clean_comment_body, format_body_for_portal

        source = '<root attr="one & two">\n\n\n  <child/>\n</root>'
        markdown = "```\n" + source + "\n```"
        self.assertEqual(clean_comment_body(format_body_for_portal(markdown)), markdown)
        self.assertEqual(clean_comment_body("plain\n\n\ntext"), "plain\n\n\ntext")
        self.assertEqual(
            clean_comment_body(
                "<p>before</p>\n\n\n<pre><code>first\n\n\nlast\n</code></pre><p>after</p>"
            ),
            "before\n\n```\nfirst\n\n\nlast\n```\n\nafter",
        )

    def test_transport_encoded_html_preserves_code(self):
        import html

        from rh_support_lib.utils import clean_comment_body

        source = "<div><pre><code>&lt;root/&gt;</code></pre></div>"
        for depth in range(3):
            with self.subTest(depth=depth):
                self.assertEqual(clean_comment_body(source), "```\n<root/>\n```")
            source = html.escape(source)
