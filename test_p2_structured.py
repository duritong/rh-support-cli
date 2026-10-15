import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

from rh_support_lib.commands.apply_template import cmd_apply, cmd_list_templates
from rh_support_lib.commands.tracking import cmd_updates
from rh_support_lib.errors import ReadError
from rh_support_lib.models import Case, CaseDetails, TrackedCase


class StructuredOutputTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        env = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.root),
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_CACHE_HOME": str(self.root / "cache"),
                "RH_SUPPORT_TEMPLATES": str(self.root),
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.config = {"templates_dir": str(self.root)}
        self.client = mock.Mock()
        for module in ("tracking", "apply_template"):
            patcher = mock.patch(f"rh_support_lib.commands.{module}.CaseService")
            setattr(self, module, patcher.start().return_value)
            self.addCleanup(patcher.stop)
        self.apply_template.get_case_details.return_value = CaseDetails(
            Case("123", "old", "Open", "High"), "old description"
        )

    def args(self, fmt="json", **kwargs):
        return SimpleNamespace(
            **{
                "format": fmt,
                "json": False,
                "case": "123",
                "user": "test-user",
                "mark_read": True,
                "all": False,
                "full": False,
                "template": ["policy"],
                "template_var": [],
                "dry_run": False,
                "simple_output": True,
                **kwargs,
            }
        )

    def invoke(self, command, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                command(*args)
            except SystemExit as error:
                if isinstance(error.code, str):
                    print(error.code, file=stderr)
                    code = 1
                else:
                    code = error.code or 0
        return code, stdout.getvalue(), stderr.getvalue()

    def decode(self, fmt, output):
        return yaml.safe_load(output) if fmt == "yaml" else json.loads(output)

    def test_empty_updates_are_structured_without_checkpoint_changes(self):
        for fmt in ("json", "yaml", "text"):
            for tracked, target in (
                ({}, None),
                ({"123": TrackedCase("123")}, "unknown"),
                ({"123": TrackedCase("123")}, "123"),
            ):
                with self.subTest(fmt=fmt, tracked=bool(tracked), target=target):
                    self.tracking.state_service.get_tracked_cases.return_value = tracked
                    code, output, errors = self.invoke(
                        cmd_updates,
                        self.args(fmt, case=target, json=fmt == "text"),
                        self.client,
                        self.config,
                    )
                    self.assertEqual(code, 0)
                    self.assertEqual(self.decode(fmt, output), [])
                    self.assertEqual(errors, "")
                    self.tracking.state_service.mark_case_read.assert_not_called()
                    self.tracking.get_case_details.assert_not_called()

    def test_updates_records_and_mark_read(self):
        tracked = TrackedCase("123")
        tracked.latest_known.unread_comment_count = 1
        self.tracking.state_service.get_tracked_cases.return_value = {"123": tracked}
        for fmt in ("json", "yaml"):
            with self.subTest(fmt=fmt):
                self.tracking.state_service.mark_case_read.reset_mock()
                code, output, _ = self.invoke(
                    cmd_updates, self.args(fmt), self.client, self.config
                )
                self.assertEqual(code, 0)
                self.assertEqual(self.decode(fmt, output)[0]["case_number"], "123")
                self.tracking.state_service.mark_case_read.assert_called_once_with(
                    "test-user", "123"
                )

    def test_tracking_read_failure_does_not_emit_or_acknowledge(self):
        self.tracking.state_service.get_tracked_cases.side_effect = ReadError(
            "Unavailable"
        )
        with (
            contextlib.redirect_stdout(io.StringIO()) as output,
            self.assertRaises(ReadError),
        ):
            cmd_updates(self.args(), self.client, self.config)
        self.assertEqual(output.getvalue(), "")
        self.tracking.state_service.mark_case_read.assert_not_called()

    def test_full_comment_failure_does_not_acknowledge(self):
        tracked = TrackedCase("123")
        tracked.latest_known.unread_comment_count = 1
        self.tracking.state_service.get_tracked_cases.return_value = {"123": tracked}
        self.tracking.get_case_details.side_effect = ReadError("Unavailable")
        code, _, errors = self.invoke(
            cmd_updates, self.args("text", full=True), self.client, self.config
        )
        self.assertNotEqual(code, 0)
        self.assertIn("checkpoints not advanced", errors)
        self.tracking.state_service.mark_case_read.assert_not_called()

    def test_empty_updates_keep_human_messages(self):
        for tracked, target, message in (
            ({}, None, "No cases currently tracked."),
            ({"123": TrackedCase("123")}, "unknown", "not currently tracked"),
            ({"123": TrackedCase("123")}, "123", "No unread updates"),
        ):
            with self.subTest(target=target):
                self.tracking.state_service.get_tracked_cases.return_value = tracked
                code, output, errors = self.invoke(
                    cmd_updates,
                    self.args("text", case=target),
                    self.client,
                    self.config,
                )
                self.assertEqual(code, 0)
                self.assertIn(message, output)
                self.assertEqual(errors, "")
                self.tracking.state_service.mark_case_read.assert_not_called()

    def test_apply_noop_dry_run_and_success(self):
        for fmt in ("json", "yaml", "text"):
            for summary, dry_run, applied in (
                ("old", False, False),
                ("new {{ label }}", True, False),
                ("new {{ label }}", False, True),
            ):
                with self.subTest(fmt=fmt, summary=summary, dry_run=dry_run):
                    (self.root / "policy.yaml").write_text(
                        yaml.safe_dump({"summary": summary}), encoding="utf-8"
                    )
                    self.apply_template.update_case.reset_mock()
                    code, output, _ = self.invoke(
                        cmd_apply,
                        self.args(
                            fmt,
                            json=fmt == "text",
                            dry_run=dry_run,
                            template_var=["label=value"],
                        ),
                        self.client,
                        self.config,
                    )
                    self.assertEqual(code, 0)
                    self.assertEqual(
                        self.decode(fmt, output),
                        {
                            "case_number": "123",
                            "dry_run": dry_run,
                            "changes": {}
                            if summary == "old"
                            else {"summary": {"from": "old", "to": "new value"}},
                            "applied": applied,
                        },
                    )
                    if applied:
                        self.apply_template.update_case.assert_called_once_with(
                            "123", summary="new value"
                        )
                    else:
                        self.apply_template.update_case.assert_not_called()

    def test_apply_failures_leave_stdout_empty_and_errors_safe(self):
        for fmt in ("json", "yaml"):
            for stage in ("read", "render", "update", "unsupported"):
                with self.subTest(fmt=fmt, stage=stage):
                    (self.root / "policy.yaml").write_text(
                        "summary: [SECRET: broken\n"
                        if stage == "render"
                        else "watchers: [SECRET]\n"
                        if stage == "unsupported"
                        else "summary: new\n",
                        encoding="utf-8",
                    )
                    self.apply_template.get_case_details.side_effect = (
                        ReadError("Case details unavailable")
                        if stage == "read"
                        else None
                    )
                    self.apply_template.update_case.reset_mock()
                    self.apply_template.update_case.side_effect = (
                        RuntimeError("SECRET") if stage == "update" else None
                    )
                    code, output, errors = self.invoke(
                        cmd_apply, self.args(fmt), self.client, self.config
                    )
                    self.assertNotEqual(code, 0)
                    self.assertEqual(output, "")
                    self.assertNotIn("SECRET", errors)
                    if stage != "update":
                        self.apply_template.update_case.assert_not_called()

    def test_template_metadata_warnings_skip_bad_entries(self):
        bad_templates = (
            "summary: [SECRET: broken\n",
            "- SECRET\n",
            "false\n",
            "1: SECRET\n",
            "description: {nested: SECRET}\n",
            "include_templates: [ {nested: SECRET} ]\n",
            "include_templates: 12\n",
            "summary: &recursive [*recursive]\n",
            "severity: .nan\n",
            "severity: .inf\n",
        )
        for index, text in enumerate(bad_templates):
            (self.root / f"bad{index}.yaml").write_text(text, encoding="utf-8")
        (self.root / "good.yaml").write_text(
            "_desc: Good template\nsummary: new\ninclude_templates: base\n",
            encoding="utf-8",
        )
        for fmt in ("json", "yaml", "text"):
            with self.subTest(fmt=fmt):
                code, output, errors = self.invoke(
                    cmd_list_templates, self.args(fmt), self.config
                )
                self.assertEqual(code, 0)
                self.assertNotIn("SECRET", errors)
                self.assertEqual(errors.count("Warning:"), len(bad_templates))
                if fmt != "text":
                    records = self.decode(fmt, output)
                    self.assertEqual([record["name"] for record in records], ["good"])
                    self.assertEqual(records[0]["includes"], ["base"])
                else:
                    self.assertIn("Good template", output)
                self.assertNotIn("SECRET", output)

    def test_empty_template_directories_are_structured(self):
        for fmt in ("json", "yaml"):
            for path in (self.root, self.root / "missing"):
                with self.subTest(fmt=fmt, path=path):
                    code, output, errors = self.invoke(
                        cmd_list_templates,
                        self.args(fmt),
                        {"templates_dir": str(path)},
                    )
                    self.assertEqual(code, 0)
                    self.assertEqual(self.decode(fmt, output), [])
                    self.assertEqual(errors, "")

    def test_checkpoint_failure_does_not_emit_success(self):
        tracked = TrackedCase("123")
        tracked.latest_known.unread_comment_count = 1
        self.tracking.state_service.get_tracked_cases.return_value = {"123": tracked}
        self.tracking.state_service.mark_case_read.side_effect = OSError("unavailable")
        with (
            contextlib.redirect_stdout(io.StringIO()) as output,
            self.assertRaises(OSError),
        ):
            cmd_updates(self.args(), self.client, self.config)
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
