"""Offline regression tests for rh-support-cli drafts discovery, preview, and cleanup commands."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


class DraftsCliTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.temp_home = tempfile.mkdtemp(prefix="rh-drafts-test-")
        self.addCleanup(lambda: shutil.rmtree(self.temp_home, ignore_errors=True))
        self.env = os.environ.copy()
        self.env["HOME"] = self.temp_home
        self.env["XDG_CONFIG_HOME"] = os.path.join(self.temp_home, ".config")
        self.drafts_dir = os.path.join(
            self.temp_home, ".config", "rh-support-cli", "drafts"
        )
        os.makedirs(self.drafts_dir, exist_ok=True)
        self.cli_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "rh-support-cli.py",
        )

    def _run_cli(self, *args: str, input: str | None = None) -> tuple[int, str, str]:
        import subprocess

        cmd = [sys.executable, self.cli_path, *args]
        res = subprocess.run(
            cmd, env=self.env, capture_output=True, text=True, input=input, check=False
        )
        return res.returncode, res.stdout, res.stderr

    def test_drafts_list_empty(self):
        """Verify drafts list reports empty state cleanly."""
        rc, stdout, _stderr = self._run_cli("drafts", "list")
        self.assertEqual(rc, 0)
        self.assertIn("No saved comment drafts found", stdout)

    def test_structured_formats_at_root_group_and_action_levels(self):
        import yaml

        path = os.path.join(self.drafts_dir, "comment_draft_12345_saved.txt")
        for fmt in ("json", "yaml"):
            for position in ("root", "group", "action"):
                for action in ("list", "show", "clear"):
                    with self.subTest(fmt=fmt, position=position, action=action):
                        with open(path, "w") as draft:
                            draft.write("Saved content")
                        suffix = (
                            ["12345"]
                            if action == "show"
                            else (["--yes"] if action == "clear" else [])
                        )
                        arguments = ["drafts", action, *suffix]
                        index = (
                            0
                            if position == "root"
                            else (1 if position == "group" else len(arguments))
                        )
                        arguments[index:index] = ["--format", fmt]
                        code, output, errors = self._run_cli(*arguments)
                        self.assertEqual(code, 0, errors)
                        result = (
                            json.loads(output)
                            if fmt == "json"
                            else yaml.safe_load(output)
                        )
                        self.assertIsInstance(
                            result, list if action == "list" else dict
                        )
                        if action == "clear":
                            self.assertEqual(result["deleted"], 1)

    def test_clear_confirmation_cancel_and_empty_receipts_are_structured(self):
        path = os.path.join(self.drafts_dir, "comment_draft_12345_saved.txt")
        with open(path, "w") as draft:
            draft.write("Saved content")
        code, output, errors = self._run_cli("drafts", "clear", "--json", input="n\n")
        self.assertEqual(code, 0, errors)
        self.assertTrue(json.loads(output)["cancelled"])
        self.assertIn("Delete 1", errors)
        self.assertTrue(os.path.exists(path))
        code, output, errors = self._run_cli("drafts", "clear", "--json", input="y\n")
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["deleted"], 1)
        code, output, errors = self._run_cli("drafts", "clear", "--json", "--yes")
        self.assertEqual(code, 0, errors)
        self.assertEqual(
            json.loads(output), {"deleted": 0, "skipped": 0, "cancelled": False}
        )

    def test_show_precedence_and_ambiguity(self):
        from pathlib import Path

        for name in ("comment_draft_12345_new.txt", "comment_draft_123_old.txt"):
            (Path(self.drafts_dir) / name).write_text(name)
        rc, stdout, _ = self._run_cli("drafts", "show", "000123", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(stdout)["case_number"], "123")
        (Path(self.drafts_dir) / "comment_draft_123_other.txt").write_text("other")
        rc, _, stderr = self._run_cli("drafts", "show", "123")
        self.assertNotEqual(rc, 0)
        self.assertIn("Ambiguous", stderr)
        rc, stdout, _ = self._run_cli(
            "drafts", "show", "comment_draft_123_old.txt", "--json"
        )
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(stdout)["file"], "comment_draft_123_old.txt")

    def test_discovery_sorts_subsecond_mtimes(self):
        from pathlib import Path

        from rh_support_lib.commands.drafts import find_drafts

        for name, ns in (("a", 1000000001), ("z", 1000000002)):
            path = Path(self.drafts_dir) / f"comment_draft_123_{name}.txt"
            path.write_text(name)
            os.utime(path, ns=(ns, ns))
        drafts = find_drafts(drafts_dir=self.drafts_dir)
        self.assertEqual(drafts[0]["content"], "z")

    def test_configured_directory_used_by_real_cli_editor_and_tui_save(self):
        from rh_support_lib import utils
        from rh_support_lib.commands.drafts import find_drafts
        from rh_support_lib.config import (
            ConfigError,
            _validate_config,
            resolve_drafts_dir,
        )
        from rh_support_lib.tui.app import SupportApp

        config = {"drafts_dir": self.drafts_dir}
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch("rh_support_lib.utils.subprocess.call", return_value=0),
        ):
            _, path = utils.open_editor("123", None, initial_body="CLI", config=config)
            self.assertEqual(os.path.dirname(path), self.drafts_dir)
            app = Mock(config=config, selected_case_id="456", _comment_generation=0)
            app.run_worker.side_effect = lambda fn, **kw: fn()
            SupportApp.save_comment_to_file(app, "# TUI body")
            self.assertEqual(
                find_drafts("456", self.drafts_dir)[0]["content"], "# TUI body"
            )
            for env, expected in (
                ({"RH_SUPPORT_DRAFTS": "/drafts"}, "/drafts"),
                ({"RH_SUPPORT_CONFIG": "/config/custom.yaml"}, "/config/drafts"),
                ({"XDG_CONFIG_HOME": "/xdg"}, "/xdg/rh-support-cli/drafts"),
            ):
                with patch.dict(os.environ, env, clear=True):
                    self.assertEqual(resolve_drafts_dir(), expected)
        with self.assertRaises(ConfigError):
            _validate_config({"drafts_dir": ["bad"]})

    def test_symlinked_storage_directory_is_consistent_across_draft_lifecycle(self):
        from pathlib import Path
        from types import SimpleNamespace

        from rh_support_lib import utils
        from rh_support_lib.commands.actions import cmd_comment
        from rh_support_lib.commands.drafts import find_drafts
        from rh_support_lib.tui.app import SupportApp

        target = Path(self.temp_home) / "physical"
        target.mkdir()
        alias = Path(self.temp_home) / "linked"
        alias.symlink_to(target, target_is_directory=True)
        config = {"drafts_dir": str(alias)}
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch("rh_support_lib.utils.subprocess.call", return_value=0),
        ):
            _, path = utils.open_editor(
                "123", None, initial_body="Saved body", config=config
            )
            self.assertEqual(len(find_drafts("123", str(alias))), 1)
            self.assertEqual(Path(path).parent, target)
            app = Mock(config=config, selected_case_id="456", _comment_generation=0)
            app.run_worker.side_effect = lambda fn, **kwargs: fn()
            SupportApp.save_comment_to_file(app, "TUI body")
            self.assertEqual(find_drafts("456", str(alias))[0]["content"], "TUI body")
            args = SimpleNamespace(
                case="123",
                file=None,
                edit=False,
                status=None,
                template=None,
                format="markdown",
                include_previous_comments=0,
            )
            with patch("rh_support_lib.commands.actions.CaseService") as service:
                service.return_value.add_comment.return_value = Mock(id="posted")
                cmd_comment(args, Mock(), config)
            self.assertEqual(find_drafts("123", str(alias)), [])

    def test_discovery_rejects_symlinks_and_survives_disappearance(self):
        from pathlib import Path

        from rh_support_lib.commands.drafts import find_drafts

        outside = Path(self.temp_home) / "secret.txt"
        outside.write_text("private outside content")
        link = Path(self.drafts_dir) / "comment_draft_123_link.txt"
        link.symlink_to(outside)
        self.assertEqual(find_drafts(drafts_dir=self.drafts_dir), [])
        link.unlink()
        path = Path(self.drafts_dir) / "comment_draft_123_gone.txt"
        path.write_text("gone")
        real_listdir = os.listdir

        def disappear(directory):
            names = real_listdir(directory)
            path.unlink()
            return names

        with patch("rh_support_lib.commands.drafts.os.listdir", side_effect=disappear):
            self.assertEqual(find_drafts(drafts_dir=self.drafts_dir), [])

    def test_clear_does_not_remove_replacement_after_confirmation(self):
        from pathlib import Path
        from types import SimpleNamespace

        from rh_support_lib.commands.drafts import cmd_drafts

        path = Path(self.drafts_dir) / "comment_draft_123_old.txt"
        path.write_text("old")

        def confirm(_):
            replacement = path.with_suffix(".new")
            replacement.write_text("new recovery copy")
            replacement.replace(path)
            return "yes"

        with patch("builtins.input", side_effect=confirm):
            cmd_drafts(
                SimpleNamespace(draft_action="clear"), {"drafts_dir": self.drafts_dir}
            )
        self.assertEqual(path.read_text(), "new recovery copy")

    def test_discovery_rechecks_inode_at_open_and_owner(self):
        from pathlib import Path

        from rh_support_lib.commands.drafts import find_drafts

        path = Path(self.drafts_dir) / "comment_draft_123_race.txt"
        path.write_text("original")
        with patch("rh_support_lib.utils.os.getuid", return_value=os.getuid() + 1):
            self.assertEqual(find_drafts(drafts_dir=self.drafts_dir), [])
        real_open = os.open

        def replace_before_open(name, flags, *args, **kwargs):
            if name == path.name:
                replacement = path.with_suffix(".new")
                replacement.write_text("replacement must not be read")
                replacement.replace(path)
            return real_open(name, flags, *args, **kwargs)

        with patch("rh_support_lib.utils.os.open", side_effect=replace_before_open):
            self.assertEqual(find_drafts(drafts_dir=self.drafts_dir), [])

    def test_clear_rechecks_ownership_acquired_while_confirmation_is_open(self):
        from contextlib import ExitStack
        from pathlib import Path
        from types import SimpleNamespace

        from rh_support_lib.commands.drafts import cmd_drafts
        from rh_support_lib.utils import draft_lock

        path = Path(self.drafts_dir) / "comment_draft_123_confirm.txt"
        path.write_text("sole recovery copy")
        with ExitStack() as owner:

            def confirm(_):
                self.assertTrue(owner.enter_context(draft_lock(path)))
                return "yes"

            with patch("builtins.input", side_effect=confirm):
                cmd_drafts(
                    SimpleNamespace(draft_action="clear"),
                    {"drafts_dir": self.drafts_dir},
                )
            self.assertTrue(path.exists())
        rc, _, stderr = self._run_cli("drafts", "clear", "-y")
        self.assertEqual(rc, 0, stderr)
        self.assertFalse(path.exists(), "Unlocked sidecar must not count as active")

    def test_drafts_discovery_filtering_and_show(self):
        """Verify drafts list, filtering by case, and drafts show preview."""
        # Create 2 drafts
        d1 = os.path.join(self.drafts_dir, "comment_draft_12345_first.txt")
        with open(d1, "w", encoding="utf-8") as f:
            f.write(
                "# Enter comment for Case 12345.\n# Leading lines starting with '#' will be ignored.\n\nFirst draft content for case 12345\n"
            )

        d2 = os.path.join(self.drafts_dir, "comment_draft_99999_second.txt")
        with open(d2, "w", encoding="utf-8") as f:
            f.write("Second draft content for case 99999\n")

        # 1. List all drafts (rich mode)
        rc, stdout, _ = self._run_cli("drafts")
        self.assertEqual(rc, 0)
        self.assertIn("Saved Comment Drafts (2)", stdout)
        self.assertIn("12345", stdout)
        self.assertIn("99999", stdout)

        # 2. Filter drafts by case
        rc, stdout, _ = self._run_cli(
            "drafts", "list", "-c", "12345", "--simple-output"
        )
        self.assertEqual(rc, 0)
        self.assertIn("12345", stdout)
        self.assertNotIn("99999", stdout)
        self.assertIn("First draft content", stdout)

        # 3. JSON format
        rc, stdout, _ = self._run_cli("drafts", "list", "--json")
        self.assertEqual(rc, 0)
        parsed = json.loads(stdout)
        self.assertEqual(len(parsed), 2)
        case_nums = [d["case_number"] for d in parsed]
        self.assertIn("12345", case_nums)
        self.assertIn("99999", case_nums)

        # 4. Show draft content
        rc, stdout, _ = self._run_cli("drafts", "show", "12345")
        self.assertEqual(rc, 0)
        self.assertIn("Draft: comment_draft_12345_first.txt", stdout)
        self.assertIn("First draft content for case 12345", stdout)

    def test_drafts_clear_with_confirmation_and_filtering(self):
        """Verify drafts clear removes specific or all draft files."""
        d1 = os.path.join(self.drafts_dir, "comment_draft_12345_one.txt")
        with open(d1, "w") as f:
            f.write("Draft 1")
        d2 = os.path.join(self.drafts_dir, "comment_draft_99999_two.txt")
        with open(d2, "w") as f:
            f.write("Draft 2")

        # Clear only case 12345
        rc, stdout, _ = self._run_cli("drafts", "clear", "-c", "12345", "-y")
        self.assertEqual(rc, 0)
        self.assertIn("Deleted 1 saved comment draft(s)", stdout)
        self.assertFalse(os.path.exists(d1))
        self.assertTrue(os.path.exists(d2))

        # Clear remaining
        rc, stdout, _ = self._run_cli("drafts", "clear", "-y")
        self.assertEqual(rc, 0)
        self.assertIn("Deleted 1 saved comment draft(s)", stdout)
        self.assertFalse(os.path.exists(d2))

    def test_comment_command_resumes_saved_draft(self):
        """Verify comment command automatically discovers and resumes an existing draft for the case."""
        d = os.path.join(self.drafts_dir, "comment_draft_12345_prev.txt")
        with open(d, "w") as f:
            f.write(
                "# Enter comment for Case 12345.\n# Leading lines starting with '#' will be ignored.\n\nResumed text from previous draft\n"
            )

        with (
            patch("rh_support_lib.commands.actions.open_editor") as mock_editor,
            patch("rh_support_lib.commands.actions.CaseService"),
        ):
            mock_editor.return_value = ("Resumed text from previous draft", d)
            from types import SimpleNamespace

            from rh_support_lib.commands.actions import cmd_comment

            args = SimpleNamespace(
                case="12345",
                file=None,
                edit=False,
                status=None,
                include_previous_comments=0,
                template=None,
                format="markdown",
            )
            # Run cmd_comment with mock client
            orig_home = os.environ.get("HOME")
            orig_config = os.environ.get("XDG_CONFIG_HOME")
            os.environ["HOME"] = self.temp_home
            os.environ["XDG_CONFIG_HOME"] = os.path.join(self.temp_home, ".config")
            try:
                cmd_comment(args, Mock())
                self.assertTrue(mock_editor.called)
                initial_body = mock_editor.call_args.kwargs.get("initial_body")
                self.assertEqual(initial_body, "Resumed text from previous draft")
            finally:
                if orig_home:
                    os.environ["HOME"] = orig_home
                else:
                    os.environ.pop("HOME", None)
                if orig_config:
                    os.environ["XDG_CONFIG_HOME"] = orig_config
                else:
                    os.environ.pop("XDG_CONFIG_HOME", None)
