"""Offline regressions for effective configuration propagation."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml
from textual.app import App
from textual.widgets import Select

from rh_support_lib.api import RedHatAPIClient
from rh_support_lib.commands.actions import cmd_close, cmd_comment
from rh_support_lib.commands.create_case import cmd_create
from rh_support_lib.commands.watchers import cmd_follow, cmd_unfollow
from rh_support_lib.main import main
from rh_support_lib.services.cache import CacheService
from rh_support_lib.services.cases import CaseService
from rh_support_lib.services.search import SearchService
from rh_support_lib.tui.app import SupportApp, TemplateModal


class ConfigurationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(
            os.environ,
            {
                "HOME": temp.name,
                "XDG_CONFIG_HOME": temp.name,
                "XDG_CACHE_HOME": temp.name,
                "XDG_STATE_HOME": temp.name,
                "RH_SUPPORT_STATE": str(self.root / "state.yaml"),
                "RH_SUPPORT_CONFIG": "",
                "RH_SUPPORT_TEMPLATES": str(self.root / "wrong-templates"),
                "RH_NO_CACHE": "0",
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.config = {
            "cache": {"list_ttl": 137, "case_ttl": 251, "catalog_ttl": 389},
            "templates_dir": str(self.root / "custom-templates"),
            "default_create_template": "incident",
        }
        Path(self.config["templates_dir"]).mkdir()
        Path(self.config["templates_dir"], "incident.yaml").write_text(
            "comment: Custom template body\n", encoding="utf-8"
        )
        self.config_path = self.root / "custom.yaml"
        self.config_path.write_text(yaml.safe_dump(self.config), encoding="utf-8")
        self.client = Mock(spec=RedHatAPIClient)
        self.client.get_cache_scope.return_value = "offline-identity/backend"
        output = patch("sys.stdout", new_callable=io.StringIO)
        output.start()
        self.addCleanup(output.stop)

    def assert_cache_config(self, service):
        cache = service.cache_service
        self.assertIsInstance(cache, CacheService)
        self.assertEqual(
            (cache.list_ttl, cache.case_ttl, cache.catalog_ttl), (137, 251, 389)
        )
        self.assertEqual(cache.scope, "offline-identity/backend")
        self.assertTrue(cache.enabled)

    def test_library_explicit_config_and_injected_services(self):
        service = CaseService(self.client, config=self.config)
        self.assert_cache_config(service)
        self.client.get_cache_scope.assert_called_with(service.graphql_url)
        search = SearchService(self.client, config=self.config)
        self.assert_cache_config(search.case_service)
        self.assertIs(SearchService(self.client, service).case_service, service)
        self.assertIs(
            CaseService(
                self.client, cache_service=service.cache_service, config={}
            ).cache_service,
            service.cache_service,
        )
        with patch.dict(os.environ, {"RH_SUPPORT_CONFIG": str(self.config_path)}):
            default = CaseService(self.client)
        self.assertEqual(default.cache_service.list_ttl, 60)
        self.assertEqual(default.cache_service.catalog_ttl, 86400)

    def test_cli_config_sources_reach_real_cache_instances(self):
        class Constructed(Exception):
            pass

        original_init = CaseService.__init__

        def inspect_service(service, *args, **kwargs):
            original_init(service, *args, **kwargs)
            self.assert_cache_config(service)
            raise Constructed

        commands = [
            ["list"],
            ["show", "-c", "123"],
            ["create", "--no-default-template"],
            ["apply", "-c", "123", "-t", "incident"],
            ["comment", "-c", "123", "-t", "incident"],
            ["close", "-c", "123", "-t", "incident"],
            ["follow", "123"],
            ["unfollow", "123"],
            ["following"],
            ["sync"],
            ["updates"],
            ["search", "diagnostic"],
        ]
        for source in ("argument", "environment"):
            for command in commands:
                with self.subTest(source=source, command=command):
                    argv = ["rh-support-cli"]
                    if source == "argument":
                        argv.extend(["--config-file", str(self.config_path)])
                    argv.extend(command)
                    with (
                        patch("sys.argv", argv),
                        patch.dict(
                            os.environ,
                            {
                                "RH_SUPPORT_CONFIG": str(self.config_path)
                                if source == "environment"
                                else ""
                            },
                        ),
                        patch("rh_support_lib.main.argcomplete", None),
                        patch(
                            "rh_support_lib.api.RedHatAPIClient",
                            return_value=self.client,
                        ),
                        patch.object(CaseService, "__init__", inspect_service),
                        self.assertRaises(Constructed),
                    ):
                        main()
        self.client.execute_graphql.assert_not_called()
        self.client.get.assert_not_called()

    def test_comment_and_close_render_custom_templates(self):
        for command, status in (
            (cmd_comment, "In Progress"),
            (cmd_close, "Closed"),
        ):
            with self.subTest(command=command.__name__):
                args = SimpleNamespace(
                    case="123",
                    template=["incident"],
                    template_var=None,
                    file=None,
                    edit=False,
                    status=None,
                    format="plain",
                )
                with patch.object(CaseService, "add_comment") as post:
                    command(args, self.client, self.config)
                post.assert_called_once_with(
                    "123",
                    "Custom template body",
                    is_public=True,
                    status=status,
                    body_format="plain",
                )

    def test_completion_respects_explicit_config_file(self):
        from rh_support_lib.completers import bookmark_completer, template_completer

        config = {**self.config, "bookmarks": {"team": {"severity": "Low"}}}
        self.config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        args = SimpleNamespace(config_file=str(self.config_path))
        self.assertEqual(bookmark_completer("te", parsed_args=args), ["team"])
        self.assertEqual(template_completer("inc", parsed_args=args), ["incident"])

    def test_create_keeps_confirmation_and_configures_single_case_service(self):
        args = SimpleNamespace(
            no_default_template=True,
            template=None,
            product="Red Hat Enterprise Linux",
            version="9.0",
            severity="Normal",
            type="Standard",
            summary="Offline case",
            description_file=str(self.root / "description"),
            attachment=None,
            format="plain",
        )
        Path(args.description_file).write_text("Description", encoding="utf-8")
        services = []

        def construct(*args, **kwargs):
            service = CaseService(*args, **kwargs)
            services.append(service)
            return service

        with (
            patch(
                "rh_support_lib.commands.create_case.CaseService", side_effect=construct
            ),
            patch(
                "rh_support_lib.commands.create_case.prompt_text", return_value="y"
            ) as prompt,
            patch.object(CaseService, "create_case", return_value="123") as create,
        ):
            self.assertEqual(cmd_create(args, self.client, self.config), "123")
        prompt.assert_called_once_with("Submit this case? (y/n/edit)", default="y")
        create.assert_called_once()
        self.assertEqual(len(services), 1)
        for service in services:
            self.assert_cache_config(service)

    def test_existing_two_argument_helpers_keep_working(self):
        args = SimpleNamespace(
            case="123",
            cases=[],
            user="offline",
            template=None,
            file=str(self.root / "body"),
            edit=False,
            status=None,
            format="plain",
        )
        Path(args.file).write_text("Existing body", encoding="utf-8")
        with (
            patch.object(CaseService, "add_comment") as post,
            patch.object(CaseService, "follow_case") as follow,
            patch.object(CaseService, "unfollow_case") as unfollow,
        ):
            cmd_comment(args, self.client)
            cmd_close(args, self.client)
            cmd_follow(args, self.client)
            cmd_unfollow(args, self.client)
        self.assertEqual(post.call_count, 2)
        follow.assert_called_once_with("123", username="offline")
        unfollow.assert_called_once_with("123", username="offline")

    async def test_tui_constructor_and_action_propagate_config(self):
        with patch.object(CaseService, "get_current_user", return_value="offline"):
            app = SupportApp(self.client, config=self.config)
        self.assert_cache_config(app.case_service)
        app.selected_case_id = "123"
        from rh_support_lib.models import Case, CaseDetails

        app.current_details = CaseDetails(
            Case("123", "Case", "Open", "Low"), "description"
        )
        with patch.object(app, "push_screen") as push:
            app.action_apply_template()
        modal = push.call_args.args[0]
        self.assertIsInstance(modal, TemplateModal)
        self.assertIs(modal.config, self.config)
        self.assertEqual(modal.default_template, "incident")


class TemplateSelectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_selector_reads_configured_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "custom.yaml").write_text("comment: Custom\n", encoding="utf-8")
            config = {"templates_dir": temp}
            app = App()
            async with app.run_test() as pilot:
                app.push_screen(TemplateModal("123", "custom", config=config))
                await pilot.pause()
                self.assertEqual(
                    app.screen.query_one("#template-select", Select).value, "custom"
                )
