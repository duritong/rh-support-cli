"""Offline creation presenter and argument-boundary tests."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from rh_support_lib.commands.create_case import cmd_create
from rh_support_lib.main import _main
from rh_support_lib.services.cases import CaseService


class CreationCLITests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.description = self.root / "description.txt"
        self.description.write_text("# Header\nCase description", encoding="utf-8")
        self.file = self.root / "diagnostics.txt"
        self.file.write_text("diagnostics", encoding="utf-8")
        self.client = Mock()
        for name in ("CaseService", "AttachmentService", "prompt_text", "edit_file"):
            patcher = patch(f"rh_support_lib.commands.create_case.{name}")
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.cases = self.CaseService.return_value
        self.cases.create_case.return_value = "123456"
        self.uploads = self.AttachmentService.return_value.upload_attachment
        self.uploads.return_value = SimpleNamespace(
            uuid="attachment-1", upload_status="Completed"
        )
        self.prompt_text.side_effect = AssertionError("Unexpected prompt")
        self.edit_file.side_effect = AssertionError("Unexpected editor")

    def args(self, **overrides):
        return SimpleNamespace(
            **{
                "product": "Red Hat Enterprise Linux",
                "version": "9.0",
                "summary": "Case summary",
                "severity": "High",
                "type": "Standard",
                "description_file": str(self.description),
                "attachment": [],
                "template": [],
                "template_var": [],
                "yes": True,
                "json": True,
                "output_format": None,
                "format": "markdown",
                **overrides,
            }
        )

    def invoke(self, args, config=None, command=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        code, result = 0, None
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                result = (
                    command()
                    if command
                    else cmd_create(args, self.client, config or {})
                )
            except SystemExit as error:
                if isinstance(error.code, str):
                    print(error.code, file=stderr)
                    code = 1
                else:
                    code = error.code or 0
        return code, stdout.getvalue(), stderr.getvalue(), result

    def test_yes_success_receipt_and_content_format(self):
        for body_format in ("markdown", "plain", "html", "raw"):
            with self.subTest(body_format=body_format):
                self.cases.create_case.reset_mock()
                code, output, errors, result = self.invoke(
                    self.args(format=body_format)
                )
                self.assertEqual(code, 0)
                self.assertEqual(result, "123456")
                self.assertEqual(
                    json.loads(output),
                    {
                        "case_number": "123456",
                        "created": True,
                        "success": True,
                        "attachments": [],
                    },
                )
                self.assertIn("Creating case", errors)
                self.cases.create_case.assert_called_once()
                fields = self.cases.create_case.call_args.kwargs
                self.assertEqual(fields["body_format"], body_format)
                self.assertEqual(fields["description"], "Case description")
        self.prompt_text.assert_not_called()
        self.edit_file.assert_not_called()

    def test_yes_requires_every_required_value_without_interaction(self):
        for field in ("product", "version", "summary", "severity", "type"):
            for value in (None, "", "  "):
                with self.subTest(field=field, value=value):
                    code, output, errors, _ = self.invoke(self.args(**{field: value}))
                    self.assertNotEqual(code, 0)
                    self.assertEqual(output, "")
                    self.assertIn(field, errors)
        self.cases.create_case.assert_not_called()
        self.cases.get_active_products.assert_not_called()
        self.prompt_text.assert_not_called()
        self.edit_file.assert_not_called()

    def test_yes_missing_description_never_opens_editor(self):
        code, output, errors, _ = self.invoke(self.args(description_file=None))
        self.assertNotEqual(code, 0)
        self.assertEqual(output, "")
        self.assertIn("description", errors)
        self.edit_file.assert_not_called()
        self.prompt_text.assert_not_called()
        self.cases.create_case.assert_not_called()

    def test_template_can_supply_required_values(self):
        (self.root / "complete.yaml").write_text(
            yaml.safe_dump(
                {
                    "product": "RHEL",
                    "version": "9",
                    "summary": "Summary",
                    "severity": "Normal",
                    "caseType": "Standard",
                    "description": "Body",
                }
            ),
            encoding="utf-8",
        )
        args = self.args(
            product=None,
            version=None,
            summary=None,
            severity=None,
            type=None,
            description_file=None,
            template=["complete"],
        )
        code, output, _, _ = self.invoke(args, {"templates_dir": str(self.root)})
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["success"])
        self.prompt_text.assert_not_called()
        self.edit_file.assert_not_called()

    def test_default_confirmation_is_not_bypassed_by_json(self):
        self.prompt_text.side_effect = None
        self.prompt_text.return_value = "y"
        code, output, errors, _ = self.invoke(self.args(yes=False))
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["created"])
        self.prompt_text.assert_called_once_with(
            "Submit this case? (y/n/edit)", default="y"
        )
        self.assertIn("New Case Overview", errors)

    def test_yes_numeric_template_severity_and_version_reach_core(self):
        service = CaseService(self.client, cache_service=Mock(scope=None))
        self.CaseService.return_value = service
        self.client.execute_graphql.return_value = {
            "redhat_support_uiapi": {
                "CaseCreate": {"RedHatSupportRecord": {"caseNumber": "123456"}}
            }
        }
        for severity, priority in (
            (1, "1 (Urgent)"),
            (2, "2 (High)"),
            (3, "3 (Normal)"),
            (4, "4 (Low)"),
        ):
            with self.subTest(severity=severity):
                (self.root / "numeric.yaml").write_text(
                    yaml.safe_dump({"severity": severity, "version": 9.0}),
                    encoding="utf-8",
                )
                with patch.object(service, "resolve_case_lookups", return_value={}):
                    code, output, _, _ = self.invoke(
                        self.args(severity=None, version=None, template=["numeric"]),
                        {"templates_dir": str(self.root)},
                    )
                self.assertEqual(code, 0)
                self.assertTrue(json.loads(output)["success"])
                payload = self.client.execute_graphql.call_args.args[1]["input"][
                    "RedHatSupportCase"
                ]
                self.assertEqual(payload["Priority"], priority)
                self.assertEqual(payload["version"], "9.0")
        self.prompt_text.assert_not_called()
        self.edit_file.assert_not_called()

    def test_default_confirmation_can_cancel_or_edit(self):
        self.prompt_text.side_effect = ["n"]
        code, output, _, _ = self.invoke(self.args(yes=False))
        self.assertEqual(code, 0)
        self.assertEqual(output, "")
        self.cases.create_case.assert_not_called()
        self.prompt_text.side_effect = ["edit", "summary", "Edited", "y"]
        code, output, _, _ = self.invoke(self.args(yes=False))
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["success"])
        self.assertEqual(self.cases.create_case.call_args.kwargs["summary"], "Edited")

    def test_partial_failure_receipt_nonzero_and_no_retry(self):
        secret = "Bearer SECRET https://storage.invalid/?token=SECRET"
        self.uploads.side_effect = [RuntimeError(secret), self.uploads.return_value]
        code, output, errors, _ = self.invoke(
            self.args(attachment=[str(self.file), str(self.description)])
        )
        self.assertEqual(code, 1)
        receipt = json.loads(output)
        self.assertTrue(receipt["created"])
        self.assertFalse(receipt["success"])
        self.assertEqual([a["uploaded"] for a in receipt["attachments"]], [False, True])
        self.assertIn("Do not repeat case creation", errors)
        self.assertNotIn("SECRET", output + errors)
        self.cases.create_case.assert_called_once()
        self.assertEqual(self.uploads.call_count, 2)
        self.assertTrue(self.description.exists())

    def test_upload_cancellation_is_partial_nonzero(self):
        self.uploads.side_effect = KeyboardInterrupt
        code, output, errors, _ = self.invoke(self.args(attachment=[str(self.file)]))
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output)["success"])
        self.assertIn("Do not repeat", errors)

    def test_human_partial_reports_created_case_and_errors_on_stderr(self):
        self.uploads.return_value = SimpleNamespace(upload_status="Pending")
        code, output, errors, _ = self.invoke(
            self.args(json=False, attachment=[str(self.file)])
        )
        self.assertEqual(code, 1)
        self.assertIn("Case #123456 created", output)
        self.assertNotIn("not confirmed", output)
        self.assertIn("not confirmed", errors)
        self.assertIn("Do not repeat", errors)

    def test_cleanup_failure_does_not_hide_created_receipt(self):
        self.prompt_text.side_effect = None
        self.prompt_text.return_value = "y"
        self.edit_file.side_effect = lambda path: Path(path).write_text("Body")
        factory = tempfile.NamedTemporaryFile

        def temporary(**kwargs):
            return factory(dir=self.root, **kwargs)

        with (
            patch(
                "rh_support_lib.commands.create_case.tempfile.NamedTemporaryFile",
                side_effect=temporary,
            ),
            patch("rh_support_lib.commands.create_case.os.remove", side_effect=OSError),
        ):
            code, output, errors, _ = self.invoke(
                self.args(yes=False, description_file=None)
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["created"])
        self.assertIn("Submitted description copy remains", errors)
        self.assertNotIn("Draft description preserved", errors)

    def test_bad_inputs_leave_stdout_empty_and_never_create(self):
        for overrides in (
            {"attachment": [str(self.root / "missing")]},
            {"attachment": [str(self.root)]},
            {"description_file": str(self.root / "missing")},
        ):
            with self.subTest(overrides=overrides):
                code, output, _, _ = self.invoke(self.args(**overrides))
                self.assertNotEqual(code, 0)
                self.assertEqual(output, "")
        self.cases.create_case.assert_not_called()
        self.uploads.assert_not_called()

    def test_creation_failures_do_not_emit_receipt_or_leak_exception(self):
        for error in (ValueError("SECRET"), RuntimeError("SECRET")):
            with self.subTest(error=type(error)):
                self.cases.create_case.side_effect = error
                code, output, errors, _ = self.invoke(self.args())
                self.assertNotEqual(code, 0)
                self.assertEqual(output, "")
                self.assertNotIn("SECRET", errors)
                self.assertIn("before retrying", errors)
                if isinstance(error, ValueError):
                    self.assertIn("Check product/version", errors)
                    self.assertIn("REDHAT_SUPPORT_USERNAME", errors)
        self.uploads.assert_not_called()

    def test_unknown_creation_outcome_is_not_success(self):
        self.cases.create_case.return_value = None
        code, output, errors, _ = self.invoke(self.args())
        self.assertNotEqual(code, 0)
        self.assertEqual(output, "")
        self.assertIn("outcome is unknown", errors)
        self.uploads.assert_not_called()

    def test_generated_drafts_preserved_on_failure_not_unsent_on_partial(self):
        for stage in ("creation", "partial", "success"):
            with self.subTest(stage=stage):
                draft = self.root / f"{stage}.tmp"
                self.edit_file.side_effect = lambda path: Path(path).write_text("Body")
                self.prompt_text.side_effect = None
                self.prompt_text.return_value = "y"
                self.cases.create_case.side_effect = (
                    RuntimeError("SECRET") if stage == "creation" else None
                )
                self.uploads.side_effect = (
                    RuntimeError("SECRET") if stage == "partial" else None
                )
                # Use a real generated draft, but put it in the test-owned directory.
                factory = tempfile.NamedTemporaryFile

                def temporary(factory=factory, **kwargs):
                    return factory(dir=self.root, **kwargs)

                with patch(
                    "rh_support_lib.commands.create_case.tempfile.NamedTemporaryFile",
                    side_effect=temporary,
                ):
                    code, output, errors, _ = self.invoke(
                        self.args(
                            yes=False,
                            description_file=None,
                            attachment=[str(self.file)],
                        )
                    )
                drafts = list(self.root.glob("*.tmp"))
                if stage == "creation":
                    self.assertNotEqual(code, 0)
                    self.assertEqual(len(drafts), 1)
                    self.assertIn("Draft description preserved", errors)
                    drafts[0].rename(draft.with_suffix(".saved"))
                else:
                    self.assertEqual(drafts, [])
                    self.assertTrue(json.loads(output)["created"])
                    self.assertNotIn("Draft description preserved", errors)
                    self.assertEqual(code, 1 if stage == "partial" else 0)

    def test_output_format_is_separate_and_json_takes_precedence(self):
        for fmt, json_flag in (("json", False), ("yaml", False), ("yaml", True)):
            with self.subTest(fmt=fmt, json=json_flag):
                code, output, _, _ = self.invoke(
                    self.args(output_format=fmt, json=json_flag, format="raw")
                )
                self.assertEqual(code, 0)
                receipt = (
                    json.loads(output)
                    if json_flag or fmt == "json"
                    else yaml.safe_load(output)
                )
                self.assertTrue(receipt["success"])
                self.assertEqual(
                    self.cases.create_case.call_args.kwargs["body_format"], "raw"
                )

    def test_main_parses_yes_and_both_json_positions(self):
        for prefix, suffix in (
            (["--json"], []),
            ([], ["--json"]),
            (["--format", "json"], []),
        ):
            with self.subTest(prefix=prefix, suffix=suffix):
                argv = [
                    "rh-support-cli",
                    *prefix,
                    "create",
                    "--yes",
                    "--product",
                    "RHEL",
                    "--version",
                    "9",
                    "--summary",
                    "Summary",
                    "--severity",
                    "High",
                    "--type",
                    "Standard",
                    "--description-file",
                    str(self.description),
                    "--format",
                    "plain",
                    *suffix,
                ]
                with (
                    patch("sys.argv", argv),
                    patch("rh_support_lib.main.argcomplete", None),
                    patch("rh_support_lib.main.load_config", return_value={}),
                    patch(
                        "rh_support_lib.api.RedHatAPIClient", return_value=self.client
                    ),
                ):
                    code, output, _, _ = self.invoke(None, command=_main)
                self.assertEqual(code, 0)
                self.assertTrue(json.loads(output)["success"])
                self.assertEqual(
                    self.cases.create_case.call_args.kwargs["body_format"], "plain"
                )
        self.prompt_text.assert_not_called()
