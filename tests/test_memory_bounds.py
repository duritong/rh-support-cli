"""Bounded-read and real multipart service upload memory measurements."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tracemalloc
import unittest

from rh_support_lib.services.attachments import FilePartStream


class MemoryBoundsTests(unittest.TestCase):
    def test_file_part_stream_memory_stays_bounded(self):
        """Read a 256 MiB sparse slice with an 8 MiB block bound."""
        size = 256 * 1024 * 1024
        with tempfile.TemporaryDirectory(prefix="rh-stream-test-") as root:
            path = os.path.join(root, "sparse.bin")
            with open(path, "wb") as output:
                output.truncate(size)
            tracemalloc.start()
            try:
                with FilePartStream(path, 0, size) as stream:
                    bytes_read = 0
                    while chunk := stream.read(8 * 1024 * 1024):
                        self.assertLessEqual(len(chunk), 8 * 1024 * 1024)
                        bytes_read += len(chunk)
                    stream.verify_complete(check_eof=True)
                _, peak = tracemalloc.get_traced_memory()
                self.assertEqual(bytes_read, size)
                self.assertLess(peak, 25 * 1024 * 1024)
            finally:
                tracemalloc.stop()

    def test_multipart_loopback_streaming_transfer_memory_and_logging_bounds(self):
        """Measure a real service upload in a fresh, inherited-guard process."""
        result = subprocess.run(
            [sys.executable, "-B", "-m", "tests.support.upload_memory_probe"],
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        metrics = json.loads(result.stdout)
        self.assertEqual(metrics["parts"], 4)
        self.assertEqual(metrics["completion_attempts"], 2)
        # Both bounds are smaller than the 96 MiB input; heap is also below one part.
        self.assertLess(metrics["peak_heap"], 16 * 1024 * 1024, metrics)
        if metrics["peak_rss"] is not None:
            self.assertLess(
                metrics["peak_rss"] - metrics["baseline_rss"],
                48 * 1024 * 1024,
                metrics,
            )
        self.assertGreater(metrics["log_size"], 0)
        self.assertLess(metrics["log_size"], 128 * 1024)
