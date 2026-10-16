"""Fresh-process loopback upload measurement; invoked only by the sandboxed suite."""

import hashlib
import http.server
import json
import logging
import os
import sys
import tempfile
import threading
import tracemalloc
from unittest.mock import patch

from rh_support_lib.api import RedHatAPIClient, enable_debug_logging
from rh_support_lib.services import attachments


def measure_upload():
    if os.environ.get("RH_TEST_SANDBOX_ACTIVE") != "1":
        raise RuntimeError("Upload probe requires the offline test sandbox")
    part_size = 32 * 1024 * 1024
    file_size = 3 * part_size + 123
    received = []
    completions = []
    initiations = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/complete"):
                completions.append(body)
                self.respond(401 if len(completions) == 1 else 200, {})
            elif self.path.endswith("/upload"):
                initiations.append(body)
                self.respond(
                    200,
                    {
                        "attachmentId": "att-memory",
                        "uploadId": "upload-memory",
                        "uploadStrategy": "MULTIPART",
                        "parts": [
                            {
                                "partNumber": number,
                                "presignedUrl": f"http://127.0.0.1:{self.server.server_port}/part/{number}",
                            }
                            for number in (4, 2, 1, 3)
                        ],
                    },
                )
            else:
                self.respond(404, {})

        def do_PUT(self):
            length = int(self.headers["Content-Length"])
            remaining = length
            digest = hashlib.sha256()
            while remaining:
                chunk = self.rfile.read(min(remaining, 64 * 1024))
                if not chunk:
                    break
                digest.update(chunk)
                remaining -= len(chunk)
            received.append((self.path, length - remaining, digest.hexdigest()))
            self.send_response(200)
            self.send_header("ETag", f'"etag-{self.path.rsplit("/", 1)[-1]}"')
            self.send_header("Content-Length", "0")
            self.end_headers()

    with tempfile.TemporaryDirectory(prefix="rh-upload-probe-") as root:
        source = os.path.join(root, "source.bin")
        expected = []
        with open(source, "wb") as output:
            output.truncate(file_size)
            for index in range(4):
                output.seek(index * part_size)
                output.write(bytes([index + 1]))
        with open(source, "rb") as input_file:
            for index in range(4):
                length = min(part_size, file_size - index * part_size)
                remaining = length
                digest = hashlib.sha256()
                while remaining:
                    chunk = input_file.read(min(remaining, 64 * 1024))
                    digest.update(chunk)
                    remaining -= len(chunk)
                expected.append((f"/part/{index + 1}", length, digest.hexdigest()))

        streams = []
        active = 0
        max_active = 0

        class TrackingStream(attachments.FilePartStream):
            def __enter__(self):
                nonlocal active, max_active
                streams.append(self)
                active += 1
                max_active = max(max_active, active)
                return super().__enter__()

            def __exit__(self, *args):
                nonlocal active
                try:
                    return super().__exit__(*args)
                finally:
                    active -= 1

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        log_path = os.path.join(root, "debug.log")
        logging.basicConfig(filename=log_path, level=logging.DEBUG, force=True)
        enable_debug_logging(log_file=log_path)
        client = RedHatAPIClient()
        client._offline_token = "synthetic-offline-token"
        service = attachments.AttachmentService(
            client, base_url=f"http://127.0.0.1:{server.server_port}"
        )
        service.MULTIPART_THRESHOLD = part_size
        progress = [0, 0, 0]

        def on_progress(current, total):
            assert current >= progress[0] and total == file_size
            progress[:] = [current, total, progress[2] + 1]

        try:
            try:
                import resource
            except ImportError:
                resource = None
            rss_scale = 1 if sys.platform == "darwin" else 1024
            baseline_rss = (
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * rss_scale
                if resource is not None
                else None
            )
            tracemalloc.start()
            with (
                patch.object(attachments, "FilePartStream", TrackingStream),
                patch(
                    "rh_support_lib.api.get_access_token",
                    side_effect=["synthetic-access-one", "synthetic-access-two"],
                ) as authenticate,
            ):
                result = service.upload_attachment(
                    "123", source, progress_callback=on_progress
                )
            _, peak_heap = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            peak_rss = (
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * rss_scale
                if resource is not None
                else None
            )
            assert authenticate.call_count == 2, "Completion did not refresh and retry"
            assert result.uuid == "att-memory" and result.size == file_size
            assert result.upload_status == "Completed"
            assert len(initiations) == 1 and initiations[0]["totalChunks"] == 4
            assert received == expected, "Transferred slices differ from source bytes"
            assert len(completions) == 2 and completions[0] == completions[1]
            assert completions[0] == {
                "attachmentId": "att-memory",
                "caseNumber": "123",
                "uploadId": "upload-memory",
                "parts": [
                    {"partNumber": number, "eTag": f"etag-{number}"}
                    for number in range(1, 5)
                ],
            }
            assert progress[0] == file_size and progress[2] > 4
            assert len(streams) == 4 and max_active == 1 and active == 0
            assert all(stream._file.closed for stream in streams)
            logging.shutdown()
            assert os.path.isfile(log_path), "Debug log was not created"
            with open(log_path) as log_file:
                log = log_file.read()
            assert "non-JSON request body omitted" in log
            assert "Response Status: 401" in log and "Response Status: 200" in log
            assert "synthetic-access" not in log and "synthetic-offline" not in log
            return {
                "file_size": file_size,
                "peak_heap": peak_heap,
                "baseline_rss": baseline_rss,
                "peak_rss": peak_rss,
                "log_size": os.path.getsize(log_path),
                "parts": len(received),
                "completion_attempts": len(completions),
            }
        finally:
            tracemalloc.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    print(json.dumps(measure_upload()))
