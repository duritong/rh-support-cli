import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rh_support_lib.commands.actions import cmd_comment
from rh_support_lib.services.cases import CaseService


class CommentStatusTests(unittest.TestCase):
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
