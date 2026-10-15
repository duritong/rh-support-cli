"""Offline boundaries for reusable case creation and attachment outcomes."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

from rh_support_lib.operations import (
    CaseCreationOutcomeUnknownError,
    PartialCaseCreationError,
    create_case_with_attachments,
)


class CreationOperationsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.paths = [Path(directory.name) / f"source-{i}.txt" for i in range(3)]
        for path in self.paths:
            path.write_text("diagnostics", encoding="utf-8")
        self.cases = Mock()
        self.cases.create_case.return_value = "123456"
        self.attachments = Mock()
        self.attachments.upload_attachment.return_value = self.completed("att-1")
        self.fields = {"summary": "private summary", "description": "private body"}

    @staticmethod
    def completed(identifier):
        return SimpleNamespace(uuid=identifier, upload_status="Completed")

    def create(self, files=None):
        return create_case_with_attachments(
            self.cases, self.attachments, self.fields, files=files
        )

    def test_success_receipt_and_pathlike_conversion(self):
        self.attachments.upload_attachment.side_effect = [
            self.completed("att-1"),
            self.completed("att-2"),
            self.completed("att-3"),
        ]
        receipt = self.create(iter(self.paths))
        self.assertEqual(
            receipt,
            {
                "case_number": "123456",
                "created": True,
                "success": True,
                "attachments": [
                    {
                        "file": str(path),
                        "uploaded": True,
                        "attachment_id": f"att-{i}",
                        "error": None,
                    }
                    for i, path in enumerate(self.paths, 1)
                ],
            },
        )
        self.cases.create_case.assert_called_once_with(**self.fields)
        self.attachments.upload_attachment.assert_has_calls(
            [call("123456", str(path)) for path in self.paths]
        )
        self.assertEqual(self.attachments.upload_attachment.call_count, 3)

    def test_no_attachments(self):
        for files in (None, [], ()):
            with self.subTest(files=files):
                self.cases.reset_mock()
                self.assertEqual(
                    self.create(files),
                    {
                        "case_number": "123456",
                        "created": True,
                        "success": True,
                        "attachments": [],
                    },
                )
                self.cases.create_case.assert_called_once_with(**self.fields)
                self.attachments.upload_attachment.assert_not_called()

    def test_preflight_rejects_nonfiles_before_creation(self):
        missing = self.paths[0].parent / "missing"
        for files in (
            [missing],
            [self.paths[0], missing],
            [missing, missing.with_name("also-missing")],
            [self.paths[0].parent],
            [None],
            [42],
            ["bad\x00path"],
            str(self.paths[0]),
            self.paths[0],
        ):
            with self.subTest(files=files), self.assertRaises(ValueError):
                self.create(files)
            self.cases.create_case.assert_not_called()
            self.attachments.upload_attachment.assert_not_called()

    def test_bytes_path_is_normalized_to_string(self):
        receipt = self.create([os.fsencode(self.paths[0])])
        self.assertEqual(receipt["attachments"][0]["file"], str(self.paths[0]))
        self.attachments.upload_attachment.assert_called_once_with(
            "123456", str(self.paths[0])
        )

    def test_partial_failure_attempts_every_file_once_without_sensitive_output(self):
        secret = "Bearer credential https://storage.invalid/?secret=token private body"
        self.attachments.upload_attachment.side_effect = [
            RuntimeError(secret),
            self.completed("att-2"),
            OSError(secret),
        ]
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(PartialCaseCreationError) as caught,
        ):
            self.create(self.paths)
        error = caught.exception
        receipt = error.receipt
        self.assertEqual(receipt["case_number"], "123456")
        self.assertTrue(receipt["created"])
        self.assertFalse(receipt["success"])
        self.assertEqual(
            [item["uploaded"] for item in receipt["attachments"]], [False, True, False]
        )
        self.assertIsNone(receipt["attachments"][0]["attachment_id"])
        self.assertIsNotNone(receipt["attachments"][0]["error"])
        self.assertIn("123456", str(error))
        self.assertIn("Do not repeat", str(error))
        serialized = json.dumps(receipt) + str(error)
        for value in (
            secret,
            "credential",
            "https://",
            "private body",
            "private summary",
        ):
            self.assertNotIn(value, serialized)
        self.assertIsNone(error.__cause__)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(
            self.attachments.upload_attachment.call_args_list,
            [call("123456", str(path)) for path in self.paths],
        )
        self.cases.create_case.assert_called_once_with(**self.fields)

    def test_unconfirmed_upload_is_not_reported_as_uploaded(self):
        for result in (None, SimpleNamespace(uuid="att-1", upload_status="Pending")):
            with self.subTest(result=result):
                self.attachments.upload_attachment.return_value = result
                with self.assertRaises(PartialCaseCreationError) as caught:
                    self.create([self.paths[0]])
                item = caught.exception.receipt["attachments"][0]
                self.assertFalse(item["uploaded"])
                self.assertIsNone(item["attachment_id"])
                self.assertIsNotNone(item["error"])

    def test_creation_failure_propagates_without_retry_or_upload(self):
        error = RuntimeError("creation failed")
        self.cases.create_case.side_effect = error
        with self.assertRaises(RuntimeError) as caught:
            self.create(self.paths)
        self.assertIs(caught.exception, error)
        self.assertFalse(hasattr(error, "receipt"))
        self.cases.create_case.assert_called_once_with(**self.fields)
        self.attachments.upload_attachment.assert_not_called()

    def test_invalid_case_number_is_unknown_and_never_uploads(self):
        for number in (None, "", " \t\n", 123456, {}, True):
            with self.subTest(number=number):
                self.cases.reset_mock()
                self.cases.create_case.return_value = number
                with self.assertRaises(CaseCreationOutcomeUnknownError) as caught:
                    self.create(self.paths)
                self.assertIn("portal", str(caught.exception).lower())
                self.assertIn("before retrying", str(caught.exception))
                self.assertFalse(hasattr(caught.exception, "receipt"))
                self.cases.create_case.assert_called_once_with(**self.fields)
                self.attachments.upload_attachment.assert_not_called()

    def test_cancellation_stops_uploads_and_preserves_all_outcomes(self):
        self.attachments.upload_attachment.side_effect = [
            self.completed("att-1"),
            KeyboardInterrupt("secret credential"),
            self.completed("must-not-upload"),
        ]
        with self.assertRaises(PartialCaseCreationError) as caught:
            self.create(self.paths)
        receipt = caught.exception.receipt
        self.assertEqual(receipt["case_number"], "123456")
        self.assertTrue(receipt["created"])
        self.assertFalse(receipt["success"])
        self.assertEqual(len(receipt["attachments"]), 3)
        first, current, remaining = receipt["attachments"]
        self.assertTrue(first["uploaded"])
        self.assertFalse(current["uploaded"])
        self.assertIn("canceled", current["error"].lower())
        self.assertFalse(remaining["uploaded"])
        self.assertIn("not attempted", remaining["error"].lower())
        self.assertIsNone(current["attachment_id"])
        self.assertIsNone(remaining["attachment_id"])
        self.assertNotIn("secret", json.dumps(receipt) + str(caught.exception))
        self.assertEqual(self.attachments.upload_attachment.call_count, 2)
        self.cases.create_case.assert_called_once_with(**self.fields)


if __name__ == "__main__":
    unittest.main()
