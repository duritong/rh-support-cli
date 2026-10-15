"""Offline upload integrity regressions with reconstructing fake storage."""

import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from rh_support_lib.services.attachments import AttachmentService, FilePartStream


class UploadIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "source.bin")
        self.client = Mock()
        self.service = AttachmentService(self.client, base_url="https://api.invalid")
        self.service.MULTIPART_THRESHOLD = 8
        self.stored = {}
        self.etag = '"storage-etag"'
        self.read_limit = None
        self.progress = []
        put_patch = patch(
            "rh_support_lib.services.attachments.requests.put",
            side_effect=self.store,
        )
        self.put = put_patch.start()
        self.addCleanup(put_patch.stop)

    def write_source(self, size):
        content = bytes((index * 37 + 11) % 256 for index in range(size))
        with open(self.path, "wb") as output:
            output.write(content)
        return content

    def manifest(self, numbers):
        return {
            "attachmentId": "att_test",
            "uploadStrategy": "MULTIPART",
            "uploadId": "upload-test",
            "parts": [
                {
                    "partNumber": number,
                    "presignedUrl": f"https://store.invalid/{number}",
                }
                for number in numbers
            ],
        }

    def initiate(self, data):
        self.client.post.return_value.json.return_value = data

    def store(self, url, *, data, headers, timeout):
        chunks = []
        count = 0
        while self.read_limit is None or count < self.read_limit:
            chunk = data.read(3)
            if not chunk:
                break
            chunks.append(chunk)
            count += 1
        self.stored[url] = b"".join(chunks)
        if self.read_limit is None:
            self.assertEqual(len(self.stored[url]), int(headers["Content-Length"]))
        return Mock(headers={} if self.etag is None else {"ETag": self.etag})

    def upload(self):
        return self.service.upload_attachment(
            "123", self.path, progress_callback=lambda *args: self.progress.append(args)
        )

    def assert_not_completed(self):
        self.assertFalse(
            any(
                call.args[0].endswith("/complete")
                for call in self.client.post.call_args_list
            )
        )

    def assert_aborted(self):
        self.client.delete.assert_called_once_with(
            "https://api.invalid/v3/cases/attachments/upload/abort",
            json={
                "attachmentId": "att_test",
                "caseNumber": "123",
                "uploadId": "upload-test",
            },
        )

    def test_multipart_reconstructs_original_including_shuffled_manifest(self):
        for size, numbers in (
            (1, [1]),
            (5, [3, 1, 2]),
            (7, [2, 1]),
            (8, [1]),
            (9, [2, 1]),
            (16, [2, 1]),
            (17, [3, 1, 2]),
            (33, [5, 3, 1, 4, 2]),
        ):
            with self.subTest(size=size, numbers=numbers):
                content = self.write_source(size)
                self.initiate(self.manifest(numbers))
                self.stored.clear()
                self.client.reset_mock()
                attachment = self.upload()
                reconstructed = b"".join(
                    self.stored[f"https://store.invalid/{number}"]
                    for number in sorted(numbers)
                )
                self.assertEqual(reconstructed, content)
                self.assertEqual(attachment.upload_status, "Completed")
                completed = self.client.post.call_args.kwargs["json"]["parts"]
                self.assertEqual(
                    [part["partNumber"] for part in completed], sorted(numbers)
                )
                self.assertTrue(
                    all(part["eTag"] == "storage-etag" for part in completed)
                )
                self.assertEqual(self.progress[-1], (size, size))
                self.client.delete.assert_not_called()

    def test_single_put_preserves_bytes_and_threshold_request(self):
        for size in (0, 1, 7, 8, 9):
            with self.subTest(size=size):
                content = self.write_source(size)
                self.initiate(
                    {
                        "attachmentId": "att_test",
                        "uploadStrategy": "SINGLE_PUT",
                        "presignedUrl": "https://store.invalid/single",
                    }
                )
                self.upload()
                self.assertEqual(self.stored["https://store.invalid/single"], content)
                payload = self.client.post.call_args.kwargs["json"]
                self.assertEqual(payload.get("totalChunks"), 2 if size > 8 else None)

    def test_unknown_null_and_missing_strategy_never_succeed(self):
        self.write_source(5)
        for strategy in ("UNKNOWN", None, "", 42):
            with self.subTest(strategy=strategy):
                self.initiate({"attachmentId": "att_test", "uploadStrategy": strategy})
                with self.assertRaises(RuntimeError):
                    self.upload()
        self.initiate(
            {"attachmentId": "att_test", "presignedUrl": "https://store.invalid/single"}
        )
        with self.assertRaises(RuntimeError):
            self.upload()
        self.put.assert_not_called()
        self.assert_not_completed()

    def test_invalid_initiation_shape_rejected_before_put(self):
        self.write_source(5)
        for data in (None, [], "invalid"):
            with self.subTest(data=data):
                self.initiate(data)
                with self.assertRaises(TypeError):
                    self.upload()
        self.put.assert_not_called()
        self.assert_not_completed()

    def test_missing_upload_id_rejected_without_unusable_abort(self):
        self.write_source(9)
        for identifier in (None, "", " ", 7):
            with self.subTest(identifier=identifier):
                data = self.manifest([1, 2])
                data["uploadId"] = identifier
                self.initiate(data)
                with self.assertRaises(RuntimeError):
                    self.upload()
        self.put.assert_not_called()
        self.assert_not_completed()
        self.client.delete.assert_not_called()

    def test_missing_attachment_id_rejected_before_put(self):
        self.write_source(5)
        for strategy in ("SINGLE_PUT", "MULTIPART"):
            for identifier in (None, "", "   ", 12):
                with self.subTest(strategy=strategy, identifier=identifier):
                    data = self.manifest([1])
                    data.update(
                        attachmentId=identifier,
                        uploadStrategy=strategy,
                        presignedUrl="https://store.invalid/single",
                    )
                    self.initiate(data)
                    with self.assertRaises(RuntimeError):
                        self.upload()
        self.put.assert_not_called()
        self.assert_not_completed()

    def test_invalid_manifest_rejected_before_any_put_and_aborted(self):
        cases = [
            (17, [1, 2]),
            (17, [1, 2, 3, 4]),
            (17, [1]),
            (9, [1, 1]),
            (9, [1, 3]),
            (9, [0, 1]),
            (9, [-1, 1]),
            (9, [True, 2]),
            (9, [1.0, 2]),
            (9, ["1", 2]),
            (9, [None, 2]),
            (2, [1, 2, 3]),
            (4, [1, 2, 3]),
            (0, [1]),
        ]
        manifests = [(size, self.manifest(numbers)) for size, numbers in cases]
        for parts in (
            [],
            None,
            {},
            [None],
            [{"presignedUrl": "https://store.invalid/1"}],
        ):
            data = self.manifest([1])
            data["parts"] = parts
            manifests.append((5, data))
        for url in (None, "", " ", 7):
            data = self.manifest([1, 2])
            data["parts"][1]["presignedUrl"] = url
            manifests.append((9, data))
        for size, data in manifests:
            with self.subTest(size=size, data=data):
                self.write_source(size)
                self.initiate(data)
                self.client.reset_mock()
                self.put.reset_mock()
                with self.assertRaises(RuntimeError):
                    self.upload()
                self.put.assert_not_called()
                self.assert_not_completed()
                self.assert_aborted()

    def test_missing_etag_aborts_without_completion(self):
        self.write_source(9)
        self.initiate(self.manifest([1, 2]))
        for etag in (None, "", '""', "  "):
            with self.subTest(etag=etag):
                self.etag = etag
                self.client.reset_mock()
                with self.assertRaises(RuntimeError):
                    self.upload()
                self.assert_not_completed()
                self.assert_aborted()

    def test_successful_put_that_does_not_consume_body_cannot_complete(self):
        for strategy in ("SINGLE_PUT", "MULTIPART"):
            with self.subTest(strategy=strategy):
                self.write_source(17)
                data = self.manifest([1, 2, 3])
                data.update(
                    uploadStrategy=strategy, presignedUrl="https://store.invalid/single"
                )
                self.initiate(data)
                self.client.reset_mock()
                self.read_limit = 1
                with self.assertRaises((OSError, RuntimeError)):
                    self.upload()
                self.assert_not_completed()
                if strategy == "MULTIPART":
                    self.assert_aborted()

    def test_source_size_changes_after_initiation_cannot_complete(self):
        for strategy in ("SINGLE_PUT", "MULTIPART"):
            for actual_size in (5, 20):
                with self.subTest(strategy=strategy, actual_size=actual_size):
                    self.write_source(17)
                    data = self.manifest([1, 2, 3])
                    data.update(
                        uploadStrategy=strategy,
                        presignedUrl="https://store.invalid/single",
                    )

                    def initiate(*args, size=actual_size, response=data, **kwargs):
                        self.write_source(size)
                        return Mock(json=lambda: response)

                    self.client.reset_mock()
                    self.client.post.side_effect = initiate
                    # Let the service, not the fake storage, detect premature EOF.
                    self.read_limit = 100
                    with self.assertRaises((OSError, RuntimeError)):
                        self.upload()
                    self.assert_not_completed()
                    if strategy == "MULTIPART":
                        self.assert_aborted()

    def test_put_failure_and_interrupt_abort_preserving_original_error(self):
        self.write_source(9)
        self.initiate(self.manifest([1, 2]))
        for error in (requests.RequestException("PUT failed"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.client.reset_mock()
                self.client.delete.side_effect = RuntimeError("abort failed")
                self.put.side_effect = error
                with self.assertRaises(type(error)):
                    self.upload()
                self.assert_not_completed()
                self.assert_aborted()

    def test_real_five_gib_chunk_boundaries_without_large_allocations(self):
        threshold = AttachmentService.MULTIPART_THRESHOLD
        self.assertEqual(threshold, 5 * 1024**3)
        self.service.MULTIPART_THRESHOLD = threshold
        self.write_source(0)
        self.put.side_effect = None
        self.put.return_value = Mock(headers={"ETag": self.etag})
        for size in (threshold + 1, threshold * 2, threshold * 2 + 1):
            with self.subTest(size=size):
                count = (size + threshold - 1) // threshold
                self.initiate(self.manifest(list(range(count, 0, -1))))
                with (
                    patch(
                        "rh_support_lib.services.attachments.os.path.getsize",
                        return_value=size,
                    ),
                    patch(
                        "rh_support_lib.services.attachments.FilePartStream"
                    ) as stream_type,
                ):
                    self.upload()
                slices = [
                    (call.kwargs["start_offset"], call.kwargs["length"])
                    for call in stream_type.call_args_list
                ]
                self.assertEqual(
                    slices,
                    [
                        (index * threshold, min(threshold, size - index * threshold))
                        for index in range(count)
                    ],
                )
                self.assertEqual(sum(length for _, length in slices), size)


class FilePartStreamTests(unittest.TestCase):
    def test_requests_prepares_stream_without_materializing_body(self):
        with tempfile.NamedTemporaryFile() as source:
            content = bytes(range(100))
            source.write(content)
            source.flush()
            with FilePartStream(source.name, 0, len(content), block_size=8) as stream:
                prepared = requests.Request(
                    "PUT", "https://store.invalid/1", data=stream
                ).prepare()
                self.assertIs(prepared.body, stream)
                self.assertEqual(prepared.headers["Content-Length"], "100")
                chunks = []
                while True:
                    chunk = prepared.body.read(16384)
                    if not chunk:
                        break
                    self.assertLessEqual(len(chunk), 8)
                    chunks.append(chunk)
                self.assertEqual(b"".join(chunks), content)
                stream.verify_complete(check_eof=True)

    def test_positive_reads_bounded_but_read_all_contract_preserved(self):
        with tempfile.NamedTemporaryFile() as source:
            content = bytes(range(100))
            source.write(content)
            source.flush()
            with FilePartStream(source.name, 5, 70, block_size=8) as stream:
                self.assertEqual(stream.read(50), content[5:13])
                self.assertEqual(stream.read(0), b"")
                self.assertEqual(stream.read(), content[13:75])
                self.assertEqual(stream.read(), b"")
