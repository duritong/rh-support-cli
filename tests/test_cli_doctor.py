"""Offline regression tests for rh-support-cli doctor diagnostic command."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


class DoctorCliTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.temp_home = tempfile.mkdtemp(prefix="rh-doctor-test-")
        self.addCleanup(lambda: shutil.rmtree(self.temp_home, ignore_errors=True))
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("RH_", "REDHAT_", "XDG_"))
        }
        self.env["HOME"] = self.temp_home
        self.env["XDG_CONFIG_HOME"] = os.path.join(self.temp_home, ".config")
        self.env["XDG_CACHE_HOME"] = os.path.join(self.temp_home, ".cache")
        self.env["XDG_STATE_HOME"] = os.path.join(self.temp_home, ".local", "state")
        self.env["REDHAT_SUPPORT_OFFLINE_TOKEN"] = "mock_offline_token_value_12345"
        bin_dir = Path(self.temp_home) / "bin"
        bin_dir.mkdir()
        systemctl = bin_dir / "systemctl"
        systemctl.write_text("#!/bin/sh\nprintf 'inactive\\n'\nexit 3\n")
        systemctl.chmod(0o700)
        self.env["PATH"] = str(bin_dir)
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.cli_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "rh-support-cli.py",
        )

    def _run_doctor(self, *extra_args: str, global_args=()) -> tuple[int, str, str]:
        import subprocess

        cmd = [
            sys.executable,
            self.cli_path,
            *global_args,
            "doctor",
            *extra_args,
        ]
        res = subprocess.run(
            cmd, env=self.env, capture_output=True, text=True, check=False
        )
        return res.returncode, res.stdout, res.stderr

    def test_doctor_passes_with_token_and_missing_systemd_is_warning(self):
        """Verify doctor succeeds (exit 0) and treats missing systemd timer as a warning."""
        rc, stdout, stderr = self._run_doctor("--simple-output")
        self.assertEqual(
            rc,
            0,
            f"Doctor should exit 0 when only warnings are present:\n{stderr}\n{stdout}",
        )
        self.assertIn("RED HAT SUPPORT CLI — DOCTOR DIAGNOSTIC REPORT", stdout)
        self.assertIn("[PASS] Authentication", stdout)
        self.assertIn("[WARN] Systemd Timer", stdout)
        self.assertNotIn("[FAIL]", stdout)

    def test_doctor_fails_cleanly_when_token_is_missing(self):
        """Verify doctor reports FAIL on authentication when token is absent and exits 1."""
        self.env.pop("REDHAT_SUPPORT_OFFLINE_TOKEN", None)
        rc, stdout, _stderr = self._run_doctor("--simple-output")
        self.assertEqual(rc, 1)
        self.assertIn("[FAIL] Authentication", stdout)
        self.assertIn("No offline token found", stdout)

    def test_doctor_json_output_schema_and_counts(self):
        """Verify doctor --json outputs valid parseable JSON matching the diagnostic schema."""
        rc, stdout, _stderr = self._run_doctor("--json")
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        self.assertIn("summary", data)
        self.assertIn("checks", data)
        summary = data["summary"]
        self.assertGreaterEqual(summary["passed"], 4)
        self.assertGreaterEqual(summary["warnings"], 1)
        self.assertEqual(summary["failures"], 0)

        # Ensure no token secrets are leaked into JSON messages
        for check in data["checks"]:
            self.assertNotIn("mock_offline_token_value_12345", check["message"])

    def test_doctor_rich_table_presentation(self):
        """Verify doctor default rich output executes and reports summary."""
        rc, stdout, _stderr = self._run_doctor()
        self.assertEqual(rc, 0)
        self.assertIn("Red Hat Support CLI — Diagnostic Report", stdout)
        self.assertIn("Summary:", stdout)

    def test_broken_configuration_still_produces_full_json_report(self):
        config = Path(self.temp_home) / "broken.yaml"
        for contents in ("bookmarks: [", "cache: []", "default_bookmark: missing"):
            config.write_text(contents)
            for source in ("cli", "environment", "default"):
                with self.subTest(contents=contents, source=source):
                    self.env.pop("RH_SUPPORT_CONFIG", None)
                    global_args = ()
                    default = (
                        Path(self.temp_home) / ".config/rh-support-cli/config.yaml"
                    )
                    if default.exists():
                        default.unlink()
                    if source == "cli":
                        global_args = ("--config-file", str(config))
                    elif source == "environment":
                        self.env["RH_SUPPORT_CONFIG"] = str(config)
                    else:
                        default.parent.mkdir(parents=True, exist_ok=True)
                        default.write_text(contents)
                    rc, stdout, stderr = self._run_doctor(
                        "--json", global_args=global_args
                    )
                    self.assertEqual(rc, 1)
                    data = json.loads(stdout)
                    self.assertEqual(len(data["checks"]), 7)
                    configuration = next(
                        c for c in data["checks"] if c["category"] == "Configuration"
                    )
                    self.assertEqual(configuration["status"], "FAIL")
                    self.assertNotIn("Authenticating", stdout + stderr)

    def test_selected_missing_config_fails_but_absent_default_is_optional(self):
        for global_args, env_path in (
            (("--config-file", str(Path(self.temp_home) / "missing")), None),
            ((), str(Path(self.temp_home) / "missing")),
            ((), None),
        ):
            with self.subTest(global_args=global_args, env_path=env_path):
                self.env.pop("RH_SUPPORT_CONFIG", None)
                if env_path:
                    self.env["RH_SUPPORT_CONFIG"] = env_path
                rc, stdout, _ = self._run_doctor("--json", global_args=global_args)
                data = json.loads(stdout)
                self.assertEqual(rc, 1 if global_args or env_path else 0)
                self.assertEqual(len(data["checks"]), 7)
