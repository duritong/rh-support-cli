"""Offline regressions for editor failures and recoverable drafts."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rh_support_lib import utils


class EditorTests(unittest.TestCase):
    def test_direct_open_editor_protects_atomic_saves_and_releases_on_failure(self):
        from rh_support_lib.commands.drafts import cmd_drafts

        directory = self.root / "direct"
        directory.mkdir()
        paths = []

        def edit(argv):
            path = Path(argv[-1])
            paths.append(path)
            replacement = path.with_suffix(".new")
            replacement.write_text(path.read_text())
            replacement.replace(path)
            cmd_drafts(
                SimpleNamespace(draft_action="clear", yes=True),
                {"drafts_dir": str(directory)},
            )
            self.assertTrue(path.exists())
            return 7

        self.editor.side_effect = edit
        with self.assertRaises(utils.EditorError):
            utils.open_editor(
                "123", None, initial_body="keep", config={"drafts_dir": str(directory)}
            )
        with utils.draft_lock(paths[-1]) as acquired:
            self.assertTrue(acquired, "Failure leaked active ownership")
        cmd_drafts(
            SimpleNamespace(draft_action="clear", yes=True),
            {"drafts_dir": str(directory)},
        )
        self.assertFalse(paths[-1].exists())

    def test_body_boundary_text_is_not_a_header(self):
        body = "Body\n# Leading lines starting with '#' will be ignored.\nkeep me\n"
        self.assertEqual(utils.strip_header_comments(body.splitlines(True)), body)
        code = "#!/bin/sh\nprintf hello\n# Leading lines starting with '#' will be ignored.\n"
        self.assertEqual(
            utils.strip_header_comments(code.splitlines(True)),
            code[code.index("printf") :],
        )

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for mocker in (
            patch.dict(os.environ, {"HOME": directory.name, "EDITOR": "vim -f"}),
            patch("tempfile.tempdir", directory.name),
        ):
            mocker.start()
            self.addCleanup(mocker.stop)
        editor = patch("rh_support_lib.utils.subprocess.call", return_value=0)
        self.editor = editor.start()
        self.addCleanup(editor.stop)

    def test_nonzero_exit_must_not_return_body_for_posting(self):
        self.editor.return_value = 7
        with self.assertRaises(utils.EditorError) as caught:
            utils.open_editor("123", None, initial_body="Unapproved private draft")
        error = caught.exception
        self.assertIsInstance(error, utils.EditorError)
        self.assertEqual(error.draft_path, self.editor.call_args.args[0][-1])
        self.assertIn("7", error.reason)
        self.assertIn("Unapproved private draft", Path(error.draft_path).read_text())
        self.assertNotIn("Unapproved private draft", str(error))
        self.editor.assert_called_once()

    def test_missing_editor_preserves_draft_instead_of_deleting_it(self):
        self.editor.side_effect = FileNotFoundError("private process details")
        with self.assertRaises(utils.EditorError) as caught:
            utils.open_editor("123", None, initial_body="Recover this draft")
        path = self.editor.call_args.args[0][-1]
        self.assertTrue(Path(path).exists(), "Editor launch failure deleted draft")
        self.assertIn("Recover this draft", Path(path).read_text())
        self.assertIsInstance(caught.exception, utils.EditorError)
        self.assertEqual(caught.exception.draft_path, path)
        self.assertIn("not found", caught.exception.reason.lower())
        self.assertNotIn("private process details", str(caught.exception))
        self.editor.assert_called_once()

    def test_edit_file_honors_editor_arguments_and_preserves_initial_body(self):
        path = self.root / "draft with spaces.txt"
        path.write_text("Initial body\n# actual content\n")
        self.assertIsNone(utils.edit_file(path))
        self.editor.assert_called_once_with(["vim", "-f", str(path)])
        self.assertEqual(path.read_text(), "Initial body\n# actual content\n")

    def test_default_editor(self):
        os.environ.pop("EDITOR")
        path = self.root / "draft.txt"
        path.write_text("Body")
        utils.edit_file(str(path))
        self.editor.assert_called_once_with(["vi", str(path)])

    def test_invalid_editor_commands_preserve_drafts_without_launching(self):
        for command in ("", "   ", 'vim "unterminated', "''", '"" -f'):
            with self.subTest(command=command):
                self.editor.reset_mock()
                with (
                    patch.dict(os.environ, {"EDITOR": command}),
                    self.assertRaises(utils.EditorError) as caught,
                ):
                    utils.open_editor("123", None, initial_body="Keep me")
                self.assertIn("EDITOR", caught.exception.reason)
                self.assertIn("Keep me", Path(caught.exception.draft_path).read_text())
                self.editor.assert_not_called()

    def test_edit_file_failure_never_approves_or_retries_initial_body(self):
        path = self.root / "draft.txt"
        initial = "Initial confidential body\n# preserve me\n"
        path.write_text(initial)
        for failure in (1, -15, FileNotFoundError(), PermissionError(), ValueError()):
            with self.subTest(failure=failure):
                self.editor.reset_mock()
                self.editor.side_effect = (
                    failure if isinstance(failure, Exception) else None
                )
                self.editor.return_value = failure
                with self.assertRaises(utils.EditorError) as caught:
                    utils.edit_file(str(path))
                self.assertEqual(caught.exception.draft_path, str(path))
                self.assertEqual(path.read_text(), initial)
                self.assertNotIn(initial, str(caught.exception))
                self.editor.assert_called_once()

    def test_read_failures_preserve_edited_draft_and_hide_raw_error(self):
        for failure in (
            PermissionError("private read details"),
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "private read details"),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.editor.reset_mock()
                with (
                    patch("rh_support_lib.utils.read_draft", side_effect=failure),
                    self.assertRaises(utils.EditorError) as caught,
                ):
                    utils.open_editor("123", None, initial_body="Recoverable body")
                error = caught.exception
                self.assertEqual(error.draft_path, self.editor.call_args.args[0][-1])
                self.assertIn("read", error.reason.lower())
                self.assertIn("Recoverable body", Path(error.draft_path).read_text())
                self.assertNotIn("private read details", str(error))
                self.editor.assert_called_once()

    def test_success_keeps_existing_header_stripping_and_content_fidelity(self):
        edited = (
            "# generated header\n  # previous comment\n"
            "Body with # inline\n\n# Actual heading\n"
            "```sh\n# shell comment\n  indented text  \n```\n"
        )

        def save_edit(argv):
            Path(argv[-1]).write_text(edited)
            return 0

        self.editor.side_effect = save_edit
        body, path = utils.open_editor("123", "Waiting", header_content="Old\nComment")
        self.assertEqual(body, edited[edited.index("Body") :].strip())
        self.assertEqual(Path(path).read_text(), edited)
        self.editor.assert_called_once_with(["vim", "-f", path])

    def test_unchanged_initial_body_is_returned_only_after_success(self):
        initial = "Body\n\n# Actual heading\n  keep indentation\n"
        body, path = utils.open_editor("123", None, initial_body=initial)
        self.assertEqual(body, initial.strip())
        self.assertTrue(Path(path).read_text().endswith(initial))
        self.editor.assert_called_once()

    def test_empty_body_is_returned_with_preserved_path(self):
        for edited in ("", "# header only\n  # another header\n", " \n\t\n"):
            with self.subTest(edited=edited):

                def save_edit(argv, edited=edited):
                    Path(argv[-1]).write_text(edited)
                    return 0

                self.editor.side_effect = save_edit
                body, path = utils.open_editor("123", None)
                self.assertEqual(body, "")
                self.assertEqual(Path(path).read_text(), edited)


class EditorCommandTests(unittest.TestCase):
    def test_editor_replacement_after_approval_read_is_not_retired_as_submitted(self):
        from rh_support_lib.commands.actions import cmd_comment

        source = self.root / "body.txt"
        source.write_text("Approved body")
        edited = []

        def editor(*args, **kwargs):
            body, path = utils.open_editor(*args, **kwargs)
            edited.append(Path(path))
            replacement = Path(path).with_suffix(".new")
            replacement.write_text("# New unsubmitted heading\nApproved body")
            replacement.replace(path)
            return body, path

        with (
            patch("rh_support_lib.utils.subprocess.call", return_value=0),
            patch("rh_support_lib.commands.actions.open_editor", side_effect=editor),
            patch("rh_support_lib.commands.actions.CaseService") as service,
        ):
            service.return_value.add_comment.return_value = Mock(id="c1")
            cmd_comment(
                self.args(file=str(source), edit=True),
                Mock(),
                {"drafts_dir": str(self.root / "drafts")},
            )
        self.assertEqual(
            service.return_value.add_comment.call_args.args[1], "Approved body"
        )
        self.assertEqual(
            edited[0].read_text(), "# New unsubmitted heading\nApproved body"
        )

    def test_cleanup_cannot_delete_source_or_atomically_replaced_editor_during_post(
        self,
    ):
        import subprocess
        import sys

        from rh_support_lib.commands.actions import cmd_comment

        drafts = self.root / "active"
        drafts.mkdir()
        source = drafts / "comment_draft_123_source.txt"
        source.write_text("saved body")
        paths = []

        def clear():
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(Path(__file__).resolve().parents[1] / "rh-support-cli.py"),
                    "drafts",
                    "clear",
                    "-y",
                ],
                env={**os.environ, "RH_SUPPORT_DRAFTS": str(drafts)},
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("active", result.stdout)
            self.assertTrue(source.exists())
            self.assertTrue(paths[-1].exists())

        def edit(argv):
            path = Path(argv[-1])
            paths.append(path)
            replacement = path.with_suffix(".new")
            replacement.write_text(path.read_text())
            replacement.replace(path)
            clear()
            return 0

        def post(*args, **kwargs):
            clear()
            return Mock(id="c1")

        with (
            patch("rh_support_lib.utils.subprocess.call", side_effect=edit),
            patch("rh_support_lib.commands.actions.CaseService") as service,
        ):
            service.return_value.add_comment.side_effect = post
            cmd_comment(self.args(), Mock(), {"drafts_dir": str(drafts)})
        self.assertFalse(source.exists())
        self.assertFalse(paths[-1].exists())

    def test_resume_retires_distinct_source_only_after_confirmed_post(self):
        from rh_support_lib.commands.actions import cmd_comment
        from rh_support_lib.errors import (
            CommentOutcomeUnknownError,
            PartialCommentError,
        )

        drafts = self.root / "resume"
        drafts.mkdir()
        source = drafts / "comment_draft_123_source.txt"
        for outcome in (None, "partial", "unknown", "failure", "new_edits"):
            with self.subTest(outcome=outcome):
                for path in drafts.glob("*.txt"):
                    path.unlink()
                source.write_text("# Heading\nSaved body")
                edited_paths = []

                def edit(argv, edited_paths=edited_paths):
                    edited_paths.append(Path(argv[-1]))
                    self.assertNotEqual(edited_paths[-1], source)
                    return 0

                def post(*args, outcome=outcome, edited_paths=edited_paths, **kw):
                    if outcome == "partial":
                        raise PartialCommentError("123", Mock(id="c1"), "Waiting")
                    if outcome == "unknown":
                        raise CommentOutcomeUnknownError("123")
                    if outcome == "failure":
                        raise RuntimeError("failed")
                    if outcome == "new_edits":
                        source.write_text("new source edits")
                        edited_paths[-1].write_text("new editor edits")
                    return Mock(id="c1")

                with (
                    patch("rh_support_lib.utils.subprocess.call", side_effect=edit),
                    patch("rh_support_lib.commands.actions.CaseService") as service,
                ):
                    service.return_value.add_comment.side_effect = post
                    if outcome in ("partial", "unknown", "failure"):
                        with self.assertRaises(SystemExit):
                            cmd_comment(
                                self.args(), Mock(), {"drafts_dir": str(drafts)}
                            )
                    else:
                        cmd_comment(self.args(), Mock(), {"drafts_dir": str(drafts)})
                keep = outcome in ("unknown", "failure", "new_edits")
                self.assertEqual(source.exists(), keep)
                self.assertEqual(edited_paths[-1].exists(), keep)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for patcher in (
            patch.dict(os.environ, {"HOME": directory.name, "EDITOR": "vim -f"}),
            patch("tempfile.tempdir", directory.name),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def args(self, **updates):
        values = {
            "case": "123",
            "template": None,
            "template_var": [],
            "file": None,
            "edit": False,
            "status": None,
            "include_previous_comments": 0,
            "no_default_template": True,
            "product": "RHEL",
            "version": "9.0",
            "summary": "Summary",
            "severity": "Low",
            "type": "Standard",
            "description_file": None,
            "attachment": [],
        }
        return SimpleNamespace(**{**values, **updates})

    def test_failed_comment_edit_preserves_both_input_and_recovery_draft(self):
        from rh_support_lib.commands.actions import cmd_comment

        source = self.root / "body.txt"
        source.write_text("Initial private draft")
        with (
            patch("rh_support_lib.commands.actions.CaseService") as service,
            patch("rh_support_lib.utils.subprocess.call", return_value=7) as editor,
            self.assertRaisesRegex(SystemExit, "Editor Error"),
        ):
            cmd_comment(self.args(file=str(source), edit=True), Mock())
        service.return_value.add_comment.assert_not_called()
        self.assertEqual(source.read_text(), "Initial private draft")
        self.assertIn(
            "Initial private draft", Path(editor.call_args.args[0][-1]).read_text()
        )

    def test_create_honors_editor_arguments_and_preserves_failed_draft(self):
        from rh_support_lib.commands.create_case import cmd_create

        for status in (7, 0):
            with self.subTest(status=status):

                def edit(argv, status=status):
                    self.assertEqual(argv[:2], ["vim", "-f"])
                    Path(argv[-1]).write_text("Edited description")
                    return status

                with (
                    patch("rh_support_lib.commands.create_case.CaseService") as service,
                    patch(
                        "rh_support_lib.commands.create_case.prompt_text",
                        return_value="y",
                    ),
                    patch(
                        "rh_support_lib.utils.subprocess.call", side_effect=edit
                    ) as editor,
                ):
                    service.return_value.create_case.return_value = "123"
                    if status:
                        with self.assertRaisesRegex(SystemExit, "Editor Error"):
                            cmd_create(self.args(), Mock(), {})
                        service.return_value.create_case.assert_not_called()
                        self.assertEqual(
                            Path(editor.call_args.args[0][-1]).read_text(),
                            "Edited description",
                        )
                    else:
                        self.assertEqual(cmd_create(self.args(), Mock(), {}), "123")
                        self.assertEqual(
                            service.return_value.create_case.call_args.kwargs[
                                "description"
                            ],
                            "Edited description",
                        )

    def test_empty_file_comments_and_close_never_mutate_status(self):
        from rh_support_lib.commands.actions import cmd_close, cmd_comment

        source = self.root / "empty.txt"
        source.write_text("   \n\t\n")
        for command in (cmd_comment, cmd_close):
            with (
                self.subTest(command=command.__name__),
                patch("rh_support_lib.commands.actions.CaseService") as service,
                self.assertRaisesRegex(SystemExit, "Empty comment"),
            ):
                command(self.args(file=str(source)), Mock())
            service.return_value.add_comment.assert_not_called()
        self.assertTrue(source.exists())

    def test_file_imports_preserve_leading_markdown_headings_verbatim(self):
        from rh_support_lib.commands.actions import cmd_comment
        from rh_support_lib.commands.create_case import cmd_create

        source = self.root / "doc.md"
        source.write_text("# Incident Root Cause\nActual explanation\n")

        with (
            patch("rh_support_lib.commands.actions.CaseService") as comment_service,
            patch("rh_support_lib.commands.create_case.CaseService") as create_service,
            patch("rh_support_lib.commands.create_case.prompt_text", return_value="y"),
        ):
            comment_service.return_value.add_comment.return_value = Mock(id="c1")
            create_service.return_value.create_case.return_value = "1001"

            cmd_comment(self.args(file=str(source)), Mock())
            comment_body = comment_service.return_value.add_comment.call_args.args[1]
            self.assertTrue(comment_body.startswith("# Incident Root Cause"))

            cmd_create(self.args(description_file=str(source)), Mock(), {})
            desc = create_service.return_value.create_case.call_args.kwargs[
                "description"
            ]
            self.assertTrue(desc.startswith("# Incident Root Cause"))

    def test_generated_recovery_file_strips_context_but_raw_resume_keeps_heading(self):
        from rh_support_lib.commands.actions import cmd_comment

        source = self.root / "recovery.txt"
        source.write_text(
            "# Enter comment for Case 123.\n# private context\n"
            "# Leading lines starting with '#' will be ignored.\n\n# Real heading\nBody"
        )
        with patch("rh_support_lib.commands.actions.CaseService") as service:
            cmd_comment(self.args(file=str(source)), Mock())
            self.assertEqual(
                service.return_value.add_comment.call_args.args[1],
                "# Real heading\nBody",
            )
        drafts = self.root / "drafts"
        drafts.mkdir()
        (drafts / "comment_draft_123_raw.txt").write_text("# Real heading\nBody")
        with (
            patch("rh_support_lib.commands.actions.CaseService"),
            patch(
                "rh_support_lib.commands.actions.open_editor",
                return_value=("Body", None),
            ) as editor,
        ):
            cmd_comment(self.args(), Mock(), {"drafts_dir": str(drafts)})
            self.assertEqual(
                editor.call_args.kwargs["initial_body"], "# Real heading\nBody"
            )

    def test_empty_description_file_never_launches_editor_or_creates_case(self):
        from rh_support_lib.commands.create_case import cmd_create

        source = self.root / "empty.txt"
        source.write_text(" \n")
        with (
            patch("rh_support_lib.commands.create_case.CaseService") as service,
            patch("rh_support_lib.utils.subprocess.call") as editor,
            self.assertRaisesRegex(SystemExit, "Empty description"),
        ):
            cmd_create(self.args(description_file=str(source)), Mock(), {})
        service.return_value.create_case.assert_not_called()
        editor.assert_not_called()
        self.assertTrue(source.exists())

    def test_core_rejects_empty_content_before_any_lookup(self):
        from rh_support_lib.services.cases import CaseService

        client = Mock()
        service = CaseService(client, state_service=Mock())
        with self.assertRaises(ValueError):
            service.add_comment("123", " ")
        for summary, description in (("", "description"), ("summary", " ")):
            with self.subTest(summary=summary), self.assertRaises(ValueError):
                service.create_case(summary, description)
        client.execute_graphql.assert_not_called()
