import io
import os
import unittest
from unittest import mock

from rh_support_lib.main import main


class ParserTests(unittest.TestCase):
    def dispatch(self, argv, handler, config=None):
        with (
            mock.patch("sys.argv", ["rh-support-cli", *argv]),
            mock.patch.dict(os.environ, {"RH_NO_CACHE": "0"}),
            mock.patch("rh_support_lib.main.argcomplete", None),
            mock.patch("rh_support_lib.main.load_config", return_value=config or {}),
            mock.patch("rh_support_lib.api.RedHatAPIClient") as client,
            mock.patch(handler) as command,
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            try:
                main()
            except SystemExit as exc:
                self.assertEqual(exc.code, 0)
            command.assert_called_once()
            args = command.call_args.args[0]
            if client.called:
                self.assertIs(command.call_args.args[1], client.return_value)
            return args

    def test_output_flags_before_and_after_commands(self):
        commands = [
            (["list"], "cmd_list"),
            (["show", "-c", "123"], "cmd_show"),
            (["search", "boot"], "cmd_search"),
            (["following"], "cmd_following"),
            (["updates"], "cmd_updates"),
            (["sync"], "cmd_sync"),
            (["list-attachments", "123"], "cmd_attachment"),
            (["list-templates"], "cmd_list_templates"),
            (["list-template"], "cmd_list_templates"),
            (["follow-account", "123"], "cmd_follow_account"),
            (["unfollow-account", "123"], "cmd_unfollow_account"),
        ]
        for command, handler in commands:
            for flags, fmt in [
                (["--format", "yaml"], "yaml"),
                (["--format", "json"], "json"),
                (["--format", "text"], "text"),
                (["--json"], "json"),
                (["-j"], "json"),
                ([], "text"),
            ]:
                for argv in (flags + command, command + flags):
                    with self.subTest(argv=argv):
                        args = self.dispatch(argv, "rh_support_lib.main." + handler)
                        self.assertEqual(args.format, fmt)
                        self.assertEqual(args.json, fmt == "json")

    def test_attachment_aliases_and_nested_flag_positions(self):
        actions = [
            ["list"],
            ["upload", "-f", "report"],
            ["add", "-f", "report"],
            ["download", "-a", "uuid"],
            ["get", "-a", "uuid"],
            ["delete", "-a", "uuid", "-y"],
            ["rm", "-a", "uuid", "-y"],
            ["status"],
        ]
        for alias in ("attachment", "attachments"):
            for action in actions:
                for flags, fmt in [
                    (["--format", "yaml"], "yaml"),
                    (["--json"], "json"),
                    (["-j"], "json"),
                    (["--no-cache", "--simple-output"], "text"),
                ]:
                    for argv in (
                        flags + [alias, "-c", "123"] + action,
                        [alias, "-c", "123"] + flags + action,
                        [alias, "-c", "123"] + action + flags,
                    ):
                        with self.subTest(argv=argv):
                            args = self.dispatch(
                                argv, "rh_support_lib.main.cmd_attachment"
                            )
                            self.assertEqual(args.case, "123")
                            self.assertEqual(args.attachment_action, action[0])
                            self.assertEqual(args.format, fmt)
                            self.assertEqual(args.json, fmt == "json")
                            self.assertEqual(args.no_cache, "--no-cache" in flags)
                            self.assertEqual(
                                args.simple_output, "--simple-output" in flags
                            )

    def test_global_booleans_before_and_after_commands(self):
        for command, handler in [
            (["list"], "cmd_list"),
            (["show", "-c", "123"], "cmd_show"),
            (["search", "boot"], "cmd_search"),
            (["sync"], "cmd_sync"),
            (["updates"], "cmd_updates"),
            (["list-attachments", "123"], "cmd_attachment"),
            (["create"], "cmd_create"),
            (["comment", "-c", "123"], "cmd_comment"),
            (["close", "-c", "123"], "cmd_close"),
            (["follow-account", "123"], "cmd_follow_account"),
            (["unfollow-account", "123"], "cmd_unfollow_account"),
        ]:
            for flags in ([], ["--no-cache"], ["--simple-output"]):
                for argv in (flags + command, command + flags):
                    with self.subTest(argv=argv):
                        args = self.dispatch(argv, "rh_support_lib.main." + handler)
                        self.assertEqual(args.no_cache, "--no-cache" in flags)
                        self.assertEqual(args.simple_output, "--simple-output" in flags)

    def test_content_format_is_independent_of_output_flags(self):
        for command in ("create", "comment", "close"):
            required = [] if command == "create" else ["-c", "123"]
            for fmt in ("markdown", "plain", "html", "raw"):
                for root in ([], ["--format", "yaml"], ["--json"], ["-j"]):
                    for trailing in ([], ["--json"], ["-j"]):
                        argv = (
                            root + [command] + required + ["--format", fmt] + trailing
                        )
                        with self.subTest(argv=argv):
                            args = self.dispatch(
                                argv, "rh_support_lib.main.cmd_" + command
                            )
                            self.assertEqual(args.format, fmt)
                            self.assertEqual(
                                args.json,
                                bool(trailing) or "--json" in root or "-j" in root,
                            )
            args = self.dispatch(
                ["--format", "yaml", command] + required,
                "rh_support_lib.main.cmd_" + command,
            )
            self.assertEqual(args.format, "markdown")

    def test_option_values_do_not_become_flags(self):
        for value in ("--json", "-j", "--no-cache", "--simple-output"):
            for argv, handler, field, expected in [
                (["search", "--", value], "cmd_search", "query", value),
                (["list", "--owner=" + value], "cmd_list", "owner", value),
                (["create", "--summary=" + value], "cmd_create", "summary", value),
                (
                    [
                        "apply",
                        "-c",
                        "123",
                        "-t",
                        "example",
                        "--template-var=" + value,
                    ],
                    "cmd_apply",
                    "template_var",
                    [value],
                ),
                (
                    ["create", "--template-var", "body=" + value],
                    "cmd_create",
                    "template_var",
                    ["body=" + value],
                ),
            ]:
                with self.subTest(argv=argv):
                    args = self.dispatch(argv, "rh_support_lib.main." + handler)
                    self.assertEqual(getattr(args, field), expected)
                    self.assertEqual(
                        args.format, "markdown" if args.command == "create" else "text"
                    )
                    self.assertFalse(args.json)
                    self.assertFalse(args.no_cache)
                    self.assertFalse(args.simple_output)

    def test_explicit_json_still_wins_over_output_format(self):
        for argv in (
            ["-j", "list", "--format", "yaml"],
            ["--format", "yaml", "list", "--json"],
            ["--json", "attachments", "--format", "text", "list", "123"],
        ):
            with self.subTest(argv=argv):
                handler = "cmd_attachment" if "attachments" in argv else "cmd_list"
                args = self.dispatch(argv, "rh_support_lib.main." + handler)
                self.assertEqual(args.format, "json")
                self.assertTrue(args.json)

    def test_last_explicit_output_format_wins(self):
        for argv, fmt in [
            (["--format", "yaml", "list", "--format", "text"], "text"),
            (["--format", "json", "list", "--format", "yaml"], "yaml"),
            (
                [
                    "--format",
                    "json",
                    "attachment",
                    "--format",
                    "text",
                    "list",
                    "123",
                    "--format",
                    "yaml",
                ],
                "yaml",
            ),
        ]:
            with self.subTest(argv=argv):
                handler = "cmd_attachment" if "attachment" in argv else "cmd_list"
                args = self.dispatch(argv, "rh_support_lib.main." + handler)
                self.assertEqual(args.format, fmt)
                self.assertEqual(args.json, fmt == "json")

    def test_cache_configuration_still_applies(self):
        args = self.dispatch(
            ["list"],
            "rh_support_lib.main.cmd_list",
            config={"cache": {"enabled": False}},
        )
        self.assertTrue(args.no_cache)

    def test_systemd_nested_positions_are_not_expanded(self):
        for alias in ("systemd", "install-systemd", "install-timer"):
            for flags in (["--json"], ["--no-cache", "--simple-output"]):
                command = [alias, "status"] if alias == "systemd" else [alias]
                handler = "cmd_systemd" if alias == "systemd" else "cmd_install_systemd"
                for argv in (flags + command, [alias] + flags + command[1:]):
                    with self.subTest(argv=argv):
                        args = self.dispatch(
                            argv, "rh_support_lib.commands.systemd." + handler
                        )
                        self.assertEqual(args.json, "--json" in flags)
                        self.assertEqual(args.no_cache, "--no-cache" in flags)
                        self.assertEqual(args.simple_output, "--simple-output" in flags)
        with (
            mock.patch("sys.argv", ["rh-support-cli", "systemd", "status", "--json"]),
            mock.patch("rh_support_lib.main.argcomplete", None),
            mock.patch("rh_support_lib.main.load_config") as config,
            mock.patch("sys.stderr", new_callable=io.StringIO),
        ):
            with self.assertRaises(SystemExit) as exc:
                main()
            self.assertEqual(exc.exception.code, 2)
            config.assert_not_called()
