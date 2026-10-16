"""Sandboxed, offline regressions for individual doctor checks."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rh_support_lib.commands import doctor


class DoctorCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(self.home / "config"),
                "XDG_CACHE_HOME": str(self.home / "cache"),
                "XDG_STATE_HOME": str(self.home / "state"),
            },
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_cache_probe_preserves_existing_file_and_symlink_target(self):
        cache = self.home / "cache" / "rh-support-cli"
        cache.mkdir(parents=True)
        probe = cache / ".perm_test"
        target = self.home / "precious"
        target.write_text("keep me")
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                if symlink:
                    probe.symlink_to(target)
                else:
                    probe.write_text("existing probe")
                self.assertEqual(doctor.check_disk_cache()["status"], "PASS")
                self.assertTrue(probe.exists())
                self.assertEqual(target.read_text(), "keep me")
                self.assertEqual(
                    probe.read_text(), "keep me" if symlink else "existing probe"
                )
                self.assertEqual(list(cache.iterdir()), [probe])
                probe.unlink()

    def test_auth_selected_source_never_falls_back(self):
        default = self.home / ".config" / "rh-support-cli" / "token"
        default.parent.mkdir(parents=True)
        default.write_text("default-secret-token")
        os.environ["REDHAT_SUPPORT_OFFLINE_TOKEN"] = "environment-secret-token"
        for selected in (str(self.home / "missing"), str(self.home), ""):
            with self.subTest(selected=selected):
                self.assertEqual(doctor.check_auth(selected)["status"], "FAIL")
        os.environ["REDHAT_SUPPORT_OFFLINE_TOKEN"] = ""
        self.assertEqual(doctor.check_auth()["status"], "FAIL")
        os.environ["REDHAT_SUPPORT_OFFLINE_TOKEN"] = "   "
        self.assertEqual(doctor.check_auth()["status"], "FAIL")

    def test_auth_file_permissions_use_selected_path_and_mask_secrets(self):
        token = self.home / "token (selected)"
        token.write_text("highly-sensitive-token")
        token.chmod(0o644)
        result = doctor.check_auth(str(token))
        self.assertEqual(result["status"], "WARN")
        self.assertIn("0o644", result["message"])
        self.assertNotIn("highly-sensitive-token", result["message"])
        token.chmod(0o600)
        self.assertEqual(doctor.check_auth(str(token))["status"], "PASS")

    def test_auth_unreadable_file_fails_without_prompt_or_secret(self):
        os.environ["REDHAT_SUPPORT_OFFLINE_TOKEN"] = "fallback-secret"
        with (
            patch("builtins.open", side_effect=PermissionError("sensitive-error")),
            patch("getpass.getpass", side_effect=AssertionError("must not prompt")),
        ):
            result = doctor.check_auth("selected-token")
        self.assertEqual(result["status"], "FAIL")
        self.assertNotIn("sensitive-error", result["message"])
