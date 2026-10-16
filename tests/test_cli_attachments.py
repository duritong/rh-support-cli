"""Focused tests for case attachment management and file streaming."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tests.support.mock_server import MockRedHatHandler, MockServerTestCase


class TestCliAttachments(MockServerTestCase):
    def test_attach_file(self):
        """Test attaching a file to a case"""
        # Create a dummy file
        with open("test_attachment.txt", "w") as f:
            f.write("This is a test attachment.")

        try:
            result = self.run_cli(
                ["attach", "-c", "01234567", "-f", "test_attachment.txt"]
            )
            if result.returncode != 0:
                print("\nSTDOUT:", result.stdout)
                print("STDERR:", result.stderr)

            self.assertEqual(result.returncode, 0)
            self.assertIn(
                "Success: File 'test_attachment.txt' attached.", result.stdout
            )
        finally:
            if os.path.exists("test_attachment.txt"):
                os.remove("test_attachment.txt")

    def test_attach_nonexistent_file(self):
        """Test attaching a file that does not exist"""
        result = self.run_cli(
            ["attach", "-c", "01234567", "-f", "nonexistent_file.txt"]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Error: File 'nonexistent_file.txt' does not exist.", result.stdout
        )

    def test_attachment_subcommands_list_and_status(self):
        """Test attachment list and status subcommands via CLI"""
        # Rich list
        result_list = self.run_cli(["attachment", "-c", "12345", "list"])
        self.assertEqual(result_list.returncode, 0)
        self.assertIn("Attachments for Case #12345", result_list.stdout)
        self.assertIn("att_1001_uuid", result_list.stdout)

        # Simple output list
        result_simple = self.run_cli(
            ["--simple-output", "attachment", "-c", "12345", "list"]
        )
        self.assertEqual(result_simple.returncode, 0)
        self.assertIn("Attachments for Case #12345:", result_simple.stdout)
        self.assertIn("test_attachment.log", result_simple.stdout)

        # Status
        result_status = self.run_cli(["attachment", "-c", "12345", "status"])
        self.assertEqual(result_status.returncode, 0)
        self.assertIn("test_attachment.log", result_status.stdout)
        self.assertIn("COMPLETED", result_status.stdout)

        # Calling attachments command without action defaults to list
        result_default_list = self.run_cli(["attachments", "-c", "12345"])
        self.assertEqual(result_default_list.returncode, 0)
        self.assertIn("att_1001_uuid", result_default_list.stdout)

        # Top-level list-attachments shortcut
        result_shortcut = self.run_cli(["list-attachments", "12345"])
        self.assertEqual(result_shortcut.returncode, 0)
        self.assertIn("att_1001_uuid", result_shortcut.stdout)

        # Verify 'show' command displays the attachments section with UUID and filename
        result_show = self.run_cli(["show", "-c", "12345", "--no-pager"])
        self.assertEqual(result_show.returncode, 0)
        self.assertIn("ATTACHMENTS", result_show.stdout)
        self.assertIn("att_1001_uuid", result_show.stdout)
        self.assertIn("test_attachment.log", result_show.stdout)

    def test_attachment_upload_download_delete_cli(self):
        """Test uploading, downloading, and deleting an attachment via CLI"""

        temp_dir = tempfile.mkdtemp()
        try:
            sample_file = os.path.join(temp_dir, "test_diag.log")
            with open(sample_file, "w") as f:
                f.write("System diagnostic log line 1\nLine 2")
            # 1. Upload with -c before upload action
            result_up1 = self.run_cli(
                ["--graphql", "attachment", "-c", "12345", "upload", "-f", sample_file]
            )
            self.assertEqual(result_up1.returncode, 0)
            self.assertIn("Success: File 'test_diag.log' attached.", result_up1.stdout)

            # 2. Upload with -c after upload action
            result_up2 = self.run_cli(
                ["--graphql", "attachment", "upload", "-c", "12345", "-f", sample_file]
            )
            self.assertEqual(result_up2.returncode, 0)
            self.assertIn("Success: File 'test_diag.log' attached.", result_up2.stdout)

            # 3. Upload with positional case after upload action
            result_up3 = self.run_cli(
                ["--graphql", "attachment", "upload", "12345", "-f", sample_file]
            )
            self.assertEqual(result_up3.returncode, 0)
            self.assertIn("Success: File 'test_diag.log' attached.", result_up3.stdout)
            # Download
            dest_file = os.path.join(temp_dir, "downloaded.log")
            result_down = self.run_cli(
                [
                    "--graphql",
                    "attachment",
                    "-c",
                    "12345",
                    "download",
                    "-a",
                    "att_test_uuid_123",
                    "-o",
                    dest_file,
                ]
            )
            self.assertEqual(result_down.returncode, 0)
            self.assertIn("Successfully downloaded attachment to:", result_down.stdout)
            self.assertTrue(os.path.exists(dest_file))
            with open(dest_file, "r") as f:
                self.assertEqual(f.read(), "System diagnostic log line 1\nLine 2")

            # Download via top-level shortcut
            dest_shortcut = os.path.join(temp_dir, "downloaded_shortcut.log")
            res_shortcut = self.run_cli(
                [
                    "download",
                    "-c",
                    "12345",
                    "-a",
                    "att_test_uuid_123",
                    "-o",
                    dest_shortcut,
                ]
            )
            self.assertEqual(res_shortcut.returncode, 0)
            self.assertTrue(os.path.exists(dest_shortcut))
            with open(dest_shortcut, "r") as f:
                self.assertEqual(f.read(), "System diagnostic log line 1\nLine 2")

            # Delete with -y
            result_del = self.run_cli(
                [
                    "--graphql",
                    "attachment",
                    "-c",
                    "12345",
                    "delete",
                    "-a",
                    "att_test_uuid_123",
                    "-y",
                ]
            )
            self.assertEqual(result_del.returncode, 0)
            self.assertIn(
                "Successfully deleted attachment 'att_test_uuid_123'.",
                result_del.stdout,
            )

        finally:
            shutil.rmtree(temp_dir)

    def test_json_attachment_list_output(self):
        """Test listing attachments in machine-readable JSON format."""

        res_json = self.run_cli(["list-attachments", "12345", "--json"])
        self.assertEqual(res_json.returncode, 0)
        self.assertNotIn("Authenticating...", res_json.stdout)
        data = json.loads(res_json.stdout)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        self.assertIn("uuid", data[0])
        self.assertIn("file_name", data[0])

    def test_file_part_stream_slicing_and_length(self):
        """Test FilePartStream streams exact byte slices in blocks with callbacks."""

        from rh_support_lib.services.attachments import FilePartStream

        temp_file = tempfile.NamedTemporaryFile(delete=False)  # noqa: SIM115
        content = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 100  # 3600 bytes
        temp_file.write(content)
        temp_file.close()

        try:
            progress_bytes = []

            def on_progress(n: int) -> None:
                progress_bytes.append(n)

            with FilePartStream(
                file_path=temp_file.name,
                start_offset=100,
                length=500,
                block_size=128,
                on_bytes_read=on_progress,
            ) as stream:
                self.assertEqual(len(stream), 500)
                read_data = stream.read()
                self.assertEqual(len(read_data), 500)
                self.assertEqual(read_data, content[100:600])
                self.assertEqual(sum(progress_bytes), 500)
                self.assertEqual(stream.read(), b"")
        finally:
            os.remove(temp_file.name)

    def test_attachment_multipart_streaming_upload(self):
        """Test automated multipart upload with low-memory streaming and progress tracking."""

        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.attachments import AttachmentService

        temp_file = tempfile.NamedTemporaryFile(delete=False)  # noqa: SIM115
        payload_data = b"MOCK_LARGE_MULTIPART_PAYLOAD_CHUNK_" * 100  # ~3500 bytes
        temp_file.write(payload_data)
        temp_file.close()

        client = LegacyAPIClient("mock_access_token_123")
        service = AttachmentService(client, base_url=self.base_url)
        orig_threshold = AttachmentService.MULTIPART_THRESHOLD
        AttachmentService.MULTIPART_THRESHOLD = (
            1000  # Trigger multipart on small test file
        )

        try:
            progress_reports = []

            def on_progress(uploaded: int, total: int) -> None:
                progress_reports.append((uploaded, total))

            att = service.upload_attachment(
                case_number="12345",
                file_path=temp_file.name,
                description="Automated multipart upload test",
                progress_callback=on_progress,
            )
            self.assertIsNotNone(att)
            self.assertEqual(att.uuid, "att_test_uuid_123")
            self.assertEqual(att.file_name, os.path.basename(temp_file.name))
            self.assertTrue(len(progress_reports) > 0)
            # Final report reached 100%
            self.assertEqual(progress_reports[-1][0], len(payload_data))
            self.assertEqual(progress_reports[-1][1], len(payload_data))
        finally:
            AttachmentService.MULTIPART_THRESHOLD = orig_threshold
            os.remove(temp_file.name)

    def test_attachment_multipart_abort_on_put_failure(self):
        """Test that multipart upload aborts session cleanly if a chunk PUT fails."""
        from unittest.mock import patch

        import requests

        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.attachments import AttachmentService

        temp_file = tempfile.NamedTemporaryFile(delete=False)  # noqa: SIM115
        temp_file.write(b"MOCK_DATA_" * 200)
        temp_file.close()

        client = LegacyAPIClient("mock_access_token_123")
        service = AttachmentService(client, base_url=self.base_url)
        orig_threshold = AttachmentService.MULTIPART_THRESHOLD
        AttachmentService.MULTIPART_THRESHOLD = 500

        try:
            # Simulate PUT failure on part upload
            with (
                patch(
                    "requests.put",
                    side_effect=requests.RequestException(
                        "Simulated connection timeout"
                    ),
                ),
                self.assertRaises(requests.RequestException),
            ):
                service.upload_attachment(
                    case_number="12345",
                    file_path=temp_file.name,
                )

            # Verify abort endpoint was called
            self.assertIsNotNone(
                getattr(MockRedHatHandler, "last_aborted_upload", None)
            )
            self.assertEqual(
                MockRedHatHandler.last_aborted_upload.get("uploadId"), "upload_id_123"
            )
            self.assertEqual(
                MockRedHatHandler.last_aborted_upload.get("caseNumber"), "12345"
            )
        finally:
            AttachmentService.MULTIPART_THRESHOLD = orig_threshold
            os.remove(temp_file.name)


class TestAttachmentDownloadSafety(unittest.TestCase):
    def setUp(self):
        from pathlib import Path

        from rh_support_lib.services.attachments import AttachmentService

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.target = self.root / "bundle"
        self.client = mock.Mock()
        self.client.get.return_value.json.return_value = {
            "fileName": "bundle",
            "downloadUrl": "https://storage.example/bundle",
        }
        self.service = AttachmentService(self.client)
        patcher = mock.patch("rh_support_lib.services.attachments.requests.get")
        self.get = patcher.start()
        self.addCleanup(patcher.stop)
        self.response = self.get.return_value
        self.response.headers = {"Content-Length": "6"}
        self.response.iter_content.return_value = iter([b"abc", b"def"])

    def download(self, callback=None):
        return self.service.download_attachment(
            "100", "att_123", str(self.target), callback
        )

    def test_existing_file_and_symlinks_are_never_overwritten(self):
        self.target.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            self.download()
        self.assertEqual(self.target.read_bytes(), b"original")
        self.get.assert_not_called()
        self.target.unlink()
        victim = self.root / "victim"
        victim.write_bytes(b"keep")
        self.target.symlink_to(victim)
        with self.assertRaises(FileExistsError):
            self.download()
        self.assertEqual(victim.read_bytes(), b"keep")

    def test_unsafe_remote_filenames_are_rejected(self):
        destination = self.root / "safe"
        destination.mkdir()
        for filename in (
            "../escape",
            str(self.root / "absolute"),
            "..\\escape",
            "C:escape",
            "",
            None,
        ):
            with self.subTest(filename=filename):
                self.client.get.return_value.json.return_value["fileName"] = filename
                with self.assertRaisesRegex(ValueError, "filename"):
                    self.service.download_attachment("100", "att_123", str(destination))
        self.get.assert_not_called()

    def test_download_is_invisible_until_complete_and_response_is_closed(self):
        def progress(done, total):
            self.assertFalse(self.target.exists())

        self.assertEqual(self.download(progress), str(self.target))
        self.assertEqual(self.target.read_bytes(), b"abcdef")
        self.assertEqual(list(self.root.iterdir()), [self.target])
        self.response.close.assert_called_once()

    def test_failure_or_interruption_removes_partial_file(self):
        for error in (OSError("Transfer interrupted"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.response.iter_content.side_effect = lambda **kwargs: iter([b"abc"])

                def fail(done, total, error=error):
                    raise error

                with self.assertRaises(type(error)):
                    self.download(fail)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_destination_created_during_transfer_is_preserved(self):
        def race(done, total):
            self.target.write_bytes(b"created concurrently")

        with self.assertRaises(FileExistsError):
            self.download(race)
        self.assertEqual(self.target.read_bytes(), b"created concurrently")
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_short_response_is_not_published(self):
        self.response.iter_content.return_value = iter([b"abc"])
        with self.assertRaisesRegex(OSError, "length"):
            self.download()
        self.assertEqual(list(self.root.iterdir()), [])
