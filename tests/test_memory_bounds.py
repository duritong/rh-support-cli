"""Memory boundary verification tests for large sparse-file streaming and multipart upload transfers."""

from __future__ import annotations

import http.server
import json
import os
import resource
import tempfile
import threading
import tracemalloc
import unittest
from unittest.mock import Mock

from rh_support_lib.api import enable_debug_logging
from rh_support_lib.services.attachments import AttachmentService, FilePartStream


class _DiscardingServerHandler(http.server.BaseHTTPRequestHandler):
    """Loopback HTTP server that discards streaming PUT bodies without buffering into memory."""

    def log_message(self, format, *args):
        pass

    def do_PUT(self):
        content_length = int(self.headers.get("Content-Length", 0))
        # Drain body in small blocks without keeping in memory
        remaining = content_length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 64 * 1024))
            if not chunk:
                break
            remaining -= len(chunk)
        self.send_response(200)
        self.send_header("ETag", '"mock_etag_12345"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "uploadId": "test_up_id",
                    "attachmentId": "test_att_id",
                    "uploadStrategy": "MULTIPART",
                    "parts": [
                        {
                            "partNumber": 1,
                            "presignedUrl": f"http://127.0.0.1:{self.server.server_port}/part1",
                        }
                    ],
                }
            ).encode()
        )


class MemoryBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _DiscardingServerHandler)
        cls.server_port = cls.server.server_port
        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="rh-mem-test-")
        self.addCleanup(self.temp_dir.cleanup)
        # Create 6 GiB sparse file (takes <10 KiB real disk blocks on any Unix filesystem)
        self.sparse_path = os.path.join(self.temp_dir.name, "sparse_6gb.bin")
        self.file_size = 6 * 1024 * 1024 * 1024  # 6 GiB
        with open(self.sparse_path, "wb") as f:
            f.truncate(self.file_size)

    def test_file_part_stream_memory_stays_bounded(self):
        """Verify that streaming a 5 GiB slice through FilePartStream allocates <25 MB heap RAM."""
        slice_size = 5 * 1024 * 1024 * 1024  # 5 GiB
        stream = FilePartStream(
            self.sparse_path,
            start_offset=0,
            length=slice_size,
            block_size=8 * 1024 * 1024,
        )

        tracemalloc.start()
        bytes_read = 0
        try:
            while True:
                chunk = stream.read(8 * 1024 * 1024)
                if not chunk:
                    break
                bytes_read += len(chunk)
                # Ensure no individual read ever exceeds block size
                self.assertLessEqual(len(chunk), 8 * 1024 * 1024)
            stream.verify_complete()

            _current, peak = tracemalloc.get_traced_memory()
            # Peak heap allocation must stay strictly bounded to chunk size (<25 MB)
            self.assertLess(
                peak,
                25 * 1024 * 1024,
                f"Peak memory during 5 GiB streaming exceeded 25 MB: {peak / 1024 / 1024:.2f} MB",
            )
            self.assertEqual(bytes_read, slice_size)
        finally:
            tracemalloc.stop()

    def test_multipart_loopback_streaming_transfer_memory_and_logging_bounds(self):
        """Verify that streaming transfer to loopback HTTP server with debug logging stays bounded <35 MB."""
        debug_log = os.path.join(self.temp_dir.name, "debug.log")
        enable_debug_logging(log_file=debug_log)

        api_client = Mock()
        api_client.post.return_value = Mock(
            json=lambda: {
                "uploadId": "test_up_id",
                "attachmentId": "test_att_id",
                "uploadStrategy": "MULTIPART",
                "totalChunks": 1,
                "parts": [
                    {
                        "partNumber": 1,
                        "presignedUrl": f"http://127.0.0.1:{self.server_port}/part1",
                    }
                ],
            },
            status_code=200,
            raise_for_status=lambda: None,
        )
        api_client.get.return_value = Mock(
            json=list,
            status_code=200,
            raise_for_status=lambda: None,
        )

        service = AttachmentService(api_client)
        # Use 1 GB threshold so the 6 GiB file triggers multipart
        service.MULTIPART_THRESHOLD = 1024 * 1024 * 1024

        tracemalloc.start()
        initial_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        try:
            # Stream a 500 MiB slice through FilePartStream to loopback server
            slice_size = 500 * 1024 * 1024
            stream = FilePartStream(
                self.sparse_path,
                start_offset=0,
                length=slice_size,
                block_size=8 * 1024 * 1024,
            )

            import requests

            resp = requests.put(
                f"http://127.0.0.1:{self.server_port}/part1",
                data=stream,
                headers={"Content-Length": str(slice_size)},
                timeout=10,
            )
            self.assertEqual(resp.status_code, 200)

            _current, peak = tracemalloc.get_traced_memory()
            final_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            self.assertGreaterEqual(final_rss, initial_rss)
            self.assertLess(
                peak,
                35 * 1024 * 1024,
                f"Peak memory during loopback upload exceeded 35 MB: {peak / 1024 / 1024:.2f} MB",
            )
            # Verify debug log did not capture the binary body
            if os.path.isfile(debug_log):
                self.assertLess(
                    os.path.getsize(debug_log),
                    1024 * 1024,
                    "Debug log file must not buffer binary request streams",
                )
        finally:
            tracemalloc.stop()
