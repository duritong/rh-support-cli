"""Core integration tests for templates, creation, search, and TUI."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from rich.text import Text

from tests.support.mock_server import MockRedHatHandler, MockServerTestCase


class TestRhSupportCli(MockServerTestCase):
    def test_create_case(self):
        """Test creating a new case with flags and attachments"""
        MockRedHatHandler.lookup_product_version = "8.0"
        # Create dummy files
        with open("desc.txt", "w") as f:
            f.write("My Issue Description")
        with open("att1.txt", "w") as f:
            f.write("Attachment 1")
        with open("att2.txt", "w") as f:
            f.write("Attachment 2")

        try:
            # We need to simulate 'y' for the confirmation prompt
            # input="\ny\n" should handle the prompt "Submit this case? (y/n/edit) [y]: "
            cmd = [
                sys.executable,
                self.cli_path,
                "create",
                "--product",
                "RHEL",
                "--version",
                "8.0",
                "--summary",
                "Test Case Summary",
                "--description-file",
                "desc.txt",
                "--severity",
                "Low",
                "--type",
                "Standard",
                "--attachment",
                "att1.txt",
                "--attachment",
                "att2.txt",
            ]

            result = subprocess.run(
                cmd,
                env=self.env,
                capture_output=True,
                text=True,
                input="y\n",
                check=False,
            )

            if result.returncode != 0:
                print("\nSTDOUT:", result.stdout)
                print("STDERR:", result.stderr)

            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Case #20230001 created.", result.stdout)
            self.assertIn("att1.txt: Uploaded", result.stdout)
            self.assertIn("att2.txt: Uploaded", result.stdout)
            self.assertIn("Creating case...", result.stderr)
            self.assertIn("Submit this case?", result.stderr)
            self.assertNotIn("Creating case...", result.stdout)
            self.assertNotIn("Submit this case?", result.stdout)
        finally:
            for f in ["desc.txt", "att1.txt", "att2.txt"]:
                if os.path.exists(f):
                    os.remove(f)

    def test_create_lookup_failure_does_not_mutate(self):
        from pathlib import Path

        description = Path(self.test_cache_dir) / "description.txt"
        description.write_text("Retain this diagnostic description")
        MockRedHatHandler.last_case_payload = None
        result = self.run_cli(
            [
                "create",
                "--product",
                "RHEL",
                "--version",
                "8.0",
                "--summary",
                "lookup failure",
                "--severity",
                "Low",
                "--type",
                "Standard",
                "--description-file",
                str(description),
            ],
            input="y\n",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Case validation failed", result.stderr)
        self.assertIn("Check product/version", result.stderr)
        self.assertIn("REDHAT_SUPPORT_USERNAME", result.stderr)
        self.assertIn("before retrying", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIsNone(MockRedHatHandler.last_case_payload)
        self.assertEqual(description.read_text(), "Retain this diagnostic description")

    def test_create_case_default_template(self):
        """Test creating a case using default_create_template"""
        MockRedHatHandler.lookup_product_version = "8.0"
        import shutil

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        config_path = os.path.join(config_dir, "config.yaml")
        with open(config_path, "w") as f:
            f.write("default_create_template: base_template\n")

        template_path = os.path.join(template_dir, "base_template.yaml")
        with open(template_path, "w") as f:
            f.write(
                "product: RHEL\nversion: 8.0\nsummary: Templated Summary\nseverity: Low\ncaseType: Standard\n"
            )

        with open("desc.txt", "w") as f:
            f.write("My Issue Description")

        try:
            cmd = [
                sys.executable,
                self.cli_path,
                "--config-file",
                config_path,
                "create",
                "--description-file",
                "desc.txt",
            ]

            result = subprocess.run(
                cmd,
                env=self.env,
                capture_output=True,
                text=True,
                input="y\n",
                check=False,
            )

            if result.returncode != 0:
                print("\nSTDOUT:", result.stdout)
                print("STDERR:", result.stderr)
            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Case #20230001 created.", result.stdout)

            cmd_no_tmpl = [
                sys.executable,
                self.cli_path,
                "--config-file",
                config_path,
                "create",
                "--description-file",
                "desc.txt",
                "--no-default-template",
            ]

            result_no_tmpl = subprocess.run(
                cmd_no_tmpl,
                env=self.env,
                capture_output=True,
                text=True,
                input="EOF\n",
                check=False,
            )
            self.assertNotEqual(result_no_tmpl.returncode, 0)

        finally:
            if os.path.exists("desc.txt"):
                os.remove("desc.txt")
            shutil.rmtree(temp_home)

    def test_template_circular_dependency(self):
        """Test template engine circular dependency detection"""
        import shutil

        from rh_support_lib.templates import TemplateEngine

        temp_dir = tempfile.mkdtemp()
        try:
            with open(os.path.join(temp_dir, "tmpl_a.yaml"), "w") as f:
                f.write("include_templates: [tmpl_b]\nkey_a: value_a\n")
            with open(os.path.join(temp_dir, "tmpl_b.yaml"), "w") as f:
                f.write("include_templates: [tmpl_c]\nkey_b: value_b\n")
            with open(os.path.join(temp_dir, "tmpl_c.yaml"), "w") as f:
                f.write("include_templates: [tmpl_a]\nkey_c: value_c\n")

            engine = TemplateEngine(temp_dir)
            with self.assertRaisesRegex(ValueError, "Circular template dependency"):
                engine.process(["tmpl_a"], {})

        finally:
            shutil.rmtree(temp_dir)

    def test_templates(self):
        """Test template functionality"""
        MockRedHatHandler.lookup_product_version = "4.12"
        # Check if jinja2 is available, else skip
        try:
            import jinja2  # noqa: F401
        except ImportError:
            print("Skipping test_templates: jinja2 not installed")
            return

        import shutil

        # Temp HOME for templates
        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        # Create templates dir
        templates_dir = os.path.join(
            temp_home, ".config", "rh-support-cli", "templates"
        )
        os.makedirs(templates_dir)

        # 1. Base OpenShift Template
        base_tmpl = """
product: "OpenShift Container Platform"
version: "4.12"
severity: "3 (Normal)"
caseType: "Bug"
"""
        with open(os.path.join(templates_dir, "openshift_base.yaml"), "w") as f:
            f.write(base_tmpl)

        # 2. Proactive Template (includes base)
        proactive_tmpl = """
include_templates: ["openshift_base"]
caseType: "Other"
severity: "4 (Low)"
remoteSessionTermsAcked: true
summary: "[Proactive] {{ product }} {{ version }} update on {{ cluster_name }}"
description: |
  We are planning to upgrade {{ product }} from {{ version }} to {{ next_version }}.
  ClusterID: {{ cluster_id }}
"""
        with open(os.path.join(templates_dir, "proactive.yaml"), "w") as f:
            f.write(proactive_tmpl)

        try:
            cmd = [
                sys.executable,
                self.cli_path,
                "create",
                "--template",
                "proactive",
                "--template-var",
                "cluster_name=ProdCluster",
                "--template-var",
                "next_version=4.13",
                "--template-var",
                "cluster_id=uuid-123",
            ]

            # input='y\n' for confirmation
            result = subprocess.run(
                cmd,
                env=self.env,
                capture_output=True,
                text=True,
                input="y\n",
                check=False,
            )

            if result.returncode != 0:
                print("Failed Template Test STDOUT:", result.stdout)
                print("Failed Template Test STDERR:", result.stderr)

            self.assertEqual(result.returncode, 0)

            # Verify Payload
            payload = MockRedHatHandler.last_case_payload
            self.assertIsNotNone(payload)
            self.assertEqual(payload.get("product"), "OpenShift Container Platform")
            self.assertEqual(payload.get("version"), "4.12")
            self.assertEqual(payload.get("caseType"), "Other")
            self.assertEqual(payload.get("severity"), "4 (Low)")
            self.assertEqual(payload.get("remoteSessionTermsAcked"), True)
            self.assertIn(
                "[Proactive] OpenShift Container Platform 4.12 update on ProdCluster",
                payload.get("summary"),
            )
            self.assertIn("ClusterID: uuid-123", payload.get("description"))

        finally:
            shutil.rmtree(temp_home)

    def test_templates_advanced(self):
        """Test advanced template features: overrides, currentDoc, parse_date"""
        MockRedHatHandler.lookup_product_version = "4.12"
        try:
            import jinja2  # noqa: F401
        except ImportError:
            print("Skipping test_templates_advanced: jinja2 not installed")
            return

        import shutil

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        # Mock dateparser
        mock_dateparser = """
from datetime import datetime
def parse(date_string):
    return datetime(2023, 1, 1, 12, 0, 0)
"""
        with open(os.path.join(temp_home, "dateparser.py"), "w") as f:
            f.write(mock_dateparser)

        # Update PYTHONPATH to include temp_home
        self.env["PYTHONPATH"] = temp_home + os.pathsep + self.env.get("PYTHONPATH", "")

        # Templates dir
        templates_dir = os.path.join(
            temp_home, ".config", "rh-support-cli", "templates"
        )
        os.makedirs(templates_dir)

        # Templates
        with open(os.path.join(templates_dir, "base_tmpl.yaml"), "w") as f:
            f.write(
                "version: '4.10'\nremoteSessionTermsAcked: true\nproduct: OCP\ndescription: base\nseverity: Low\ncaseType: Standard"
            )

        derived = """
include_templates: ["base_tmpl"]
summary: "Ver: {{ currentDoc.version }} Date: {{ 'next friday' | parse_date }}"
"""
        with open(os.path.join(templates_dir, "derived.yaml"), "w") as f:
            f.write(derived)

        try:
            cmd = [
                sys.executable,
                self.cli_path,
                "create",
                "--template",
                "derived",
                "--version",
                "4.12",
            ]
            result = subprocess.run(
                cmd,
                env=self.env,
                capture_output=True,
                text=True,
                input="y\n",
                check=False,
            )

            if result.returncode != 0:
                print("Failed Advanced Template Test STDOUT:", result.stdout)
                print("Failed Advanced Template Test STDERR:", result.stderr)

            self.assertEqual(result.returncode, 0)

            payload = MockRedHatHandler.last_case_payload
            self.assertEqual(
                payload.get("version"), "4.12", "CLI version should override template"
            )
            self.assertEqual(
                payload.get("remoteSessionTermsAcked"),
                True,
                "Extra field should be present",
            )

            # Summary check
            # Ver: 4.10 (from template)
            # Date: 01-01-2023 ... (from mock)
            self.assertIn("Ver: 4.10", payload.get("summary"))
            self.assertIn("01-01-2023", payload.get("summary"))

        finally:
            shutil.rmtree(temp_home)

    def test_debug_flag(self):
        """Test that the --debug flag produces debug output"""
        # We need to capture stderr to check for log messages
        # "show" command is good because it does a fetch
        result = self.run_cli(["--debug", "show", "-c", "12345", "--no-pager"])

        if result.returncode != 0:
            print("Failed Debug Flag Test STDOUT:", result.stdout)
            print("Failed Debug Flag Test STDERR:", result.stderr)

        self.assertEqual(result.returncode, 0)
        # Check for debug logs in stderr
        # Note: requests uses lowercase methods internally often
        self.assertIn("DEBUG: Request:", result.stderr)
        self.assertIn("DEBUG: Response Status: 200", result.stderr)

    def test_debug_file_logging(self):
        """Test that --debug-file logs to file and does not truncate"""
        log_file = os.path.abspath("debug_output.log")

        # We need a large response to test truncation
        # Mock /cases/12345 to return a large description
        # We can't easily change the MockRedHatHandler instance logic dynamically for just one test without restarting,
        # but we can rely on the fact that existing responses are small, except we can craft a test where we verify it writes to file.
        # Actually, let's just verify it writes to file first.

        try:
            result = self.run_cli(
                ["--debug-file", log_file, "show", "-c", "12345", "--no-pager"]
            )
            self.assertEqual(result.returncode, 0)

            self.assertTrue(os.path.exists(log_file), "Log file was not created")

            with open(log_file, "r") as f:
                content = f.read()

            self.assertIn("DEBUG: Request:", content)
            self.assertIn("DEBUG: Response Status: 200", content)
            # Ensure it didn't print to stderr (since we redirected to file)
            self.assertNotIn("DEBUG: Request:", result.stderr)

        finally:
            if os.path.exists(log_file):
                os.remove(log_file)

    def test_debug_file_from_config(self):
        """Test that debug_file from config enables background logging"""
        log_file = os.path.abspath("config_debug_output.log")
        config_file = "test_debug_config.yaml"
        config = {"debug_file": log_file}

        with open(config_file, "w") as f:
            import yaml

            yaml.dump(config, f)

        try:
            # Run without --debug or --debug-file, but with --config-file
            result = self.run_cli(
                ["--config-file", config_file, "show", "-c", "12345", "--no-pager"]
            )
            self.assertEqual(result.returncode, 0)

            self.assertTrue(os.path.exists(log_file), "Log file was not created")

            with open(log_file, "r") as f:
                content = f.read()

            self.assertIn("DEBUG: Request:", content)
            self.assertIn("DEBUG: Response Status: 200", content)
            # Ensure it didn't print to stderr
            self.assertNotIn("DEBUG: Request:", result.stderr)

        finally:
            if os.path.exists(log_file):
                os.remove(log_file)
            if os.path.exists(config_file):
                os.remove(config_file)

    def test_apply_template_dry_run(self):
        """Test applying a template to a case in dry-run mode"""
        import shutil

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        template_path = os.path.join(template_dir, "test_template.yaml")
        with open(template_path, "w") as f:
            f.write("severity: Low\nstatus: Waiting on Customer\n")

        try:
            result = self.run_cli(
                [
                    "--simple-output",
                    "apply",
                    "-c",
                    "12345",
                    "-t",
                    "test_template",
                    "--dry-run",
                ]
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("[Dry-run] Would apply changes listed above.", result.stdout)
            self.assertIn("severity: High -> Low", result.stdout)
            self.assertIn(
                "status: Waiting on Red Hat -> Waiting on Customer", result.stdout
            )

        finally:
            shutil.rmtree(temp_home)

    def test_apply_template_execution(self):
        """Test executing applying a template to a case (real PUT/POST)"""
        import shutil

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        template_path = os.path.join(template_dir, "test_template.yaml")
        with open(template_path, "w") as f:
            f.write("severity: Low\n")

        try:
            result = self.run_cli(["apply", "-c", "12345", "-t", "test_template"])
            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Case fields updated.", result.stdout)
        finally:
            shutil.rmtree(temp_home)

    def test_apply_template_no_changes(self):
        """Test applying a template when no changes are needed"""
        import shutil

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        template_path = os.path.join(template_dir, "test_template.yaml")
        # Existing case has severity: High, status: Waiting on Red Hat, and let's not define watchers
        with open(template_path, "w") as f:
            f.write("severity: High\nstatus: Waiting on Red Hat\n")

        try:
            result = self.run_cli(["apply", "-c", "12345", "-t", "test_template"])
            self.assertEqual(result.returncode, 0)
            self.assertIn(
                "Case #12345 already adheres to the template(s). No changes needed.",
                result.stdout,
            )
        finally:
            shutil.rmtree(temp_home)

    def test_apply_template_vars(self):
        """Test applying a template with template variables"""
        import shutil

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        template_path = os.path.join(template_dir, "test_template.yaml")
        with open(template_path, "w") as f:
            f.write(
                'severity: "{{ target_severity }}"\nstatus: "{{ target_status }}"\n'
            )

        try:
            result = self.run_cli(
                [
                    "--simple-output",
                    "apply",
                    "-c",
                    "12345",
                    "-t",
                    "test_template",
                    "--template-var",
                    "target_severity=Low",
                    "--template-var",
                    "target_status=Closed",
                    "--dry-run",
                ]
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("severity: High -> Low", result.stdout)
            self.assertIn("status: Waiting on Red Hat -> Closed", result.stdout)
        finally:
            shutil.rmtree(temp_home)

    def test_apply_case_not_found(self):
        """Test applying a template to a non-existent case fails gracefully"""
        import shutil

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        template_path = os.path.join(template_dir, "test_template.yaml")
        with open(template_path, "w") as f:
            f.write("severity: Low\n")

        try:
            # Case id "abc" is not digit, so mock GET returns 404, leading to failure
            result = self.run_cli(["apply", "-c", "abc", "-t", "test_template"])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Error: Case abc not found", result.stderr)
        finally:
            shutil.rmtree(temp_home)

    def test_list_templates(self):
        """Test listing local templates and parsing their metadata"""
        import shutil

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        # Create template A
        template_path_a = os.path.join(template_dir, "tmpl_a.yaml")
        with open(template_path_a, "w") as f:
            f.write(
                "_template_description: Template A Description\nseverity: Low\nwatchers:\n  - alice\n_some_ignored_meta: metadata\n"
            )

        # Create template B
        template_path_b = os.path.join(template_dir, "tmpl_b.yaml")
        with open(template_path_b, "w") as f:
            f.write(
                "_desc: Template B Description\nproduct: RHEL\ninclude_templates: tmpl_a\n"
            )

        try:
            # 1. Simple output mode
            result = self.run_cli(["--simple-output", "list-templates"])
            self.assertEqual(result.returncode, 0)
            self.assertIn("Found 2 template(s)", result.stdout)
            self.assertIn("Template:    tmpl_a", result.stdout)
            self.assertIn("Desc:      Template A Description", result.stdout)
            self.assertIn("Fields:    severity: Low", result.stdout)
            self.assertNotIn("_some_ignored_meta", result.stdout)
            self.assertIn("Template:    tmpl_b", result.stdout)
            self.assertIn("Desc:      Template B Description", result.stdout)
            self.assertIn("Fields:    product: RHEL", result.stdout)
            self.assertIn("Includes:  tmpl_a", result.stdout)

            # 2. Default Rich mode (verifies Panel rendering without AttributeError)
            result_rich = self.run_cli(["list-templates"])
            self.assertEqual(result_rich.returncode, 0)
            visible_output = Text.from_ansi(result_rich.stdout).plain
            self.assertIn("Found 2 template(s)", visible_output)
            self.assertIn("Template: tmpl_a", visible_output)
            self.assertIn("Template: tmpl_b", visible_output)

            # 3. Structured JSON output mode
            result_json = self.run_cli(["list-templates", "--json"])
            self.assertEqual(result_json.returncode, 0)
            parsed = json.loads(result_json.stdout)
            self.assertEqual(len(parsed), 2)
            self.assertEqual(parsed[0]["name"], "tmpl_a")
            self.assertEqual(parsed[1]["name"], "tmpl_b")

            # 4. Alias list-template
            result_alias = self.run_cli(["list-template", "--simple-output"])
            self.assertEqual(result_alias.returncode, 0)
            self.assertIn("Found 2 template(s)", result_alias.stdout)
        finally:
            shutil.rmtree(temp_home)

    def test_list_templates_empty(self):
        """Test listing when there are no templates"""
        import shutil

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        try:
            result = self.run_cli(["list-templates"])
            self.assertEqual(result.returncode, 0)
            self.assertIn("No templates found", result.stdout)
        finally:
            shutil.rmtree(temp_home)

    def test_list_templates_no_directory(self):
        """Test listing when the template directory does not exist"""
        import shutil

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        try:
            result = self.run_cli(["list-templates"])
            self.assertEqual(result.returncode, 0)
            self.assertIn("Template directory does not exist", result.stdout)
        finally:
            shutil.rmtree(temp_home)

    def test_template_fields_starting_with_underscore_ignored(self):
        """Test that any template fields starting with an underscore are completely ignored in create and apply"""
        MockRedHatHandler.lookup_product_version = "8.0"
        import shutil

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        template_path = os.path.join(template_dir, "test_underscore.yaml")
        with open(template_path, "w") as f:
            f.write(
                "severity: Low\n_ignored_field: should_be_ignored\n_template_description: This is a test template\n"
            )

        try:
            # 1. Run apply
            result_apply = self.run_cli(
                [
                    "--simple-output",
                    "apply",
                    "-c",
                    "12345",
                    "-t",
                    "test_underscore",
                    "--dry-run",
                ]
            )
            self.assertEqual(result_apply.returncode, 0)
            # severity should change, but _ignored_field or _template_description should not be listed as changes
            self.assertIn("severity: High -> Low", result_apply.stdout)
            self.assertNotIn("_ignored_field", result_apply.stdout)
            self.assertNotIn("_template_description", result_apply.stdout)

            # 2. Run create
            with open("desc.txt", "w") as f:
                f.write("My Issue Description")

            cmd = [
                sys.executable,
                self.cli_path,
                "create",
                "--product",
                "RHEL",
                "--version",
                "8.0",
                "--summary",
                "My Summary",
                "--type",
                "Standard",
                "--severity",
                "Low",
                "--description-file",
                "desc.txt",
                "--template",
                "test_underscore",
            ]
            result_create = subprocess.run(
                cmd,
                env=self.env,
                capture_output=True,
                text=True,
                input="y\n",
                check=False,
            )
            if result_create.returncode != 0:
                print("\n--- CREATE STDOUT ---\n", result_create.stdout)
                print("\n--- CREATE STDERR ---\n", result_create.stderr)
            self.assertEqual(result_create.returncode, 0)

            # Verify MockRedHatHandler.last_case_payload does not contain any key starting with '_'
            self.assertIsNotNone(MockRedHatHandler.last_case_payload)
            for k in MockRedHatHandler.last_case_payload:
                self.assertFalse(
                    k.startswith("_"),
                    f"Key starting with underscore found in payload: {k}",
                )

        finally:
            if os.path.exists("desc.txt"):
                os.remove("desc.txt")
            shutil.rmtree(temp_home)

    def test_tui_command_registration(self):
        """Test that the TUI command is registered and help menu works"""
        result = self.run_cli(["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("tui", result.stdout)

    def test_tui_activation(self):
        """Test that running the tui subcommand initializes and launches the SupportApp"""
        from unittest.mock import MagicMock, patch

        from rh_support_lib.tui.app import cmd_tui

        with patch("rh_support_lib.tui.app.SupportApp.run") as mock_run:
            args = MagicMock()
            token = "mock_token"
            config = {}
            cmd_tui(args, token, config)
            mock_run.assert_called_once()

    def test_tui_app_headless(self):
        """Programmatically launch the TUI app headlessly to verify CSS parsing and startup widgets"""
        import asyncio

        from textual.widgets import DataTable

        import rh_support_lib.tui.app
        from rh_support_lib.tui.app import SupportApp

        # Route GraphQL and API URLs to our mock HTTP server
        orig_gql_url = rh_support_lib.constants.GRAPHQL_URL
        orig_api_url = rh_support_lib.constants.API_URL
        rh_support_lib.constants.GRAPHQL_URL = f"{self.base_url}/graphql"
        rh_support_lib.constants.API_URL = self.base_url

        async def run_headless():
            app = SupportApp(token="mock_access_token_123", config={})
            async with app.run_test() as pilot:
                # If we get here, CSS parsed successfully and the app launched with zero crashes!
                self.assertEqual(app.title, "Red Hat Support CLI")
                table = app.query_one("#case-table", DataTable)
                self.assertIsNotNone(table)
                self.assertIsNotNone(app.query_one("#case-detail-container"))

                # Directly call the row selection handler with a mock event to simulate selection
                from unittest.mock import MagicMock

                event = MagicMock()
                event.row_key.value = "12345"
                app.on_row_selected(event)

                # Let the pilot process events and background workers run
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertIsNotNone(app.current_details)
                self.assertEqual(app.current_details.case.case_number, "12345")
                loaded_details = app.current_details

                # Verify that case was successfully selected
                self.assertEqual(app.selected_case_id, "12345")

                # Verify that the details panel loaded successfully without mounting/layout crashes

                detail_container = app.query_one("#case-detail-container")
                self.assertIsNotNone(detail_container)
                self.assertTrue(detail_container.can_focus)

                # Ensure that compiled Static panels are now mounted directly inside the container
                self.assertTrue(len(detail_container.children) > 0)

                # 3. Test focus/unfocus toggling layouts programmatically using 'f'
                app.action_focus_pane()
                self.assertTrue(
                    app.screen.has_class("focused-left")
                    or app.screen.has_class("focused-right")
                )

                # Press 'f' again to toggle it off!
                app.action_focus_pane()
                self.assertFalse(app.screen.has_class("focused-left"))
                self.assertFalse(app.screen.has_class("focused-right"))

                # Focus again and test escape_action to unfocus!
                app.action_focus_pane()
                self.assertTrue(
                    app.screen.has_class("focused-left")
                    or app.screen.has_class("focused-right")
                )
                app.action_escape_action()
                self.assertFalse(app.screen.has_class("focused-left"))
                self.assertFalse(app.screen.has_class("focused-right"))

                # 4. Test contextual refresh layouts programmatically
                app.action_refresh()

                # 5. Test activating the inline comment pane
                app.action_add_comment()
                await pilot.pause()

                # Verify that the comment pane is displayed
                from textual.widgets import Select, TextArea

                self.assertTrue(app.screen.has_class("commenting"))
                textarea = app.query_one("#tui-comment-textarea", TextArea)
                self.assertIsNotNone(textarea)
                select_status = app.query_one("#tui-comment-status-select", Select)
                self.assertIsNotNone(select_status)

                # Type some draft comment text into the TextArea
                textarea.text = "This is a draft comment to test unsaved changes"

                # Trigger exit which will prompt the UnsavedChangesModal
                app.action_exit_commenting()
                await pilot.pause()

                # Verify UnsavedChangesModal is now active on screen!
                unsaved_modal = app.screen
                self.assertIn("UnsavedChangesModal", str(unsaved_modal))
                self.assertIsNotNone(unsaved_modal.query_one("#discard-btn"))
                self.assertIsNotNone(unsaved_modal.query_one("#save-btn"))

                # Dismiss the modal by selecting 'discard'
                unsaved_modal.dismiss("discard")
                await pilot.pause()

                # Verify commenting pane is closed and text is cleared!
                self.assertFalse(app.screen.has_class("commenting"))
                self.assertEqual(textarea.text, "")

                # Test activating comment pane when case status is 'Waiting on Customer Action Required'
                from rh_support_lib.models import Case, CaseDetails

                app.cases.append(
                    Case(
                        case_number="99999",
                        summary="Test Action Required Case",
                        status="Waiting on Customer Action Required",
                        severity="High",
                        product="RHEL",
                        version="9.0",
                        account_number="123456",
                        created_date="2026-10-01",
                        last_modified_date="2026-10-02",
                        created_by="User",
                        last_modified_by="User",
                        owner="User",
                        id="99999",
                    )
                )
                app.selected_case_id = "99999"
                app.render_case_details(CaseDetails(app.cases[-1], "description"), [])
                app.action_add_comment()
                await pilot.pause()
                self.assertTrue(app.screen.has_class("commenting"))
                select_status2 = app.query_one("#tui-comment-status-select", Select)
                self.assertIsNotNone(select_status2)
                self.assertEqual(select_status2.value, "In Progress")
                app.screen.remove_class("commenting")
                # 6. Test activating the file attachment modal
                app.action_add_attachment()
                await pilot.pause()

                attach_modal = app.screen
                self.assertIn("AttachModal", str(attach_modal))
                self.assertIsNotNone(attach_modal.query_one("#attach-file-input"))

                # Dismiss modal
                attach_modal.dismiss("")
                await pilot.pause()

                # 6b. Test activating the file download modal
                from rh_support_lib.models import Attachment

                app.current_attachments = [
                    Attachment(
                        uuid="att_test_123",
                        file_name="diag.tar.gz",
                        size=1024,
                        size_kb=1.0,
                        created_date="2026-10-08T10:00:00Z",
                    )
                ]
                app.action_download_attachment()
                await pilot.pause()
                download_modal = app.screen
                self.assertIn("DownloadModal", str(download_modal))
                download_modal.dismiss(None)
                await pilot.pause()

                # 7. Test TUI follow toggle and mark read actions
                app.selected_case_id = "12345"
                app.render_case_details(loaded_details, [])
                app.action_toggle_follow()
                u = app.case_service.get_current_user()
                self.assertTrue(app.case_service.state_service.is_following("12345", u))
                app.action_toggle_follow()
                self.assertFalse(
                    app.case_service.state_service.is_following("12345", u)
                )
                app.action_mark_read()
                await app.workers.wait_for_complete()

        try:
            with mock.patch.dict(
                os.environ,
                {
                    "RH_GRAPHQL_URL": f"{self.base_url}/graphql",
                    "RH_ATTACHMENT_API_URL": self.base_url,
                    "XDG_CACHE_HOME": self.test_cache_dir,
                    "XDG_STATE_HOME": self.test_state_dir,
                },
            ):
                asyncio.run(run_headless())
        finally:
            rh_support_lib.constants.GRAPHQL_URL = orig_gql_url
            rh_support_lib.constants.API_URL = orig_api_url

    def test_tui_config_handling(self):
        """Test that TUI correctly processes default_bookmark and default_create_template from config"""
        import shutil

        from rh_support_lib.tui.app import (
            BookmarkModal,
            TemplateModal,
            build_filter_payload,
        )

        config = {
            "default_bookmark": "my_team",
            "default_create_template": "my_default_template",
            "bookmarks": {
                "my_team": {
                    "account": "999999",
                    "status": ["Waiting on Red Hat"],
                    "severity": ["High"],
                }
            },
        }

        # 1. Test build_filter_payload with standard and override options
        payload = build_filter_payload(config)
        self.assertEqual(payload.get("accountNumber"), "999999")
        self.assertEqual(payload.get("status"), "Waiting on Red Hat")
        self.assertEqual(payload.get("severity"), "2 (High)")

        payload_no_default = build_filter_payload(config, no_default_bookmark=True)
        self.assertNotIn("accountNumber", payload_no_default)

        payload_specific = build_filter_payload(config, bookmark="my_team")
        self.assertEqual(payload_specific.get("accountNumber"), "999999")

        payload_none = build_filter_payload(config, bookmark="none")
        self.assertNotIn("accountNumber", payload_none)

        # 2. Test Modal initial values
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        temp_home = tempfile.mkdtemp()
        try:
            modal = TemplateModal("12345", "my_default_template")
            self.assertEqual(modal.default_template, "my_default_template")

            modal_bk = BookmarkModal(config, "my_team")
            self.assertEqual(modal_bk.active_bookmark, "my_team")
        finally:
            shutil.rmtree(temp_home)
            loop.close()
            asyncio.set_event_loop(None)

    def test_mock_mode_list_and_show(self):
        """Test that --mock runs the CLI offline against the in-process synthetic corpus"""
        from unittest import mock

        from rh_support_lib.main import main

        # Mock stdout to capture outputs
        stdout_capture = io.StringIO()
        with (
            mock.patch(
                "sys.argv",
                [
                    "rh-support-cli.py",
                    "--mock",
                    "--simple-output",
                    "list",
                    "--no-default-bookmark",
                ],
            ),
            mock.patch("sys.stdout", stdout_capture),
        ):
            try:
                main()
            except SystemExit as e:
                self.assertEqual(e.code, 0)

        output = stdout_capture.getvalue()
        self.assertIn("Cannot mount NFS share on RHEL", output)
        self.assertIn("1001", output)
        self.assertIn("1002", output)
        self.assertIn("1003", output)

        # Test show case details
        stdout_capture_show = io.StringIO()
        with (
            mock.patch(
                "sys.argv",
                [
                    "rh-support-cli.py",
                    "--mock",
                    "--simple-output",
                    "show",
                    "-c",
                    "1001",
                    "--no-pager",
                ],
            ),
            mock.patch("sys.stdout", stdout_capture_show),
        ):
            try:
                main()
            except SystemExit as e:
                self.assertEqual(e.code, 0)

        show_output = stdout_capture_show.getvalue()
        self.assertIn("CASE: 1001", show_output)
        self.assertIn("Cannot mount NFS share on RHEL 9.0", show_output)
        self.assertIn("COMMENTS (2)", show_output)

    def test_mock_mode_create_and_comment(self):
        """Test that creating cases and writing comments in --mock mode mutates state correctly"""
        import shutil
        from unittest import mock

        import rh_support_lib.constants
        from rh_support_lib.main import main

        saved_constants = {
            "API_URL": rh_support_lib.constants.API_URL,
            "SSO_URL": rh_support_lib.constants.SSO_URL,
            "GRAPHQL_URL": rh_support_lib.constants.GRAPHQL_URL,
            "ATTACHMENT_API_URL": rh_support_lib.constants.ATTACHMENT_API_URL,
        }
        saved_env = os.environ.copy()

        # Use a writable mock corpus directory to verify mutation
        temp_corpus = tempfile.mkdtemp()
        from rh_support_lib.synthetic_corpus import generate_default_corpus

        generate_default_corpus(temp_corpus)
        try:
            # Create a description file for case creation
            desc_file_path = os.path.join(temp_corpus, "desc.txt")
            with open(desc_file_path, "w") as f:
                f.write("A brand new synthetic mock issue description.")

            # Create a case - we must mock stdin to answer 'y' to the submit prompt
            stdout_capture = io.StringIO()
            stdin_mock = io.StringIO("y\n")
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "rh-support-cli.py",
                        "--mock",
                        "--mock-corpus",
                        temp_corpus,
                        "--simple-output",
                        "create",
                        "--product",
                        "RHEL",
                        "--version",
                        "9.0",
                        "--summary",
                        "Synthetic Test Summary",
                        "--description-file",
                        desc_file_path,
                        "--severity",
                        "Normal",
                        "--type",
                        "Standard",
                    ],
                ),
                mock.patch("sys.stdout", stdout_capture),
                mock.patch("sys.stdin", stdin_mock),
            ):
                try:
                    main()
                except SystemExit as e:
                    self.assertEqual(e.code, 0)

            # Check that case #1004 was created and file exists
            case_file = os.path.join(temp_corpus, "cases", "1004.json")
            self.assertTrue(os.path.exists(case_file))
            with open(case_file, "r") as f:
                case_data = json.load(f)
            self.assertEqual(case_data["summary"], "Synthetic Test Summary")
            from rh_support_lib.utils import clean_comment_body

            self.assertEqual(
                clean_comment_body(case_data["description"].strip()),
                "A brand new synthetic mock issue description.",
            )

            # Post a comment to the newly created case
            comment_file_path = os.path.join(temp_corpus, "comment.txt")
            with open(comment_file_path, "w") as f:
                f.write("A custom comment body text.")

            stdout_comment = io.StringIO()
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "rh-support-cli.py",
                        "--mock",
                        "--mock-corpus",
                        temp_corpus,
                        "--simple-output",
                        "comment",
                        "-c",
                        "1004",
                        "-f",
                        comment_file_path,
                    ],
                ),
                mock.patch("sys.stdout", stdout_comment),
            ):
                try:
                    main()
                except SystemExit as e:
                    self.assertEqual(e.code, 0)

            # Verify comment was written to file
            with open(case_file, "r") as f:
                case_data_updated = json.load(f)
            self.assertEqual(len(case_data_updated["comments"]), 1)
            self.assertEqual(
                clean_comment_body(case_data_updated["comments"][0]["body"]),
                "A custom comment body text.",
            )

        finally:
            for k, v in saved_constants.items():
                setattr(rh_support_lib.constants, k, v)
            os.environ.clear()
            os.environ.update(saved_env)
            shutil.rmtree(temp_corpus)

    def test_services_direct_api(self):
        """Test CaseService and AttachmentService directly as a library"""
        import rh_support_lib.constants
        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.attachments import AttachmentService
        from rh_support_lib.services.cases import CaseService

        orig_gql = rh_support_lib.constants.GRAPHQL_URL
        rh_support_lib.constants.GRAPHQL_URL = f"{self.base_url}/graphql"
        try:
            api_client = LegacyAPIClient("mock_access_token_123")
            case_service = CaseService(
                api_client, graphql_url=f"{self.base_url}/graphql"
            )
            att_service = AttachmentService(api_client, base_url=self.base_url)

            # 1. CaseService list_cases
            cases = case_service.list_cases(
                status=["Waiting on Red Hat"], severity=["High"]
            )
            self.assertTrue(len(cases) >= 1)
            self.assertTrue(cases[0].case_number in ("FILTER_TEST", "1001"))

            # 2. CaseService get_case_details
            details = case_service.get_case_details("12345")
            self.assertIsNotNone(details)
            self.assertEqual(details.case.case_number, "12345")
            self.assertEqual(len(details.comments), 2)

            # 3. CaseService add_comment
            comment = case_service.add_comment(
                "12345", "Automated test comment", is_public=True
            )
            self.assertEqual(comment.body, "Automated test comment")
            self.assertTrue(comment.is_public)

            # 4. CaseService get_active_products
            products = case_service.get_active_products()
            self.assertTrue(len(products) >= 2)
            self.assertEqual(products[0].name, "Red Hat Enterprise Linux")

            # 5. AttachmentService list_attachments
            attachments = att_service.list_attachments("12345")
            self.assertTrue(len(attachments) >= 1)
            self.assertEqual(attachments[0].file_name, "test_attachment.log")
        finally:
            rh_support_lib.constants.GRAPHQL_URL = orig_gql

    def test_graphql_flag_cli(self):
        """Test running CLI commands with --graphql (GraphQL and v3 APIs)"""

        temp_dir = tempfile.mkdtemp()
        try:
            # 1. List with --graphql
            result_list = self.run_cli(["--graphql", "list"])
            self.assertEqual(result_list.returncode, 0)
            self.assertIn("FILTER_TEST", result_list.stdout)

            # 2. Show with --graphql
            result_show = self.run_cli(
                ["--graphql", "show", "-c", "12345", "--no-pager"]
            )
            self.assertEqual(result_show.returncode, 0)
            self.assertIn("CASE: 12345", result_show.stdout)
            self.assertIn("Test Case Details", result_show.stdout)

            # 3. Comment with --graphql
            comment_file = os.path.join(temp_dir, "comment.txt")
            with open(comment_file, "w") as f:
                f.write("New CRM comment text")
            result_comment = self.run_cli(
                ["--graphql", "comment", "-c", "12345", "-f", comment_file]
            )
            self.assertEqual(result_comment.returncode, 0)
            self.assertIn("Success: Comment added.", result_comment.stdout)

            # 4. Attachment list with --graphql
            result_att_list = self.run_cli(
                ["--graphql", "attachment", "-c", "12345", "list"]
            )
            self.assertEqual(result_att_list.returncode, 0)
            self.assertIn("Attachments for Case #12345", result_att_list.stdout)

            # 5. Attachment upload, download, delete with --graphql
            sample_file = os.path.join(temp_dir, "sample.txt")
            with open(sample_file, "w") as f:
                f.write("Sample content")
            result_up = self.run_cli(
                ["--graphql", "attachment", "-c", "12345", "upload", "-f", sample_file]
            )
            self.assertEqual(result_up.returncode, 0)
            self.assertIn("Success: File 'sample.txt' attached.", result_up.stdout)

            dest_file = os.path.join(temp_dir, "down.txt")
            result_down = self.run_cli(
                [
                    "--graphql",
                    "attachment",
                    "-c",
                    "12345",
                    "download",
                    "-a",
                    "att_test_uuid_123",
                    "-o",
                    dest_file,
                ]
            )
            self.assertEqual(result_down.returncode, 0)
            self.assertTrue(os.path.exists(dest_file))

            result_del = self.run_cli(
                [
                    "--graphql",
                    "attachment",
                    "-c",
                    "12345",
                    "delete",
                    "-a",
                    "att_test_uuid_123",
                    "-y",
                ]
            )
            self.assertEqual(result_del.returncode, 0)
            self.assertIn(
                "Successfully deleted attachment 'att_test_uuid_123'.",
                result_del.stdout,
            )
        finally:
            shutil.rmtree(temp_dir)

    def test_graphql_cli(self):
        """Test running CLI commands with and without --graphql"""
        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.attachments import AttachmentService
        from rh_support_lib.services.cases import CaseService

        # 1. Direct library instantiation
        api_client = LegacyAPIClient("mock_access_token_123")
        case_service = CaseService(api_client)
        self.assertIsNotNone(case_service)
        att_service = AttachmentService(api_client)
        self.assertIsNotNone(att_service)
        # 2. CLI commands default to graphql without --graphql flags
        result_default = self.run_cli(["list"])
        self.assertEqual(result_default.returncode, 0)
        self.assertIn("FILTER_TEST", result_default.stdout)

        # 3. --graphql flag is also accepted as a compatible option
        result_gql = self.run_cli(["--graphql", "list"])
        self.assertEqual(result_gql.returncode, 0)
        self.assertIn("FILTER_TEST", result_gql.stdout)

    def test_tui_layout_horizontal_mailbox_and_persistence(self):
        """Test TUI horizontal split (mailbox) layout initialization, toggle action, and persistence."""
        import asyncio
        import shutil

        from rh_support_lib.services.state import StateService
        from rh_support_lib.tui.app import SupportApp

        # 1. StateService get/set tui layout
        temp_dir = tempfile.mkdtemp()
        try:
            state_file = os.path.join(temp_dir, "state.yaml")
            svc = StateService(state_file=state_file)
            self.assertEqual(svc.get_tui_layout("testuser"), "horizontal")
            svc.set_tui_layout("testuser", "vertical")
            self.assertEqual(svc.get_tui_layout("testuser"), "vertical")
            svc.set_tui_layout("testuser", "horizontal")
            self.assertEqual(svc.get_tui_layout("testuser"), "horizontal")
            svc.set_tui_layout("testuser", "mailbox")
            self.assertEqual(svc.get_tui_layout("testuser"), "horizontal")
        finally:
            shutil.rmtree(temp_dir)

        # 2. Headless SupportApp defaulting to horizontal mailbox
        async def run_layout_test():
            app = SupportApp(token="mock_access_token_123", config={})
            async with app.run_test() as pilot:
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(app.layout, "horizontal")
                self.assertNotIn("layout-vertical", app.screen.classes)

                # 3. Test pane switching with ctrl-arrows in horizontal split
                await pilot.press("ctrl+down")
                self.assertTrue(
                    app.focused.id == "case-detail-container"
                    or app.query_one("#case-detail-container").has_focus
                )
                await pilot.press("ctrl+up")
                self.assertTrue(
                    app.focused.id == "case-table"
                    or app.query_one("#case-table").has_focus
                )

                # 4. Toggle to vertical split and test ctrl-left / ctrl-right
                app.action_toggle_layout()
                await pilot.pause()
                self.assertEqual(app.layout, "vertical")
                self.assertIn("layout-vertical", app.screen.classes)
                await pilot.press("ctrl+right")
                self.assertTrue(
                    app.focused.id == "case-detail-container"
                    or app.query_one("#case-detail-container").has_focus
                )
                await pilot.press("ctrl+left")
                self.assertTrue(
                    app.focused.id == "case-table"
                    or app.query_one("#case-table").has_focus
                )

                # 5. Toggle back to horizontal
                app.action_toggle_layout()
                await pilot.pause()
                self.assertEqual(app.layout, "horizontal")
                self.assertNotIn("layout-vertical", app.screen.classes)

        with (
            mock.patch("rh_support_lib.tui.app.CaseService") as service,
            mock.patch("rh_support_lib.tui.app.AttachmentService"),
        ):
            service.return_value.state_service.get_tui_layout.return_value = (
                "horizontal"
            )
            service.return_value.list_cases.return_value = []
            asyncio.run(run_layout_test())

    def test_tui_mark_read_removes_visual_unread_indicator(self):
        """Test that pressing 'm' in TUI removes the unread indicator from table and details divider."""
        import asyncio
        import shutil
        from unittest.mock import patch

        import rh_support_lib.constants
        from rh_support_lib.models import Case, CaseComment, CaseDetails
        from rh_support_lib.services.state import StateService
        from rh_support_lib.tui.app import SupportApp

        orig_gql = rh_support_lib.constants.GRAPHQL_URL
        rh_support_lib.constants.GRAPHQL_URL = f"{self.base_url}/graphql"
        temp_dir = tempfile.mkdtemp()
        state_file = os.path.join(temp_dir, "rh-support-cli", "state.yaml")

        try:
            with patch.dict(
                os.environ,
                {
                    "XDG_STATE_HOME": temp_dir,
                    "XDG_CONFIG_HOME": temp_dir,
                    "RH_GRAPHQL_URL": f"{self.base_url}/graphql",
                },
            ):
                svc = StateService(state_file=state_file)
                app = SupportApp(token="mock_access_token_123", config={})
                app.case_service.state_service = svc
                user = app.case_service.get_current_user()

                # Set an older checkpoint on case 12345
                svc.add_tracked_case(
                    user, "12345", relationship="followed", summary="Test Case"
                )
                data = svc.load_state()
                u_state = svc._get_user_state(data, user)
                u_state["tracked_cases"]["12345"]["read_checkpoint"] = {
                    "last_read_comment_date": "2026-10-01T00:00:00Z",
                    "last_read_comment_id": "comm_0",
                    "status": "Waiting on Customer Action Required",
                    "marked_read_at": "2026-10-01T00:00:00Z",
                }
                svc.save_state(data)

                async def run_test():
                    async with app.run_test() as pilot:
                        await pilot.pause()
                        c = Case(
                            case_number="12345",
                            summary="Test Case",
                            status="Waiting on Red Hat",
                            severity="High",
                            has_unread=True,
                            unread_count=1,
                        )
                        details = CaseDetails(
                            case=c,
                            description="Case Description",
                            comments=[
                                CaseComment(
                                    id="comm_1",
                                    body="New comment",
                                    created_date="2026-10-08T09:00:00Z",
                                    is_public=True,
                                    created_by="Red Hat Support",
                                )
                            ],
                        )
                        app.cases = [c]
                        app.populate_cases_table()
                        table = app.query_one("#case-table")
                        cols = list(table.columns.keys())

                        # Check that table cell has unread badge
                        cell_text = str(table.get_cell("12345", cols[0]))
                        self.assertIn("●1", cell_text)

                        # Mount details and verify divider is present
                        app.selected_case_id = "12345"
                        app.render_case_details(details, [])
                        await pilot.pause()
                        dividers_before = app.query(".tui-checkpoint-divider")
                        self.assertEqual(len(dividers_before), 1)

                        # Trigger 'm' (mark read)
                        app.action_mark_read()
                        await pilot.pause()

                        # Table cell unread badge MUST be removed
                        cell_after = str(table.get_cell("12345", cols[0]))
                        self.assertNotIn("●", cell_after)
                        self.assertFalse(c.has_unread)
                        self.assertEqual(c.unread_count, 0)

                        # Re-render details (as fetch_case_details does) and verify divider is gone
                        app.render_case_details(details, [])
                        await pilot.pause()
                        dividers_after = app.query(".tui-checkpoint-divider")
                        self.assertEqual(len(dividers_after), 0)

                        # Verify checkpoint in state.yaml was advanced
                        reloaded_tc = svc.get_tracked_cases(user).get("12345")
                        self.assertEqual(
                            reloaded_tc.read_checkpoint.last_read_comment_date,
                            "2026-10-08T09:00:00Z",
                        )
                        self.assertEqual(
                            reloaded_tc.read_checkpoint.status, "Waiting on Red Hat"
                        )

                asyncio.run(run_test())
        finally:
            rh_support_lib.constants.GRAPHQL_URL = orig_gql
            shutil.rmtree(temp_dir)

    def test_cache_service_atomic_writes_and_keys(self):
        """Test CacheService atomic writes, TTL expiration, and deterministic key hashing."""
        from rh_support_lib.models import Case
        from rh_support_lib.services.cache import CacheService

        cache = CacheService(cache_dir=self.test_cache_dir, scope="unit-test")
        self.assertTrue(cache.enabled)

        # 1. Deterministic list cache key hashing
        key1 = cache.compute_list_cache_key(
            status=["Waiting on Red Hat", "Closed"], severity=["1", "2"]
        )
        # Permuted order should produce identical hash key
        key2 = cache.compute_list_cache_key(
            status=["Closed", "Waiting on Red Hat"], severity=["2", "1"]
        )
        self.assertEqual(key1, key2)

        # Different filter produces different key
        key3 = cache.compute_list_cache_key(status=["Closed"])
        self.assertNotEqual(key1, key3)

        # 2. Atomic list caching and TTL
        dummy_case = Case(
            case_number="99901",
            summary="Test Case",
            status="Waiting on Red Hat",
            severity="1 (Urgent)",
            product="RHEL",
            version="9.0",
            account_number="123",
            created_date="2026-01-01",
            last_modified_date="2026-01-02",
            created_by="User",
            last_modified_by="User",
            owner="Owner",
            contact_name="Contact",
            contact_sso="sso",
            case_type="Standard",
            id="50099901",
        )
        cache.set_case_list(key1, [dummy_case])
        cached_list = cache.get_case_list(key1, ttl=60)
        self.assertIsNotNone(cached_list)
        self.assertEqual(len(cached_list), 1)
        self.assertEqual(cached_list[0].case_number, "99901")

        # Expired TTL returns None
        self.assertIsNone(cache.get_case_list(key1, ttl=0))

    def test_tui_swr_cache_instant_render(self):
        """Test that TUI immediately renders details from cache without loading placeholder."""
        import asyncio

        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.models import Case, CaseComment, CaseDetails
        from rh_support_lib.tui.app import SupportApp

        dummy_case = Case(
            case_number="12345",
            summary="SWR Instant Case",
            status="Waiting on Red Hat",
            severity="1 (Urgent)",
            product="RHEL",
            version="9.0",
            account_number="123",
            created_date="2026-01-01",
            last_modified_date="2023-01-02T10:00:00Z",
            created_by="Tester",
            last_modified_by="Tester",
            owner="Owner",
            contact_name="Tester",
            contact_sso="tester",
            case_type="Standard",
            id="50012345",
        )
        details = CaseDetails(
            case=dummy_case,
            description="Cached instant description",
            comments=[
                CaseComment(
                    id="comment_2",
                    created_date="2023-01-03T10:00:00Z",
                    body="c2",
                    created_by="Tester",
                    is_public=True,
                )
            ],
            attachments=[],
            notified_users=[],
        )

        async def run_headless_swr():
            from unittest.mock import patch

            client = LegacyAPIClient("mock_access_token_123")
            with patch.dict(
                os.environ,
                {
                    "XDG_CACHE_HOME": self.test_cache_dir,
                    "RH_GRAPHQL_URL": f"{self.base_url}/graphql",
                    "RH_ATTACHMENT_API_URL": self.base_url,
                },
            ):
                app = SupportApp(client, config={"tui": {"poll_interval": 0}})
                app.case_service.cache_service._set_case_details(
                    "12345",
                    details,
                    last_modified_date="2023-01-02T10:00:00Z",
                    comment_coverage={
                        "complete": True,
                        "available_prefix": len(details.comments),
                    },
                )
                async with app.run_test() as pilot:
                    await pilot.pause()
                    # Trigger row selection for cached case 12345
                    from unittest.mock import MagicMock

                    event = MagicMock()
                    event.row_key.value = "12345"
                    app.on_row_selected(event)
                    # current_details should be populated IMMEDIATELY from cache!
                    self.assertIsNotNone(app.current_details)
                    self.assertEqual(
                        app.current_details.description, "Cached instant description"
                    )

        asyncio.run(run_headless_swr())

    def test_tui_background_activity_indicator(self):
        """Test that active background jobs update the TUI header subtitle with visual feedback."""
        import asyncio

        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.tui.app import SupportApp

        async def run_indicator_test():
            client = LegacyAPIClient("mock_access_token_123")
            app = SupportApp(client, config={"tui": {"poll_interval": 0}})
            async with app.run_test() as pilot:
                # On mount, fetch_cases is actively running and reflected in the header subtitle
                if app.sub_title:
                    self.assertIn("Fetching cases...", app.sub_title)

                # Wait for initial load worker to finish
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(app.sub_title, "")
                app._start_job("Syncing updates...")
                self.assertEqual(app.sub_title, "⟳ Syncing updates...")
                formatted = app.format_title(app.title, app.sub_title)
                self.assertIn("Syncing updates...", str(formatted))

                # Start concurrent job 2
                app._start_job("Uploading file.log...")
                self.assertEqual(
                    app.sub_title, "⟳ Syncing updates... | Uploading file.log..."
                )

                # Finish job 1
                app._finish_job("Syncing updates...")
                self.assertEqual(app.sub_title, "⟳ Uploading file.log...")

                # Finish job 2
                app._finish_job("Uploading file.log...")
                self.assertEqual(app.sub_title, "")

        asyncio.run(run_indicator_test())

    def test_tui_post_comment_refreshes_details_without_wiping_container(self):
        """Test that submitting a comment in the TUI refreshes details without stalling on 'Loading cases...'."""
        import asyncio
        from unittest.mock import MagicMock, patch

        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.tui.app import SupportApp

        async def run_post_comment_test():
            client = LegacyAPIClient("mock_access_token_123")
            with patch.dict(
                os.environ,
                {
                    "XDG_CACHE_HOME": self.test_cache_dir,
                    "RH_GRAPHQL_URL": f"{self.base_url}/graphql",
                    "RH_ATTACHMENT_API_URL": self.base_url,
                },
            ):
                app = SupportApp(client, config={"tui": {"poll_interval": 0}})
                async with app.run_test() as pilot:
                    await app.workers.wait_for_complete()
                    await pilot.pause()

                    # Select case 12345
                    event = MagicMock()
                    event.row_key.value = "12345"
                    app.on_row_selected(event)
                    await app.workers.wait_for_complete()
                    await pilot.pause()

                    # Post comment
                    app.execute_comment_submission(
                        "12345",
                        "New comment from test",
                        "Waiting on Customer",
                        app._comment_generation,
                    )
                    await app.workers.wait_for_complete()
                    await pilot.pause()

                    # Verify that detail container was NOT wiped and buttons are rendered
                    container = app.query_one("#case-detail-container")
                    self.assertTrue(len(container.query(".tui-comment-btn")) > 0)
                    self.assertIsNotNone(app.current_details)
                    self.assertEqual(app.selected_case_id, "12345")

        asyncio.run(run_post_comment_test())

    def test_search_cases_default(self):
        """Test search command default behavior searching cases."""
        res = self.run_cli(["search", "kernel panic"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("CASE #", res.stdout)
        self.assertIn("SUMMARY", res.stdout)
        self.assertIn("URL", res.stdout)
        self.assertIn("https://access.redhat.com/support/cases/", res.stdout)
        self.assertIn("SUBJ=kernel panic", res.stdout)

    def test_search_kcs_and_urls(self):
        """Test search command querying Knowledgebase (KCS) with solution/article URLs."""
        res = self.run_cli(["search", "boot mount", "--type", "kcs"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("ID", res.stdout)
        self.assertIn("TYPE", res.stdout)
        self.assertIn("7008126", res.stdout)
        self.assertIn("Solution", res.stdout)
        self.assertIn("Failed to mount API filesystem", res.stdout)
        self.assertIn("https://access.redhat.com/solutions/7008126", res.stdout)
        self.assertIn("https://access.redhat.com/articles/6911201", res.stdout)

    def test_search_all_combined(self):
        """Test search --type all queries both cases and knowledgebase."""
        res = self.run_cli(["search", "ingress", "--type", "all"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("SUPPORT CASES", res.stdout)
        self.assertIn("KNOWLEDGEBASE", res.stdout)
        self.assertIn("SOLUTIONS", res.stdout)
        self.assertIn("https://access.redhat.com/solutions/7008126", res.stdout)

    def test_search_open_browser_flag(self):
        """Test search with --open flag announces opening browser URL."""
        res = self.run_cli(["search", "boot mount", "--type", "kcs", "--open"])
        self.assertEqual(res.returncode, 0)
        self.assertIn(
            "Opening https://access.redhat.com/solutions/7008126 in browser...",
            res.stdout,
        )

    def test_search_local_cache(self):
        """Test search --local searches cached cases across description and comments without hitting network."""
        import json

        # Pre-populate a unique case in the isolated test cache
        case_file = os.path.join(self.case_cache().cases_dir, "987654.json")
        os.makedirs(os.path.dirname(case_file), exist_ok=True)
        cached_record = {
            "cached_at": time.time(),
            "last_modified_date": "2026-10-12T00:00:00Z",
            "status": "Waiting on Customer",
            "case": {
                "case_number": "987654",
                "summary": "Local Cache Case",
                "status": "Waiting on Customer",
                "severity": "2 (High)",
                "product": "RHEL",
            },
            "description": "Unique description token: XYZ_LOCAL_DEBUG",
            "comments": [
                {
                    "id": "c1",
                    "body": "Comment containing secret diagnostic fingerprint: ALPHA_BETA_DIAG",
                    "created_date": "2026-10-12T01:00:00Z",
                    "created_by": "Red Hat Support",
                }
            ],
            "attachments": [],
            "notified_users": [],
        }
        with open(case_file, "w") as f:
            json.dump(cached_record, f)

        res = self.run_cli(["search", "ALPHA_BETA_DIAG", "--local"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("987654", res.stdout)
        self.assertIn("Local Cache Case", res.stdout)

    def test_search_structured_output(self):
        """Test search with --format json and yaml."""
        res_json = self.run_cli(
            ["search", "boot mount", "--type", "kcs", "--format", "json"]
        )
        self.assertEqual(res_json.returncode, 0)
        data = json.loads(res_json.stdout)
        self.assertTrue(isinstance(data, list))
        self.assertTrue(any(d.get("id") == "7008126" for d in data))
        self.assertTrue(
            any(
                "https://access.redhat.com/solutions/7008126" in d.get("url", "")
                for d in data
            )
        )

    def test_search_kcs_language_and_doc_id(self):
        """Test KCS search omits docs URL as ID, filters non-English by default, and allows --language."""
        # 1. Default (en): excludes Japanese doc, includes English doc with omitted ID
        res_default = self.run_cli(["search", "networking", "--type", "kcs"])
        self.assertEqual(res_default.returncode, 0)
        self.assertIn("Configuring Networking on RHEL 9", res_default.stdout)
        self.assertIn("https://docs.redhat.com/en/documentation/", res_default.stdout)
        # Japanese result excluded
        self.assertNotIn("RHEL 9 ネットワークの設定", res_default.stdout)
        # Ensure documentation ID does not display the full docs URL as an ID
        for line in res_default.stdout.splitlines():
            if "Configuring Networking" in line:
                self.assertNotIn("https://docs.redhat.com", line.split()[0])

        # 2. Querying with --language ja returns Japanese result
        res_ja = self.run_cli(
            ["search", "networking", "--type", "kcs", "--language", "ja"]
        )
        self.assertEqual(res_ja.returncode, 0)
        self.assertIn("RHEL 9 ネットワークの設定", res_ja.stdout)
        self.assertIn("https://docs.redhat.com/ja/documentation/", res_ja.stdout)

    def test_search_bookmarks(self):
        """Test search command incorporates default bookmark and supports --no-default-bookmark."""
        import shutil

        import yaml

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        self.env["HOME"] = temp_home
        cfg_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        os.makedirs(cfg_dir, exist_ok=True)
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_home, ".config")

        config_data = {
            "default_bookmark": "test-bm",
            "bookmarks": {
                "test-bm": {
                    "product": "Red Hat OpenShift Container Platform",
                    "status": "Waiting on Customer Action Required",
                }
            },
        }
        with open(os.path.join(cfg_dir, "config.yaml"), "w") as f:
            yaml.dump(config_data, f)

        try:
            # 1. Default search incorporates bookmark filters into query
            res_bm = self.run_cli(["search", "cert", "--format", "json"])
            self.assertEqual(res_bm.returncode, 0)
            self.assertIn("STAT=Waiting on Customer Action Required", res_bm.stdout)
            self.assertIn("PROD=Red Hat OpenShift Container Platform", res_bm.stdout)

            # 2. --no-default-bookmark skips bookmark filters
            res_no_bm = self.run_cli(
                ["search", "cert", "--no-default-bookmark", "--format", "json"]
            )
            self.assertEqual(res_no_bm.returncode, 0)
            self.assertNotIn("PROD=Red Hat OpenShift", res_no_bm.stdout)

            # 3. CLI override takes precedence over bookmark
            res_override = self.run_cli(
                ["search", "cert", "--product", "RHEL", "--format", "json"]
            )
            self.assertEqual(res_override.returncode, 0)
            self.assertIn("PROD=RHEL", res_override.stdout)
            self.assertNotIn("PROD=Red Hat OpenShift", res_override.stdout)
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            shutil.rmtree(temp_home)

    def test_all_cli_commands_work_as_expected(self):
        """Comprehensive end-to-end test verifying that every registered CLI command operates as expected."""

        import yaml

        temp_dir = tempfile.mkdtemp()
        try:
            # 1. Setup local template in user's config directory
            config_dir = os.path.join(temp_dir, ".config", "rh-support-cli")
            tpl_dir = os.path.join(config_dir, "templates")
            os.makedirs(tpl_dir, exist_ok=True)
            with open(os.path.join(tpl_dir, "sample.yaml"), "w") as f:
                yaml.safe_dump(
                    {
                        "_template_description": "Sample test template",
                        "severity": "Low",
                    },
                    f,
                )

            # Create scratch files for description, comment, and attachment
            desc_file = os.path.join(temp_dir, "desc.txt")
            with open(desc_file, "w") as f:
                f.write("Test ticket description")

            comm_file = os.path.join(temp_dir, "comment.txt")
            with open(comm_file, "w") as f:
                f.write("Automated test comment")

            close_file = os.path.join(temp_dir, "close.txt")
            with open(close_file, "w") as f:
                f.write("Closing case cleanly")

            att_file = os.path.join(temp_dir, "diag.txt")
            with open(att_file, "w") as f:
                f.write("Log file diagnostics")

            down_file = os.path.join(temp_dir, "downloaded.txt")
            down_file2 = os.path.join(temp_dir, "downloaded2.txt")

            orig_home = self.env.get("HOME")
            self.env["HOME"] = temp_dir

            # List of all commands, expected stdout substrings, and optional stdin input
            commands_to_verify = [
                (["list"], "NUMBER", None),
                (["list", "--status", "open"], "NUMBER", None),
                (["follow", "12345"], "Added", None),
                (["list", "--following"], "12345", None),
                (["list", "--json"], "[", None),
                (["show", "-c", "12345", "--no-pager"], "CASE: 12345", None),
                (
                    ["show", "-c", "12345", "--newest-first", "--no-pager"],
                    "newest first",
                    None,
                ),
                (
                    ["show", "-c", "12345", "--oldest-first", "--no-pager"],
                    "oldest first",
                    None,
                ),
                (
                    ["link", "-c", "12345"],
                    "https://access.redhat.com/support/cases/12345",
                    None,
                ),
                (
                    [
                        "create",
                        "--product",
                        "RHEL",
                        "--version",
                        "9.0",
                        "--summary",
                        "Comprehensive Test Case",
                        "--description-file",
                        desc_file,
                        "--severity",
                        "Low",
                        "--type",
                        "Bug",
                    ],
                    "created",
                    "y\n",
                ),
                (["comment", "-c", "12345", "-f", comm_file], "Comment added", None),
                (
                    ["comment", "-c", "12345", "-f", comm_file, "--no-status-change"],
                    "Comment added",
                    None,
                ),
                (["close", "-c", "12345", "-f", close_file], "Closed", None),
                (
                    ["apply", "-c", "12345", "-t", "sample", "--dry-run"],
                    "Dry-run",
                    None,
                ),
                (["list-templates"], "Template: sample", None),
                (["list-template", "--simple-output"], "Template:    sample", None),
                (["list-templates", "--json"], "Sample test template", None),
                (["attach", "-c", "12345", "-f", att_file], "attached", None),
                (["list-attachments", "12345"], "Attachments for Case #12345", None),
                (
                    ["attachment", "list", "-c", "12345"],
                    "Attachments for Case #12345",
                    None,
                ),
                (
                    ["attachment", "upload", "-c", "12345", "-f", att_file],
                    "attached",
                    None,
                ),
                (["attachment", "status", "-c", "12345"], "COMPLETED", None),
                (
                    [
                        "attachment",
                        "download",
                        "-c",
                        "12345",
                        "-a",
                        "att_1001_uuid",
                        "-o",
                        down_file,
                    ],
                    "downloaded",
                    None,
                ),
                (
                    [
                        "attachment",
                        "delete",
                        "-c",
                        "12345",
                        "-a",
                        "att_1001_uuid",
                        "-y",
                    ],
                    "deleted",
                    None,
                ),
                (
                    [
                        "download",
                        "-c",
                        "12345",
                        "-a",
                        "att_1001_uuid",
                        "-o",
                        down_file2,
                    ],
                    "downloaded",
                    None,
                ),
                (["following"], "12345", None),
                (["sync"], "Sync complete", None),
                (["updates"], "No unread updates", None),
                (["unfollow", "12345"], "Removed", None),
                (["search", "kernel"], "CASE #", None),
                (["search", "kernel", "--type", "kcs"], "Solution", None),
                (["search", "kernel", "--type", "all"], "CASE #", None),
                (["completion"], "register-python-argcomplete", None),
                (
                    ["install-systemd", "--interval", "15m"],
                    "Installed systemd user",
                    None,
                ),
                (["systemd", "status"], "Timer unit:", None),
                (["systemd", "uninstall"], "uninstalled", None),
            ]

            for cmd_args, expected_str, stdin_input in commands_to_verify:
                with self.subTest(command=" ".join(cmd_args)):
                    res = self.run_cli(cmd_args, input=stdin_input)
                    self.assertEqual(
                        res.returncode,
                        0,
                        f"Command failed (exit code {res.returncode}): {' '.join(cmd_args)}\nSTDERR: {res.stderr}\nSTDOUT: {res.stdout}",
                    )
                    self.assertIn(
                        expected_str,
                        Text.from_ansi(res.stdout).plain,
                        f"Expected string '{expected_str}' not in output for command: {' '.join(cmd_args)}\nSTDOUT: {res.stdout}",
                    )
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            shutil.rmtree(temp_dir)

    def test_bash_completion_and_dynamic_completers(self):
        """Test dynamic bash auto-completion completers for products, statuses, severities, bookmarks, templates, and cases."""
        import argparse
        import json
        import shutil

        import yaml

        from rh_support_lib.completers import (
            bookmark_completer,
            case_completer,
            case_type_completer,
            product_completer,
            setup_completers,
            severity_completer,
            status_completer,
            template_completer,
        )

        temp_dir = tempfile.mkdtemp()
        config_dir = os.path.join(temp_dir, ".config", "rh-support-cli")
        state_dir = os.path.join(temp_dir, ".local", "state", "rh-support-cli")
        env_patch = mock.patch.dict(
            os.environ,
            {
                "HOME": temp_dir,
                "XDG_CACHE_HOME": os.path.join(temp_dir, ".cache"),
                "XDG_STATE_HOME": os.path.dirname(state_dir),
                "RH_SUPPORT_STATE": os.path.join(state_dir, "state.yaml"),
                "RH_SUPPORT_CONFIG": os.path.join(config_dir, "config.yaml"),
                "RH_SUPPORT_TEMPLATES": os.path.join(config_dir, "templates"),
            },
        )
        env_patch.start()
        try:
            # 1. Product Completer with fallback defaults
            prods = product_completer("Red Hat Open")
            self.assertIn("Red Hat OpenShift Container Platform", prods)
            self.assertIn("Red Hat OpenStack Platform", prods)
            self.assertNotIn("Red Hat Enterprise Linux", prods)

            # Product Completer with custom cached products in products.json
            catalog_dir = os.path.join(temp_dir, ".cache", "rh-support-cli", "catalog")
            os.makedirs(catalog_dir, exist_ok=True)
            with open(os.path.join(catalog_dir, "products.json"), "w") as f:
                json.dump(
                    {
                        "products": [
                            {"name": "Custom In-House Product"},
                            {"name": "Red Hat Custom Stack"},
                        ]
                    },
                    f,
                )
            cached_prods = product_completer("Custom")
            self.assertIn("Custom In-House Product", cached_prods)
            self.assertIn("Red Hat Custom Stack", cached_prods)

            # 2. Status Completer (list/search context vs comment/close context)
            class FakeArgs:
                command = "list"

            list_statuses = status_completer("o", parsed_args=FakeArgs())
            self.assertIn("open", list_statuses)
            self.assertNotIn("none", list_statuses)

            FakeArgs.command = "comment"
            comment_statuses = status_completer("n", parsed_args=FakeArgs())
            self.assertIn("none", comment_statuses)

            # 3. Severity Completer
            sevs = severity_completer("u")
            self.assertIn("Urgent", sevs)

            # 4. Bookmark Completer
            os.makedirs(config_dir, exist_ok=True)
            with open(os.path.join(config_dir, "config.yaml"), "w") as f:
                yaml.safe_dump(
                    {"bookmarks": {"my-team-cases": {}, "ocp-urgent": {}}}, f
                )
            bms = bookmark_completer("ocp")
            self.assertIn("ocp-urgent", bms)

            # 5. Template Completer
            tpl_dir = os.path.join(config_dir, "templates")
            os.makedirs(tpl_dir, exist_ok=True)
            with open(os.path.join(tpl_dir, "custom_issue.yaml"), "w") as f:
                f.write("summary: test\n")
            tmpls = template_completer("custom")
            self.assertIn("custom_issue", tmpls)

            # 6. Case Completer
            os.makedirs(state_dir, exist_ok=True)
            with open(os.path.join(state_dir, "state.yaml"), "w") as f:
                yaml.safe_dump(
                    {
                        "users": {
                            "jdeveloper": {
                                "tracked_cases": {"04526149": {}},
                                "following": {"12345678": {}},
                            }
                        }
                    },
                    f,
                )
            cases = case_completer("045")
            self.assertIn("04526149", cases)

            # 7. Case Type Completer
            types = case_type_completer("s")
            self.assertIn("Standard", types)

            # 8. setup_completers attachment & choices protection
            parser = argparse.ArgumentParser(prog="rh-support-cli")
            subs = parser.add_subparsers(dest="cmd")
            p_list = subs.add_parser("list")
            p_act = p_list.add_argument("--product")
            s_act = p_list.add_argument("-s", "--status")
            p_apply = subs.add_parser("apply")
            tmpl_act = p_apply.add_argument("-t", "--template")
            p_search = subs.add_parser("search")
            type_act = p_search.add_argument(
                "-t", "--type", choices=["cases", "kcs", "all"]
            )
            setup_completers(parser)
            self.assertEqual(getattr(p_act, "completer", None), product_completer)
            self.assertEqual(getattr(s_act, "completer", None), status_completer)
            self.assertEqual(getattr(tmpl_act, "completer", None), template_completer)
            # Choices actions must never be overridden with a custom completer
            self.assertIsNone(getattr(type_act, "completer", None))
            self.assertEqual(type_act.choices, ["cases", "kcs", "all"])
        finally:
            env_patch.stop()
            shutil.rmtree(temp_dir)

    def test_update_catalog_script(self):
        """Test update_catalog script execution and JSON output writing."""
        import json
        import shutil
        import subprocess

        temp_dir = tempfile.mkdtemp()
        out_file = os.path.join(temp_dir, "test_products.json")
        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts",
            "update_catalog.py",
        )
        try:
            res = subprocess.run(
                [sys.executable, script_path, "--output", out_file, "--no-cache", "-q"],
                env=self.env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                res.returncode, 0, f"Script failed: {res.stderr}\n{res.stdout}"
            )
            self.assertTrue(os.path.isfile(out_file))
            with open(out_file, "r") as f:
                data = json.load(f)
            self.assertIn("products", data)
            self.assertIn("count", data)
            self.assertIn("updated_at", data)
            self.assertTrue(len(data["products"]) >= 1)
        finally:
            shutil.rmtree(temp_dir)

    def test_cli_bash_completion_e2e_scenarios(self):
        """End-to-end integration test of actual bash tab-completion invocations across CLI commands."""

        import yaml

        temp_dir = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_state = self.env.get("XDG_STATE_HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        self.env["HOME"] = temp_dir
        self.env["XDG_STATE_HOME"] = os.path.join(temp_dir, ".local", "state")
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_dir, ".config")

        try:
            # 1. Setup sample templates, bookmarks, and state
            config_dir = os.path.join(temp_dir, ".config", "rh-support-cli")
            tpl_dir = os.path.join(config_dir, "templates")
            os.makedirs(tpl_dir, exist_ok=True)
            with open(os.path.join(tpl_dir, "base_bit.yaml"), "w") as f:
                f.write("summary: bit\n")
            with open(os.path.join(tpl_dir, "rh_watchers.yaml"), "w") as f:
                f.write("summary: watchers\n")

            with open(os.path.join(config_dir, "config.yaml"), "w") as f:
                yaml.safe_dump({"bookmarks": {"ocp-urgent": {}, "rhel-kernel": {}}}, f)

            state_dir = os.path.join(temp_dir, ".local", "state", "rh-support-cli")
            os.makedirs(state_dir, exist_ok=True)
            with open(os.path.join(state_dir, "state.yaml"), "w") as f:
                yaml.safe_dump(
                    {
                        "users": {
                            "jdeveloper": {
                                "tracked_cases": {"04526149": {}},
                                "following": {"12345678": {}},
                            }
                        }
                    },
                    f,
                )

            # Scenario 1: search --type MUST complete choices (cases, kcs, all) and NOT templates
            res_search_type = self.run_completion("rh-support-cli search --type ")
            self.assertEqual(res_search_type, ["cases", "kcs", "all"])
            self.assertNotIn("base_bit", res_search_type)

            # Scenario 2: apply -t MUST complete template names
            res_apply_t = self.run_completion("rh-support-cli apply -t ")
            self.assertIn("base_bit", res_apply_t)
            self.assertIn("rh_watchers", res_apply_t)

            # Scenario 3: list --status completes query statuses
            res_list_status = self.run_completion("rh-support-cli list --status ")
            self.assertIn("open", res_list_status)
            self.assertIn("closed", res_list_status)
            self.assertIn("in_progress", res_list_status)

            # Scenario 4: comment -s completes comment transition statuses
            res_comment_status = self.run_completion(
                "rh-support-cli comment -c 12345 -s "
            )
            self.assertIn("none", res_comment_status)
            self.assertIn("keep", res_comment_status)
            self.assertIn("redhat", res_comment_status)

            # Scenario 5: list --severity completes severity values
            res_severity = self.run_completion("rh-support-cli list --severity ")
            self.assertIn("Low", res_severity)
            self.assertIn("Urgent", res_severity)

            # Scenario 6: search --product completes products from catalog
            res_prod = self.run_completion("rh-support-cli search --product Red")
            self.assertTrue(
                any(
                    "Enterprise\\ Linux" in p or "Enterprise Linux" in p
                    for p in res_prod
                )
            )
            self.assertTrue(any("OpenShift" in p for p in res_prod))

            # Scenario 7: list -b completes configured bookmarks
            res_bm = self.run_completion("rh-support-cli list -b ")
            self.assertIn("ocp-urgent", res_bm)
            self.assertIn("rhel-kernel", res_bm)

            # Scenario 8: show -c completes tracked case numbers
            res_case = self.run_completion("rh-support-cli show -c ")
            self.assertIn("04526149", res_case)
            self.assertIn("12345678", res_case)

            # Scenario 9: create --type completes ticket types
            res_create_type = self.run_completion("rh-support-cli create --type ")
            self.assertIn("Standard", res_create_type)
            self.assertIn("Bug", res_create_type)

            # Scenario 10: show --order completes order values
            res_order = self.run_completion("rh-support-cli show --order ")
            self.assertEqual(res_order, ["newest", "oldest", "desc", "asc"])

            # Scenario 11: list --format completes format choices
            res_fmt = self.run_completion("rh-support-cli list --format ")
            self.assertEqual(res_fmt, ["text", "json", "yaml"])

            # Scenario 12: tui --layout completes layout choices
            res_layout = self.run_completion("rh-support-cli tui --layout ")
            self.assertEqual(res_layout, ["vertical", "horizontal", "mailbox"])

        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_state:
                self.env["XDG_STATE_HOME"] = orig_state
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            shutil.rmtree(temp_dir)


class TestTemplateFailureSafety(unittest.TestCase):
    def setUp(self):
        from pathlib import Path

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name, body in {
            "missing-include": "include_templates: [absent]\nseverity: Low",
            "cycle": "include_templates: [cycle]\nseverity: Low",
            "undefined": 'comment: "{{ required_variable }}"',
            "syntax": 'comment: "{{ broken"',
            "wrong-root": "- not-a-mapping",
            "wrong-includes": "include_templates: 42",
            "invalid-yaml": "comment: [",
            "optional": "comment: \"{{ optional_variable | default('fallback') }}\"",
        }.items():
            (self.root / f"{name}.yaml").write_text(body)

    def test_engine_rejects_invalid_templates_instead_of_returning_partial_data(self):
        from rh_support_lib.templates import TemplateEngine

        engine = TemplateEngine(str(self.root))
        for name in (
            "absent",
            "missing-include",
            "cycle",
            "undefined",
            "syntax",
            "wrong-root",
            "wrong-includes",
            "invalid-yaml",
        ):
            with (
                self.subTest(template=name),
                self.assertRaisesRegex(ValueError, "template|Template"),
            ):
                engine.process([name], {})
        self.assertEqual(engine.process(["optional"], {}), {"comment": "fallback"})

    def test_mutating_commands_stop_on_template_failure(self):
        from types import SimpleNamespace

        from rh_support_lib.commands.actions import cmd_close, cmd_comment
        from rh_support_lib.commands.apply_template import cmd_apply
        from rh_support_lib.commands.create_case import cmd_create
        from rh_support_lib.models import Case, CaseDetails

        args = SimpleNamespace(
            case="100",
            template=["absent"],
            template_var=[],
            no_default_template=True,
            file=None,
            edit=False,
            status=None,
            include_previous_comments=0,
            product="RHEL",
            version="9.0",
            summary="summary",
            severity="Low",
            type="Standard",
            description_file=str(self.root / "description.txt"),
            attachment=[],
            dry_run=False,
            simple_output=True,
        )
        (self.root / "description.txt").write_text("description")
        for command, module in (
            (cmd_comment, "actions"),
            (cmd_close, "actions"),
            (cmd_create, "create_case"),
            (cmd_apply, "apply_template"),
        ):
            for template in ("absent", "undefined", "syntax"):
                with self.subTest(command=command.__name__, template=template):
                    args.template = [template]
                    with (
                        mock.patch.dict(
                            os.environ, {"RH_SUPPORT_TEMPLATES": str(self.root)}
                        ),
                        mock.patch(
                            f"rh_support_lib.commands.{module}.CaseService"
                        ) as service,
                        mock.patch("builtins.input", return_value="y"),
                        mock.patch("sys.stdout", new_callable=io.StringIO),
                    ):
                        service.return_value.get_case_details.return_value = (
                            CaseDetails(
                                Case("100", "old", "Open", "High"), "description"
                            )
                        )
                        with self.assertRaisesRegex(SystemExit, "Template Error"):
                            if command in (cmd_create, cmd_apply):
                                command(args, mock.Mock(), {})
                            else:
                                command(args, mock.Mock())
                        for mutation in ("add_comment", "create_case", "update_case"):
                            getattr(service.return_value, mutation).assert_not_called()

    def test_empty_rendered_comment_cannot_change_status(self):
        from types import SimpleNamespace

        from rh_support_lib.commands.actions import cmd_close, cmd_comment

        (self.root / "empty.yaml").write_text(
            "comment: \"{{ optional | default('') }}\""
        )
        for command in (cmd_comment, cmd_close):
            with self.subTest(command=command.__name__):
                args = SimpleNamespace(
                    template=["empty"], template_var=[], file=None, edit=False
                )
                with (
                    mock.patch.dict(
                        os.environ, {"RH_SUPPORT_TEMPLATES": str(self.root)}
                    ),
                    mock.patch(
                        "rh_support_lib.commands.actions.CaseService"
                    ) as service,
                    self.assertRaisesRegex(
                        SystemExit, "Template Error: Rendered comment is empty"
                    ),
                ):
                    command(args, mock.Mock())
                service.assert_not_called()


class TestCaseCreationLookupSafety(unittest.TestCase):
    def setUp(self):
        from rh_support_lib.services.cases import CaseService

        self.client = mock.Mock()
        self.service = CaseService(self.client, state_service=mock.Mock())
        self.lookups = {
            "redhat_support_uiapi": {
                "Contact": {
                    "RedHatSupportContact": {
                        "edges": [
                            {
                                "node": {
                                    "Id": "contact",
                                    "AccountId": {"value": "account"},
                                }
                            }
                        ]
                    }
                },
                "Account": {
                    "RedHatSupportAccount": {"edges": [{"node": {"Id": "account"}}]}
                },
                "RecordType": {
                    "RedHatSupportRecordType": {
                        "edges": [{"node": {"Id": "record-type"}}]
                    }
                },
                "Product": {
                    "RedHatSupportProduct2": {
                        "edges": [
                            {
                                "node": {
                                    "Id": "product",
                                    "Name": {"value": "RHEL"},
                                    "VersionName__c": {"value": "9.0"},
                                }
                            }
                        ]
                    }
                },
            }
        }

        def execute(query, variables, **kwargs):
            if "ResolveCaseLookupData" in query:
                return self.lookups
            if "CreateNewCase" in query:
                return {
                    "redhat_support_uiapi": {
                        "CaseCreate": {
                            "RedHatSupportRecord": {"CaseNumber__c": {"value": "100"}}
                        }
                    }
                }
            raise AssertionError("Unexpected query")

        self.client.execute_graphql.side_effect = execute

    def create(self, **kwargs):
        return self.service.create_case(
            "summary", "description", product_name="RHEL", sso_user="operator", **kwargs
        )

    def assert_no_creation(self):
        self.assertFalse(
            any(
                "CreateNewCase" in call.args[0]
                for call in self.client.execute_graphql.call_args_list
            )
        )

    def test_missing_malformed_or_ambiguous_entities_prevent_creation(self):
        from copy import deepcopy

        original = deepcopy(self.lookups)
        for alias, field in (
            ("Contact", "RedHatSupportContact"),
            ("Account", "RedHatSupportAccount"),
            ("RecordType", "RedHatSupportRecordType"),
            ("Product", "RedHatSupportProduct2"),
        ):
            for edges in (
                [],
                None,
                [{"node": None}],
                [{"node": {"Id": ""}}],
                [{"node": {"Id": "a"}}, {"node": {"Id": "b"}}],
            ):
                with self.subTest(alias=alias, edges=edges):
                    self.lookups = deepcopy(original)
                    self.lookups["redhat_support_uiapi"][alias][field]["edges"] = edges
                    self.client.execute_graphql.reset_mock()
                    with self.assertRaisesRegex(ValueError, "lookup"):
                        self.create(account_number="123")
                    self.assert_no_creation()

    def test_conflicting_or_missing_contact_account_prevents_creation(self):
        node = self.lookups["redhat_support_uiapi"]["Contact"]["RedHatSupportContact"][
            "edges"
        ][0]["node"]
        for account in ("other-account", ""):
            with self.subTest(account=account):
                node["AccountId"]["value"] = account
                self.client.execute_graphql.reset_mock()
                with self.assertRaisesRegex(ValueError, "account"):
                    self.create(account_number="123")
                self.assert_no_creation()

    def test_version_mismatch_prevents_creation(self):
        with self.assertRaisesRegex(ValueError, "version"):
            self.create(version_name="8.0")
        self.assert_no_creation()

    def test_omitted_account_uses_only_resolved_contact_account(self):
        self.assertEqual(
            self.create(extra_fields={"accountNumber": "ignored-routing-key"}), "100"
        )
        calls = self.client.execute_graphql.call_args_list
        self.assertEqual(calls[0].args[1]["ssoUser"], "operator")
        payload = calls[-1].args[1]["input"]["RedHatSupportCase"]
        self.assertEqual(payload["AccountId"], "account")
        self.assertEqual(payload["ContactId"], "contact")
        self.assertEqual(payload["ProductId"], "product")
        self.assertNotIn("accountNumber", payload)

    def test_missing_identity_never_falls_back_to_os_user(self):
        self.client.get_token.return_value = "opaque-access-token"
        with (
            mock.patch.dict(
                os.environ,
                {
                    "REDHAT_SUPPORT_USERNAME": "",
                    "RH_SSO_USERNAME": "",
                    "RH_USERNAME": "",
                    "USER": "unrelated-user",
                },
            ),
            self.assertRaisesRegex(ValueError, "SSO"),
        ):
            self.service.create_case("summary", "description", product_name="RHEL")
        self.client.execute_graphql.assert_not_called()

    def test_identity_claim_and_explicit_account_are_checked(self):
        import base64

        claims = (
            base64.urlsafe_b64encode(
                json.dumps({"preferred_username": "authenticated-user"}).encode()
            )
            .decode()
            .rstrip("=")
        )
        self.client.get_token.return_value = f"header.{claims}.signature"
        with mock.patch.dict(
            os.environ,
            {"REDHAT_SUPPORT_USERNAME": "", "RH_SSO_USERNAME": "", "RH_USERNAME": ""},
        ):
            self.service.create_case(
                "summary", "description", product_name="RHEL", account_number="123"
            )
        lookup = self.client.execute_graphql.call_args_list[0]
        self.assertEqual(lookup.args[1]["ssoUser"], "authenticated-user")
        self.assertEqual(lookup.args[1]["accountNumber"], "123")
        self.assertTrue(lookup.args[1]["resolveAccount"])
        self.assertEqual(lookup.args[0].count("first: 2"), 4)


class TestTemplateApplicationSafety(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace

        from rh_support_lib.models import Case, CaseDetails

        self.args = SimpleNamespace(
            case="100",
            template=["policy"],
            template_var=[],
            dry_run=False,
            simple_output=True,
        )
        engine = mock.patch("rh_support_lib.commands.apply_template.TemplateEngine")
        self.engine = engine.start().return_value
        self.addCleanup(engine.stop)
        service = mock.patch("rh_support_lib.commands.apply_template.CaseService")
        self.service = service.start().return_value
        self.addCleanup(service.stop)
        self.service.get_case_details.return_value = CaseDetails(
            Case("100", "old", "Open", "High"), "old description"
        )

    def test_unsupported_fields_and_watchers_fail_before_mutation(self):
        from rh_support_lib.commands.apply_template import cmd_apply

        for field in (
            "product",
            "version",
            "type",
            "watchers",
            "notified_users",
            "notifiedUsers",
            "unknown",
        ):
            for dry_run in (False, True):
                with self.subTest(field=field, dry_run=dry_run):
                    self.args.dry_run = dry_run
                    self.engine.process.return_value = {
                        "severity": "Low",
                        field: ["oncall"]
                        if "user" in field or field == "watchers"
                        else "changed",
                    }
                    with (
                        self.assertRaisesRegex(SystemExit, field),
                        mock.patch("sys.stdout", new_callable=io.StringIO) as output,
                    ):
                        cmd_apply(self.args, mock.Mock(), {})
                    self.assertNotIn("Success", output.getvalue())
                    self.assertNotIn("already adheres", output.getvalue())
                    self.service.update_case.assert_not_called()

    def test_supported_plan_and_execution_use_the_same_fields(self):
        from rh_support_lib.commands.apply_template import cmd_apply

        fields = {
            "status": "Closed",
            "severity": "Low",
            "summary": "new",
            "description": "new description",
        }
        self.engine.process.return_value = {**fields, "_desc": "metadata"}
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.args.dry_run = True
            with self.assertRaises(SystemExit) as result:
                cmd_apply(self.args, mock.Mock(), {})
            self.assertEqual(result.exception.code, 0)
            self.service.update_case.assert_not_called()
            self.args.dry_run = False
            cmd_apply(self.args, mock.Mock(), {})
        self.service.update_case.assert_called_once_with("100", **fields)

    def test_watcher_only_template_is_not_reported_compliant(self):
        from rh_support_lib.commands.apply_template import cmd_apply

        self.engine.process.return_value = {"watchers": ["oncall"]}
        with self.assertRaisesRegex(SystemExit, "Unsupported.*watchers"):
            cmd_apply(self.args, mock.Mock(), {})
        self.service.update_case.assert_not_called()

    def test_values_ignored_by_update_service_are_not_reported_as_applied(self):
        from rh_support_lib.commands.apply_template import cmd_apply

        for field, value in (("summary", ""), ("status", False), ("description", [])):
            with self.subTest(field=field):
                self.engine.process.return_value = {field: value}
                with self.assertRaisesRegex(SystemExit, "Invalid value"):
                    cmd_apply(self.args, mock.Mock(), {})
                self.service.update_case.assert_not_called()


class TestContentCacheIsolation(unittest.TestCase):
    def setUp(self):

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = mock.patch.dict(
            os.environ,
            {
                "HOME": self.directory.name,
                "XDG_CACHE_HOME": self.directory.name + "/cache",
                "XDG_STATE_HOME": self.directory.name + "/state",
                "REDHAT_SUPPORT_OFFLINE_TOKEN": "credential-A",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        for target in ("requests.request", "requests.post", "getpass.getpass"):
            patcher = mock.patch(
                target,
                side_effect=AssertionError(
                    "Offline operation attempted authentication/network"
                ),
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def service(self, credential="credential-A", backend="https://graphql.example/a"):
        from rh_support_lib.api import RedHatAPIClient
        from rh_support_lib.services.cases import CaseService

        with mock.patch.dict(os.environ, {"REDHAT_SUPPORT_OFFLINE_TOKEN": credential}):
            return CaseService(RedHatAPIClient(), graphql_url=backend)

    def test_cases_lists_internal_ids_and_local_search_are_isolated(self):
        from rh_support_lib.models import Case, CaseDetails

        a = self.service()
        case = Case("100", "private A", "Open", "Low", id="internal-A")
        a.cache_service.set_case_details("100", CaseDetails(case, "private diagnostic"))
        key = a.cache_service.compute_list_cache_key()
        a.cache_service.set_case_list(key, [case])
        for other in (
            self.service("credential-B"),
            self.service(backend="https://graphql.example/b"),
        ):
            with self.subTest(namespace=other.graphql_url):
                self.assertIsNone(other.cache_service.get_case_record("100"))
                self.assertIsNone(other.cache_service.get_case_list(key))
                self.assertEqual(other.search_cases("private", local_only=True), [])
                other.cache_service.invalidate_case("100")
                self.assertIsNotNone(a.cache_service.get_case_record("100"))
        again = self.service()
        self.assertEqual(
            again.search_cases("diagnostic", local_only=True)[0].id, "internal-A"
        )
        self.assertEqual(again.cache_service.get_case_list(key)[0].summary, "private A")

    def test_no_credentials_or_legacy_cache_are_used_for_offline_search(self):
        from pathlib import Path

        from rh_support_lib.api import RedHatAPIClient
        from rh_support_lib.services.cases import CaseService

        legacy = Path(self.directory.name) / "cache/rh-support-cli/cases/100.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(
            json.dumps({"case": {"case_number": "100", "summary": "legacy secret"}})
        )
        with mock.patch.dict(os.environ, {"REDHAT_SUPPORT_OFFLINE_TOKEN": ""}):
            anonymous = CaseService(RedHatAPIClient())
            with self.assertRaisesRegex(ValueError, "credential"):
                anonymous.search_cases("secret", local_only=True)
        self.assertEqual(self.service().search_cases("secret", local_only=True), [])

    def test_client_credential_binding_survives_environment_changes(self):
        a = self.service()
        original = a.cache_service.cases_dir
        from rh_support_lib.services.cases import CaseService

        with mock.patch.dict(
            os.environ, {"REDHAT_SUPPORT_OFFLINE_TOKEN": "credential-B"}
        ):
            same_client = CaseService(a.api_client, graphql_url=a.graphql_url)
            self.assertEqual(same_client.cache_service.cases_dir, original)
            self.assertNotEqual(
                self.service("credential-B").cache_service.cases_dir, original
            )

    def test_legacy_tokens_are_isolated_and_unbound_caches_are_disabled(self):
        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.cache import CacheService
        from rh_support_lib.services.cases import CaseService

        a = CaseService(LegacyAPIClient("access-A"))
        b = CaseService(LegacyAPIClient("access-B"))
        self.assertNotEqual(a.cache_service.cases_dir, b.cache_service.cases_dir)
        self.assertNotIn("access-A", a.cache_service.cases_dir)
        self.assertFalse(CacheService().enabled)

    def test_case_identifiers_cannot_escape_their_namespace(self):
        cache = self.service().cache_service
        for case in ("../../other/cases/100", "/tmp/100", "..\\other\\100"):
            with self.subTest(case=case):
                with self.assertRaisesRegex(ValueError, "case identifier"):
                    cache.get_case_record(case)
                with self.assertRaisesRegex(ValueError, "case identifier"):
                    cache.invalidate_case(case)

    def test_injected_cache_cannot_bypass_identity_binding(self):
        from rh_support_lib.services.cases import CaseService

        a, b = self.service(), self.service("credential-B")
        with self.assertRaisesRegex(ValueError, "cache.*context"):
            CaseService(
                b.api_client, graphql_url=b.graphql_url, cache_service=a.cache_service
            )


class TestTuiDraftSafety(unittest.TestCase):
    def setUp(self):

        from rh_support_lib.tui.app import SupportApp

        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        for patcher in (
            mock.patch.dict(
                os.environ,
                {
                    "HOME": self.home.name,
                    "XDG_CONFIG_HOME": os.path.join(self.home.name, ".config"),
                },
            ),
            mock.patch("rh_support_lib.tui.app.CaseService"),
            mock.patch("rh_support_lib.tui.app.AttachmentService"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = SupportApp("test-token", config={"tui": {"poll_interval": 0}})
        self.app.fetch_cases = mock.Mock()
        self.app.fetch_case_details = mock.Mock()
        self.app.selected_case_id = "A"
        from rh_support_lib.models import Case, CaseDetails

        self.app.current_details = CaseDetails(
            Case("A", "Case A", "Open", "Low"), "description"
        )

    def test_failed_post_retains_exact_draft_and_prevents_duplicate_submission(self):
        import asyncio

        from textual.widgets import Select, TextArea

        async def exercise():
            async with self.app.run_test() as pilot:
                self.app.action_add_comment()
                await pilot.pause()
                editor = self.app.query_one("#tui-comment-textarea", TextArea)
                status = self.app.query_one("#tui-comment-status-select", Select).value
                raw = "  Incident update\n\n```\n<xml/>\n```\n "
                editor.text = raw
                self.app.case_service.add_comment.side_effect = RuntimeError(
                    "Connection failed"
                )
                self.app.on_comment_post_click()
                self.app.on_comment_post_click()
                await self.app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(editor.text, raw)
                self.assertTrue(self.app.screen.has_class("commenting"))
                self.assertEqual(
                    self.app.query_one("#tui-comment-status-select", Select).value,
                    status,
                )
                self.assertFalse(self.app.query_one("#tui-comment-post-btn").disabled)
                self.app.case_service.add_comment.assert_called_once()

        asyncio.run(exercise())

    def test_save_draft_from_ui_thread_is_exact_and_unique(self):
        import asyncio
        from pathlib import Path

        from textual.widgets import TextArea

        async def exercise():
            async with self.app.run_test() as pilot:
                for raw in ("  first draft\n", "second draft\n\n"):
                    self.app.action_add_comment()
                    await pilot.pause()
                    self.app.query_one("#tui-comment-textarea", TextArea).text = raw
                    self.app.on_comment_save_click()
                    await self.app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertFalse(self.app.screen.has_class("commenting"))
                files = list(
                    Path(self.home.name).glob(
                        ".config/rh-support-cli/drafts/comment_draft_A_*.txt"
                    )
                )
                self.assertEqual(len(files), 2)
                self.assertEqual(
                    {path.read_text() for path in files},
                    {"  first draft\n", "second draft\n\n"},
                )

        asyncio.run(exercise())

    def test_late_submission_failure_does_not_restore_discarded_draft(self):
        import asyncio

        started, release = threading.Event(), threading.Event()

        def fail(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release submission")
            raise RuntimeError("Late failure")

        self.app.case_service.add_comment.side_effect = fail

        async def exercise():
            async with self.app.run_test() as pilot:
                self.app.action_add_comment()
                await pilot.pause()
                self.app.query_one("#tui-comment-textarea").text = "draft A"
                self.app.on_comment_post_click()
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 5))
                    self.app.prompt_unsaved_changes_on_navigation("draft A", "B")
                    await pilot.pause()
                    await pilot.click("#discard-btn")
                    from rh_support_lib.models import Case, CaseDetails

                    self.app.current_details = CaseDetails(
                        Case("B", "Case B", "Open", "Low"), "description"
                    )
                    self.app.action_add_comment()
                    await pilot.pause()
                    self.app.query_one("#tui-comment-textarea").text = "new draft B"
                finally:
                    release.set()
                await self.app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(
                    self.app.query_one("#tui-comment-textarea").text, "new draft B"
                )
                self.assertTrue(self.app.screen.has_class("commenting"))
                self.assertFalse(self.app.query_one("#tui-comment-post-btn").disabled)
                self.assertFalse(self.app._pending_comments)

        asyncio.run(exercise())

    def test_successful_post_is_not_restored_when_refresh_fails(self):
        import asyncio

        async def exercise():
            async with self.app.run_test() as pilot:
                self.app.action_add_comment()
                await pilot.pause()
                self.app.query_one("#tui-comment-textarea").text = "posted"
                self.app.fetch_cases.side_effect = RuntimeError("Refresh failed")
                self.app.on_comment_post_click()
                await self.app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(self.app.query_one("#tui-comment-textarea").text, "")
                self.assertFalse(self.app.screen.has_class("commenting"))
                self.app.case_service.add_comment.assert_called_once()

        asyncio.run(exercise())

    def test_successful_post_keeps_edits_made_during_submission(self):
        import asyncio

        started, release = threading.Event(), threading.Event()

        def post(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release submission")

        self.app.case_service.add_comment.side_effect = post

        async def exercise():
            async with self.app.run_test() as pilot:
                self.app.action_add_comment()
                await pilot.pause()
                editor = self.app.query_one("#tui-comment-textarea")
                editor.text = "submitted"
                self.app.on_comment_post_click()
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 5))
                    editor.text = "newer edits"
                finally:
                    release.set()
                await self.app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(editor.text, "newer edits")
                self.assertTrue(self.app.screen.has_class("commenting"))
                self.assertFalse(self.app.query_one("#tui-comment-post-btn").disabled)

        asyncio.run(exercise())

    def test_quit_saves_before_exiting_and_q_remains_editor_input(self):
        import asyncio
        from pathlib import Path

        async def exercise():
            async with self.app.run_test() as pilot:
                self.app.action_add_comment()
                await pilot.pause()
                await pilot.press("q")
                self.assertEqual(self.app.query_one("#tui-comment-textarea").text, "q")
                with mock.patch.object(self.app, "exit") as exit_app:
                    await self.app.run_action("quit")
                    await pilot.pause()
                    exit_app.assert_not_called()
                    await pilot.click("#save-btn")
                    await self.app.workers.wait_for_complete()
                    await pilot.pause()
                    exit_app.assert_called_once()
                    files = list(
                        Path(self.home.name).glob(".config/rh-support-cli/drafts/*.txt")
                    )
                    self.assertEqual([path.read_text() for path in files], ["q"])

        asyncio.run(exercise())

    def test_quit_uses_existing_save_discard_cancel_modal(self):
        import asyncio

        from rh_support_lib.tui.app import UnsavedChangesModal

        async def exercise():
            async with self.app.run_test() as pilot:
                self.app.action_add_comment()
                await pilot.pause()
                self.app.query_one("#tui-comment-textarea").text = "keep me"
                with mock.patch.object(self.app, "exit") as exit_app:
                    await self.app.run_action("quit")
                    await pilot.pause()
                    self.assertIsInstance(self.app.screen, UnsavedChangesModal)
                    await pilot.click("#cancel-btn")
                    self.assertEqual(
                        self.app.query_one("#tui-comment-textarea").text, "keep me"
                    )
                    exit_app.assert_not_called()
                    await self.app.run_action("quit")
                    await pilot.pause()
                    with mock.patch(
                        "pathlib.Path.mkdir", side_effect=OSError("No space")
                    ):
                        await pilot.click("#save-btn")
                        await self.app.workers.wait_for_complete()
                        await pilot.pause()
                    exit_app.assert_not_called()
                    self.assertEqual(
                        self.app.query_one("#tui-comment-textarea").text, "keep me"
                    )
                    await self.app.run_action("quit")
                    await pilot.pause()
                    await pilot.click("#discard-btn")
                    exit_app.assert_called_once()

        asyncio.run(exercise())


class TestTuiOperationSafety(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import contextlib

        from rh_support_lib.tui.app import SupportApp

        for target in ("CaseService", "AttachmentService"):
            patcher = mock.patch(f"rh_support_lib.tui.app.{target}")
            patcher.start()
            self.addCleanup(patcher.stop)
        screen = mock.patch.object(SupportApp, "screen", new_callable=mock.PropertyMock)
        screen.start().return_value.has_class.return_value = False
        self.addCleanup(screen.stop)
        self.app = SupportApp("test-token", config={"tui": {"poll_interval": 0}})
        self.app.selected_case_id = "A"
        self.app.track_job = lambda description: contextlib.nullcontext()
        from rh_support_lib.models import Case, CaseDetails

        self.app.current_details = CaseDetails(
            Case("A", "Case A", "Open", "Low"), "description"
        )
        self.app._safe_call = lambda fn, *args, **kwargs: fn(*args, **kwargs)
        self.app.call_from_thread = self.app._safe_call
        self.app.show_error = mock.Mock()
        self.app.render_case_details = mock.Mock()
        self.app.fetch_cases = mock.Mock()
        self.app.run_worker = mock.Mock()

    def test_queued_comment_keeps_original_case(self):
        from types import SimpleNamespace

        self.app.query_one = mock.Mock(
            side_effect=lambda selector, *args: {
                "#tui-comment-textarea": SimpleNamespace(text="Draft for A"),
                "#tui-comment-status-select": SimpleNamespace(value="In Progress"),
                "#tui-comment-post-btn": SimpleNamespace(disabled=False),
            }[selector]
        )
        self.app.fetch_case_details = mock.Mock()
        self.app.on_comment_post_click()
        self.app.selected_case_id = "B"
        self.app.run_worker.call_args.args[0]()
        self.app.case_service.add_comment.assert_called_once_with(
            "A", "Draft for A", is_public=True, status="In Progress"
        )

    async def test_modal_operations_keep_original_case(self):
        for action, result in (
            ("action_add_attachment", "diagnostics.tar"),
            ("action_apply_template", "incident"),
            ("action_download_attachment", ("att_1", "/tmp/download")),
        ):
            with self.subTest(action=action):
                self.app.selected_case_id = "A"
                self.app.current_attachments = [mock.Mock()]
                self.app.push_screen = mock.Mock()
                self.app.query_one = mock.Mock()
                self.app.fetch_case_details = mock.Mock()
                with (
                    mock.patch("os.path.isfile", return_value=True),
                    mock.patch(
                        "rh_support_lib.commands.apply_template.cmd_apply"
                    ) as apply,
                ):
                    getattr(self.app, action)()
                    callback = self.app.push_screen.call_args.args[1]
                    callback(result)
                    self.app.selected_case_id = "B"
                    self.app.run_worker.call_args.args[0]()
                if action == "action_add_attachment":
                    self.assertEqual(
                        self.app.attachment_service.upload_attachment.call_args.args[0],
                        "A",
                    )
                elif action == "action_apply_template":
                    self.assertEqual(apply.call_args.args[0].case, "A")
                else:
                    self.assertEqual(
                        self.app.attachment_service.download_attachment.call_args.args[
                            0
                        ],
                        "A",
                    )

    def test_late_detail_results_and_errors_do_not_replace_new_selection(self):
        from concurrent.futures import ThreadPoolExecutor

        from rh_support_lib.models import Case, CaseDetails

        for outcome in ("success", "missing", "error"):
            with self.subTest(outcome=outcome):
                started, release = threading.Event(), threading.Event()
                details_a = CaseDetails(Case("A", "A", "Open", "Low"), "A")
                details_b = CaseDetails(Case("B", "B", "Open", "Low"), "B")

                def fetch(
                    case_id,
                    *,
                    outcome=outcome,
                    started=started,
                    release=release,
                    details_a=details_a,
                    details_b=details_b,
                    **kwargs,
                ):
                    if case_id == "B":
                        return details_b
                    started.set()
                    if not release.wait(5):
                        raise TimeoutError("Test did not release request A")
                    if outcome == "error":
                        raise RuntimeError("Old request failed")
                    return details_a if outcome == "success" else None

                self.app.case_service.get_case_details.side_effect = fetch
                self.app.selected_case_id = "A"
                self.app.show_error.reset_mock()
                self.app.render_case_details.reset_mock()
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending = executor.submit(self.app.fetch_case_details, "A")
                    try:
                        self.assertTrue(started.wait(5))
                        self.app.selected_case_id = "B"
                        self.app.fetch_case_details("B")
                    finally:
                        release.set()
                    pending.result(timeout=5)
                self.app.render_case_details.assert_called_once_with(
                    details_b, self.app.attachment_service.list_attachments.return_value
                )
                self.app.show_error.assert_not_called()

    def test_queued_detail_requests_capture_target_and_generation(self):
        from rh_support_lib.models import Case, CaseDetails

        self.app.case_service.get_case_details.side_effect = lambda case_id, **kwargs: (
            CaseDetails(Case(case_id, case_id, "Open", "Low"), case_id)
        )
        callbacks = []
        self.app.run_worker.side_effect = lambda fn, **kwargs: callbacks.append(fn)
        for case_id in ("A", "B", "A"):
            self.app.selected_case_id = case_id
            self.app.on_refresh_click()
        for callback in reversed(callbacks):
            callback()
        self.assertEqual(self.app.render_case_details.call_count, 1)
        self.assertEqual(
            self.app.case_service.get_case_details.call_args_list,
            [
                mock.call("A", use_cache=False),
                mock.call("B", use_cache=False),
                mock.call("A", use_cache=False),
            ],
        )

    def test_detail_guard_runs_when_ui_callback_is_applied(self):
        from rh_support_lib.models import Case, CaseDetails

        self.app.case_service.get_case_details.return_value = CaseDetails(
            Case("A", "A", "Open", "Low"), "A"
        )
        callbacks = []
        self.app._safe_call = lambda fn, *args: callbacks.append(lambda: fn(*args))
        self.app.fetch_case_details("A")
        self.app.selected_case_id = "B"
        for callback in callbacks:
            callback()
        self.app.render_case_details.assert_not_called()
