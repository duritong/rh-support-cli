"""Offline presentation regressions for confirmed and partial comment writes."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

from rh_support_lib.commands.actions import cmd_comment
from rh_support_lib.errors import CommentOutcomeUnknownError, PartialCommentError
from rh_support_lib.models import CaseComment
from rh_support_lib.tui.app import SupportApp
from rh_support_lib.utils import draft_identity


class CommentPresenterTests(unittest.TestCase):
    def test_cli_receipts_and_submitted_draft_cleanup(self):
        comment = CaseComment("comment-id", "body", "date", "Customer")
        for fmt in ("json", "yaml", None):
            for partial in (False, True):
                with (
                    self.subTest(fmt=fmt, partial=partial),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    draft = Path(directory) / "draft.txt"
                    draft.write_text("body")

                    def approve(*args, draft=draft, **kwargs):
                        kwargs["approval_info"]["identity"] = draft_identity(
                            draft.stat()
                        )
                        return "body", str(draft)

                    args = SimpleNamespace(
                        case="123",
                        template=None,
                        file=None,
                        edit=True,
                        status="closed",
                        include_previous_comments=0,
                        format="markdown",
                        json=fmt == "json",
                        output_format=fmt,
                    )
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with (
                        mock.patch(
                            "rh_support_lib.commands.actions.CaseService"
                        ) as service,
                        mock.patch(
                            "rh_support_lib.commands.actions.open_editor",
                            side_effect=approve,
                        ),
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        service.return_value.add_comment.return_value = comment
                        if partial:
                            service.return_value.add_comment.side_effect = (
                                PartialCommentError("123", comment, "Closed")
                            )
                            with self.assertRaises(SystemExit) as caught:
                                cmd_comment(args, mock.Mock())
                            self.assertEqual(caught.exception.code, 1)
                        else:
                            cmd_comment(args, mock.Mock())
                        service.return_value.add_comment.assert_called_once()
                    self.assertFalse(draft.exists())
                    self.assertNotIn("Draft comment preserved", stderr.getvalue())
                    if partial:
                        self.assertIn("Do not repost", stderr.getvalue())
                    if fmt:
                        receipt = (
                            json.loads(stdout.getvalue())
                            if fmt == "json"
                            else yaml.safe_load(stdout.getvalue())
                        )
                        self.assertEqual(receipt["comment_id"], "comment-id")
                        self.assertTrue(receipt["comment_posted"])
                        self.assertEqual(receipt["success"], not partial)
                        self.assertEqual(receipt["status_confirmed"], not partial)
                    elif partial:
                        self.assertNotIn("status updated", stdout.getvalue())

    def test_unknown_or_failed_post_preserves_recovery_copy_without_success(self):
        for error in (CommentOutcomeUnknownError("123"), RuntimeError("SECRET")):
            with (
                self.subTest(error=type(error).__name__),
                tempfile.TemporaryDirectory() as directory,
            ):
                draft = Path(directory) / "draft.txt"
                draft.write_text("body")
                args = SimpleNamespace(
                    case="123",
                    template=None,
                    file=None,
                    edit=True,
                    status=None,
                    include_previous_comments=0,
                    json=True,
                )
                stdout, stderr = io.StringIO(), io.StringIO()
                with (
                    mock.patch(
                        "rh_support_lib.commands.actions.CaseService"
                    ) as service,
                    mock.patch(
                        "rh_support_lib.commands.actions.open_editor",
                        return_value=("body", str(draft)),
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(SystemExit) as caught,
                ):
                    service.return_value.add_comment.side_effect = error
                    cmd_comment(args, mock.Mock())
                self.assertTrue(draft.exists())
                self.assertEqual(stdout.getvalue(), "")
                self.assertNotIn("SECRET", str(caught.exception))
                self.assertIn("recovery copy", stderr.getvalue())

    def test_tui_partial_clears_only_the_posted_body(self):
        for text in ("body", "new draft"):
            with self.subTest(text=text):
                editor = SimpleNamespace(text=text)
                app = mock.Mock()
                app._pending_comments = {4}
                app.selected_case_id = "123"
                app._comment_generation = 4
                app.query_one.side_effect = lambda selector, *args, editor=editor: (
                    editor if selector == "#tui-comment-textarea" else mock.Mock()
                )
                error = PartialCommentError(
                    "123", CaseComment("id", "body", "date", "Customer"), "Closed"
                )
                SupportApp._finish_comment_submission(
                    app, "123", "body", "Closed", 4, error
                )
                self.assertEqual(editor.text, "" if text == "body" else text)
                self.assertFalse(app._pending_comments)
                app.show_error.assert_called_once_with(str(error))
                app._request_case_details.assert_called_once_with("123", True)
