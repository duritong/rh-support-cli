import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rh_support_lib.api import AuthenticationError
from rh_support_lib.commands.actions import cmd_comment
from rh_support_lib.errors import ReadError
from rh_support_lib.services.cases import CaseService


class CommentStatusTests(unittest.TestCase):
    def test_unavailable_initial_status_stops_before_editing_or_posting(self):
        service = CaseService(Mock(), no_cache=True)
        args = SimpleNamespace(
            case="123", file=None, edit=True, status=None, include_previous_comments=0
        )
        with (
            patch("rh_support_lib.commands.actions.CaseService", return_value=service),
            patch.object(service, "check_case_last_modified", return_value=None),
            patch.object(
                service, "get_case_details", side_effect=ReadError("Unavailable.")
            ),
            patch.object(service, "add_comment") as post,
            patch(
                "rh_support_lib.commands.actions.open_editor",
                return_value=("Reply", None),
            ) as editor,
            patch("sys.stdout", new_callable=io.StringIO) as output,
            patch("sys.stderr", new_callable=io.StringIO),
            self.assertRaisesRegex(SystemExit, "No comment or status change submitted"),
        ):
            cmd_comment(args, Mock())
        post.assert_not_called()
        editor.assert_not_called()
        self.assertEqual(output.getvalue(), "")

    def test_failed_post_editor_status_check_preserves_draft_without_posting(self):
        for error in (
            ReadError("Unavailable."),
            AuthenticationError("Authentication required."),
        ):
            with (
                self.subTest(error=type(error).__name__),
                tempfile.TemporaryDirectory() as directory,
            ):
                draft = Path(directory) / "comment.txt"
                draft.write_text("Customer reply", encoding="utf-8")
                service = CaseService(Mock(), no_cache=True)
                args = SimpleNamespace(
                    case="123",
                    file=None,
                    edit=True,
                    status=None,
                    include_previous_comments=0,
                )
                with (
                    patch(
                        "rh_support_lib.commands.actions.CaseService",
                        return_value=service,
                    ),
                    patch.object(
                        service,
                        "check_case_last_modified",
                        side_effect=[
                            ("id", "date", "Waiting on Customer", "", ""),
                            None,
                        ],
                    ),
                    patch.object(service, "get_case_details", side_effect=error),
                    patch.object(service, "add_comment") as post,
                    patch(
                        "rh_support_lib.commands.actions.open_editor",
                        return_value=("Customer reply", str(draft)),
                    ),
                    patch("sys.stdout", new_callable=io.StringIO) as output,
                    patch("sys.stderr", new_callable=io.StringIO) as errors,
                    self.assertRaises(
                        AuthenticationError
                        if isinstance(error, AuthenticationError)
                        else SystemExit
                    ),
                ):
                    cmd_comment(args, Mock())
                post.assert_not_called()
                self.assertEqual(output.getvalue(), "")
                self.assertIn(str(draft), errors.getvalue())
                self.assertEqual(draft.read_text(encoding="utf-8"), "Customer reply")

    def test_implicit_status_is_rechecked_after_editing(self):
        for before, after, expected in (
            ("Waiting on Customer", "Closed", None),
            ("Waiting on Customer", "Waiting on Engineering", None),
            ("In Progress", "Waiting on Customer", "In Progress"),
        ):
            with self.subTest(before=before, after=after):
                service = Mock(spec=CaseService)
                current = {"status": before}
                service.get_case_status.side_effect = lambda _, current=current: (
                    current["status"]
                )

                def edit(*args, current=current, after=after, **kwargs):
                    current["status"] = after
                    return "Customer reply", None

                args = SimpleNamespace(
                    case="123",
                    file=None,
                    edit=True,
                    status=None,
                    include_previous_comments=0,
                )
                with (
                    patch(
                        "rh_support_lib.commands.actions.CaseService",
                        return_value=service,
                    ),
                    patch(
                        "rh_support_lib.commands.actions.open_editor", side_effect=edit
                    ),
                    patch("sys.stdout", new_callable=io.StringIO),
                    patch("sys.stderr", new_callable=io.StringIO),
                ):
                    cmd_comment(args, Mock())
                service.add_comment.assert_called_once_with(
                    "123",
                    "Customer reply",
                    is_public=True,
                    status=expected,
                    body_format="markdown",
                )

    def test_explicit_status_choices_do_not_read_current_status(self):
        for status, keep, expected in (
            ("closed", False, "Closed"),
            (None, True, None),
            ("keep", False, None),
        ):
            with self.subTest(status=status, keep=keep):
                service = Mock(spec=CaseService)
                args = SimpleNamespace(
                    case="123",
                    file=None,
                    edit=True,
                    status=status,
                    no_status_change=keep,
                    include_previous_comments=0,
                )
                with (
                    patch(
                        "rh_support_lib.commands.actions.CaseService",
                        return_value=service,
                    ),
                    patch(
                        "rh_support_lib.commands.actions.open_editor",
                        return_value=("Reply", None),
                    ),
                    patch("sys.stdout", new_callable=io.StringIO),
                    patch("sys.stderr", new_callable=io.StringIO),
                ):
                    cmd_comment(args, Mock())
                service.get_case_status.assert_not_called()
                self.assertEqual(
                    service.add_comment.call_args.kwargs["status"], expected
                )
