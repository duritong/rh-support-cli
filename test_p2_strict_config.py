"""Strict library configuration regressions, isolated from user files and APIs."""

import contextlib
import copy
import io
import os
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from rh_support_lib.config import ConfigError, load_config, resolve_bookmarks


class StrictConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        env = patch.dict(os.environ, {"HOME": temp.name, "RH_SUPPORT_CONFIG": ""})
        env.start()
        self.addCleanup(env.stop)
        self.path = self.home / "config.yaml"
        self.default = self.home / ".config/rh-support-cli/config.yaml"

    def write(self, value):
        self.path.write_text(yaml.safe_dump(value), encoding="utf-8")
        return self.path

    def test_optional_absent_default_and_empty_mapping(self):
        self.assertEqual(load_config(), {})
        self.assertEqual(load_config(self.write({})), {})
        self.default.parent.mkdir(parents=True)
        self.default.write_text("{}", encoding="utf-8")
        self.assertEqual(load_config(), {})

    def test_source_precedence_and_tilde_expansion(self):
        self.write({"layout": "horizontal"})
        self.default.parent.mkdir(parents=True)
        self.default.write_text("layout: vertical", encoding="utf-8")
        with patch.dict(os.environ, {"RH_SUPPORT_CONFIG": "~/config.yaml"}):
            self.assertEqual(load_config()["layout"], "horizontal")
            self.assertEqual(load_config(self.default)["layout"], "vertical")
        self.assertEqual(load_config("~/config.yaml")["layout"], "horizontal")
        self.assertEqual(load_config()["layout"], "vertical")

    def test_missing_explicit_sources_fail_without_output(self):
        for source in ("argument", "environment"):
            with self.subTest(source=source):
                env = str(self.path) if source == "environment" else ""
                arg = self.path if source == "argument" else None
                with (
                    patch.dict(os.environ, {"RH_SUPPORT_CONFIG": env}),
                    patch("sys.stdout", new_callable=io.StringIO) as stdout,
                    patch("sys.stderr", new_callable=io.StringIO) as stderr,
                    self.assertRaises(ConfigError) as error,
                ):
                    load_config(arg)
                self.assertIn(str(self.path), str(error.exception))
                self.assertIsInstance(error.exception, ValueError)
                self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")
        with self.assertRaises(ConfigError):
            load_config("")

    def test_unreadable_files_directories_and_dangling_default(self):
        self.write({})
        with (
            patch("rh_support_lib.config.open", side_effect=PermissionError),
            self.assertRaisesRegex(ConfigError, "cannot be read"),
        ):
            load_config(self.path)
        with self.assertRaises(ConfigError):
            load_config(self.home)
        self.default.parent.mkdir(parents=True)
        self.default.symlink_to(self.home / "missing")
        with self.assertRaises(ConfigError):
            load_config()
        self.default.unlink()
        self.default.mkdir()
        with self.assertRaises(ConfigError):
            load_config()

    def test_invalid_existing_default_is_not_ignored(self):
        self.default.parent.mkdir(parents=True)
        for contents in ("[", "[]", "null", "cache: []", ""):
            with self.subTest(contents=contents):
                self.default.write_text(contents, encoding="utf-8")
                with self.assertRaises(ConfigError) as error:
                    load_config()
                self.assertIn(str(self.default), str(error.exception))

    def test_parse_errors_never_disclose_yaml_or_parser_context(self):
        for contents in (
            "secret: [DO_NOT_DISCLOSE",
            "secret: !!python/object:DO_NOT_DISCLOSE {}",
            "secret: *DO_NOT_DISCLOSE",
        ):
            with self.subTest(contents=contents):
                self.path.write_text(contents, encoding="utf-8")
                try:
                    load_config(self.path)
                except ConfigError as error:
                    diagnostic = "".join(
                        traceback.format_exception(
                            type(error), error, error.__traceback__
                        )
                    )
                    self.assertIn(str(self.path), str(error))
                    self.assertNotIn("DO_NOT_DISCLOSE", diagnostic)
                else:
                    self.fail("Expected ConfigError")
        self.path.write_bytes(b"\xff")
        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_known_invalid_shapes_and_types(self):
        invalid = [
            ([], "root"),
            (None, "root"),
            (False, "root"),
            ({"cache": None}, "cache"),
            ({"cache": {"enabled": "false"}}, "cache.enabled"),
            ({"cache": {"list_ttl": -1}}, "cache.list_ttl"),
            ({"cache": {"case_ttl": True}}, "cache.case_ttl"),
            ({"cache": {"catalog_ttl": 1.5}}, "cache.catalog_ttl"),
            ({"cache": {"list_ttl": "1.5"}}, "cache.list_ttl"),
            ({"cache": {"tui_poll_interval": "bad"}}, "cache.tui_poll_interval"),
            ({"tui": []}, "tui"),
            ({"tui": {"poll_interval": False}}, "tui.poll_interval"),
            ({"tui": {"poll_interval": -0.5}}, "tui.poll_interval"),
            ({"tui": {"poll_interval": float("inf")}}, "tui.poll_interval"),
            ({"tui": {"poll_interval": "nan"}}, "tui.poll_interval"),
            ({"kcs": []}, "kcs"),
            ({"kcs": {"language": []}}, "kcs.language"),
            ({"bookmarks": []}, "bookmarks"),
            ({"bookmarks": {12: {}}}, "bookmarks"),
            ({"bookmarks": {"team": None}}, "bookmarks.team"),
            ({"default_bookmark": "missing"}, "default_bookmark"),
            ({"default_bookmark": [True]}, "default_bookmark"),
            ({"default_bookmark": None}, "default_bookmark"),
        ]
        for key in (
            "templates_dir",
            "debug_file",
            "default_create_template",
            "default_kcs_language",
            "comments_order",
            "comment_order",
            "layout",
            "tui_layout",
        ):
            invalid.append(({key: []}, key))
        for config, field in invalid:
            with self.subTest(config=config):
                with self.assertRaises(ConfigError) as error:
                    load_config(self.write(config))
                self.assertIn(str(self.path), str(error.exception))
                self.assertIn(field, str(error.exception))

    def test_invalid_filter_types(self):
        invalid = {
            "account": [1],
            "accountNumber": True,
            "status": [1],
            "severity": [True],
            "following": "true",
            "owner": 1,
            "ownerSSOName": [],
            "product": {},
            "user": None,
            "language": False,
        }
        for field, value in invalid.items():
            with self.subTest(field=field):
                config = {"bookmarks": {"team": {field: value}}}
                with self.assertRaises(ConfigError) as error:
                    load_config(self.write(config))
                self.assertIn(f"bookmarks.team.{field}", str(error.exception))
        self.write({"cache": {"list_ttl": "DO_NOT_DISCLOSE"}})
        with self.assertRaises(ConfigError) as error:
            load_config(self.path)
        self.assertNotIn("DO_NOT_DISCLOSE", str(error.exception))

    def test_valid_configs_normalize_numbers_and_preserve_unknown_fields(self):
        config = {
            "cache": {
                "enabled": False,
                "list_ttl": "0",
                "case_ttl": "137",
                "catalog_ttl": 86400,
                "tui_poll_interval": "0",
                "future": [],
            },
            "tui": {"poll_interval": "0.5", "future": None},
            "kcs": {"language": "en", "future": []},
            "templates_dir": "~/templates",
            "debug_file": "~/debug.log",
            "bookmarks": {
                "empty": {},
                "team": {
                    "account": 123,
                    "accountNumber": "00123",
                    "status": "open",
                    "severity": [1, "High"],
                    "following": True,
                    "owner": "me",
                    "ownerSSOName": "me",
                    "user": "me",
                    "product": "RHEL",
                    "language": "en",
                    "future": {"anything": None},
                },
            },
            "default_bookmark": ["empty", "team"],
            "future": [None],
        }
        result = load_config(self.write(config))
        self.assertEqual(result["cache"]["list_ttl"], 0)
        self.assertEqual(result["cache"]["case_ttl"], 137)
        self.assertEqual(result["tui"]["poll_interval"], 0.5)
        self.assertEqual(result["cache"]["tui_poll_interval"], 0)
        self.assertEqual(result["templates_dir"], str(self.home / "templates"))
        self.assertEqual(result["debug_file"], str(self.home / "debug.log"))
        self.assertEqual(result["bookmarks"], config["bookmarks"])
        self.assertEqual(result["future"], [None])
        for value in (0, 0.5, "0", "1.5"):
            self.assertGreaterEqual(
                load_config(self.write({"tui": {"poll_interval": value}}))["tui"][
                    "poll_interval"
                ],
                0,
            )

    def test_resolver_precedence_merge_empty_and_no_mutation(self):
        config = {
            "bookmarks": {
                "empty": {},
                "a": {"account": 1, "status": "open"},
                "b": {"status": "closed"},
            },
            "default_bookmark": ["empty", "a"],
        }
        original = copy.deepcopy(config)
        self.assertEqual(resolve_bookmarks(config), {"account": 1, "status": "open"})
        self.assertEqual(
            resolve_bookmarks(config, "b"), {"account": 1, "status": "closed"}
        )
        self.assertEqual(
            resolve_bookmarks(config, ["a", "b"]), {"account": 1, "status": "closed"}
        )
        self.assertEqual(
            resolve_bookmarks(config, "empty"), {"account": 1, "status": "open"}
        )
        self.assertEqual(
            resolve_bookmarks(config, []), {"account": 1, "status": "open"}
        )
        self.assertEqual(resolve_bookmarks(config, no_default=True), {})
        self.assertEqual(
            resolve_bookmarks(config, "b", no_default=True), {"status": "closed"}
        )
        self.assertEqual(config, original)
        config["default_bookmark"] = "empty"
        self.assertEqual(resolve_bookmarks(config), {})

    def test_resolver_rejects_invalid_selections_without_io(self):
        config = {"bookmarks": {"good": {}, "bad": {"following": 1}}}
        with patch("rh_support_lib.config.open", side_effect=AssertionError("I/O")):
            for selected in ("missing", ["good", "missing"], [True], {}, 1, "bad"):
                with self.subTest(selected=selected), self.assertRaises(ConfigError):
                    resolve_bookmarks(config, selected)
            self.assertEqual(resolve_bookmarks(config), {})
            self.assertEqual(resolve_bookmarks(None, [True], clear=True), {})
            self.assertEqual(
                resolve_bookmarks({"default_bookmark": None}, no_default=True), {}
            )
            with self.assertRaises(ConfigError):
                resolve_bookmarks([])
            with self.assertRaises(ConfigError):
                resolve_bookmarks({"bookmarks": []}, "good")
            with self.assertRaises(ConfigError):
                resolve_bookmarks({"bookmarks": {"bad": None}}, "bad")


class StrictConfigurationCliTests(unittest.TestCase):
    def test_invalid_selected_config_or_bookmark_never_starts_api_or_mock_backend(self):
        from rh_support_lib.main import main

        for arguments, config in (
            (["--config-file", "MISSING", "--mock", "list"], None),
            (["--mock", "list", "-b", "missing", "--json"], {"bookmarks": {}}),
        ):
            with (
                self.subTest(arguments=arguments),
                tempfile.TemporaryDirectory() as home,
            ):
                arguments = [
                    os.path.join(home, "missing.yaml") if value == "MISSING" else value
                    for value in arguments
                ]
                with (
                    patch.dict(os.environ, {"HOME": home, "RH_SUPPORT_CONFIG": ""}),
                    patch("sys.argv", ["rh-support-cli", *arguments]),
                    patch("rh_support_lib.main.argcomplete", None),
                    patch("rh_support_lib.main._start_mock") as backend,
                    patch("rh_support_lib.api.RedHatAPIClient") as client,
                    contextlib.redirect_stdout(io.StringIO()) as output,
                    self.assertRaises(SystemExit),
                ):
                    if config is None:
                        main()
                    else:
                        with patch(
                            "rh_support_lib.main.load_config", return_value=config
                        ):
                            main()
                self.assertEqual(output.getvalue(), "")
                backend.assert_not_called()
                client.assert_not_called()

    def test_content_value_mock_is_not_a_backend_selection_flag(self):
        from rh_support_lib.main import main

        with (
            patch("sys.argv", ["rh-support-cli", "search", "--", "--mock"]),
            patch("rh_support_lib.main.argcomplete", None),
            patch("rh_support_lib.main.load_config", return_value={}),
            patch("rh_support_lib.main._start_mock") as backend,
            patch("rh_support_lib.api.RedHatAPIClient"),
            patch("rh_support_lib.main.cmd_search") as search,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            main()
        backend.assert_not_called()
        self.assertEqual(search.call_args.args[0].query, "--mock")


if __name__ == "__main__":
    unittest.main()
