"""Sandboxed, offline regressions for individual doctor checks."""

from __future__ import annotations

import json
import os
import sys
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
        previous_directory = os.getcwd()
        os.chdir(self.home)
        self.addCleanup(os.chdir, previous_directory)

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

    def test_main_dispatches_doctor_before_config_or_api_with_flags_and_mock(self):
        from rh_support_lib.main import _main

        with (
            patch.object(
                sys,
                "argv",
                [
                    "rh-support-cli",
                    "--mock",
                    "--json",
                    "--token-file",
                    "selected",
                    "doctor",
                    "--online",
                    "--simple-output",
                ],
            ),
            patch(
                "rh_support_lib.main.load_config",
                side_effect=AssertionError("config prerequisite"),
            ),
            patch("rh_support_lib.main._start_mock") as mock_server,
            patch(
                "rh_support_lib.api.RedHatAPIClient",
                side_effect=AssertionError("API client"),
            ),
            patch.object(doctor, "cmd_doctor", return_value=1) as command,
            self.assertRaises(SystemExit) as exit_result,
        ):
            _main()
        self.assertEqual(exit_result.exception.code, 1)
        mock_server.assert_called_once_with(None)
        args = command.call_args.args[0]
        self.assertEqual(args.token_file, "selected")
        self.assertEqual(args.format, "json")
        self.assertTrue(args.online)
        self.assertTrue(args.simple_output)

    def test_state_probe_uses_core_path_selection(self):
        from rh_support_lib.services.state import StateService

        for environment, config in (
            ({"RH_SUPPORT_STATE": "~/custom/state.yaml"}, None),
            ({"RH_SUPPORT_STATE": "relative-state.yaml"}, None),
            ({"RH_SUPPORT_CONFIG": "~/custom/config.yaml"}, None),
            ({}, "relative/config.yaml"),
            ({"XDG_STATE_HOME": "~/state-home"}, None),
        ):
            with (
                self.subTest(environment=environment, config=config),
                patch.dict(os.environ, environment),
            ):
                expected = Path(StateService._resolve_state_path(config_file=config))
                expected.parent.mkdir(parents=True, exist_ok=True)
                expected.write_text(
                    "users:\n  test:\n    tracked_cases:\n      '123': {}\n"
                )
                result = doctor.check_state_storage(config_file=config)
                self.assertEqual(result["status"], "PASS")
                self.assertIn(str(expected), result["message"])
                self.assertIn("1 tracked case(s)", result["message"])
                self.assertTrue(Path(str(expected) + ".lock").is_file())

    def test_cache_and_catalog_use_core_expansion_and_app_directory(self):
        from rh_support_lib.services.cache import CacheService

        for base in ("~/cache-tilde", "relative-cache", "~/cache-app/rh-support-cli"):
            with (
                self.subTest(base=base),
                patch.dict(os.environ, {"XDG_CACHE_HOME": base}),
            ):
                expected = Path(CacheService._resolve_cache_dir())
                result = doctor.check_disk_cache()
                self.assertEqual(result["status"], "PASS")
                self.assertIn(str(expected), result["message"])
                self.assertTrue(expected.is_dir())
                catalog = expected / "catalog" / "products.json"
                catalog.parent.mkdir()
                catalog.write_text(json.dumps({"products": ["product"] * 1000}))
                result = doctor.check_product_catalog()
                self.assertIn("1000 products", result["message"])
                self.assertIn("local cache", result["message"])
