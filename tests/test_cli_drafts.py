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

    def _run_cli(self, *args: str) -> tuple[int, str, str]:
        import subprocess

        cmd = [sys.executable, self.cli_path, *args]
        res = subprocess.run(
            cmd, env=self.env, capture_output=True, text=True, check=False
        )
        return res.returncode, res.stdout, res.stderr

    def test_drafts_list_empty(self):
        """Verify drafts list reports empty state cleanly."""
        rc, stdout, _stderr = self._run_cli("drafts", "list")
        self.assertEqual(rc, 0)
        self.assertIn("No saved comment drafts found", stdout)

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
