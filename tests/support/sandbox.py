"""Scoped process-global isolation for serial unittest execution on Linux."""

import logging
import os
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from .network_guard import network_guard

_state = threading.local()


class _BufferedLogHandler(logging.Handler):
    def __init__(self, result):
        super().__init__()
        self.result = result

    def emit(self, record):
        # Textual forwards stderr to sys.__stderr__, bypassing unittest's capture.
        stream = (
            getattr(self.result, "_stderr_buffer", None)
            if self.result is not None and self.result.buffer
            else None
        )
        logging.StreamHandler(stream if stream is not None else sys.stderr).emit(record)


@contextmanager
def _sandbox(result=None):
    environment = os.environ.copy()
    path = sys.path[:]
    cwd = os.getcwd()
    tempdir = tempfile.tempdir
    active = getattr(_state, "active", False)
    try:
        # Never choose the parent through a personal TMPDIR or tempfile cache.
        with tempfile.TemporaryDirectory(prefix="rh-tests-", dir="/tmp") as root:
            try:
                for key in list(os.environ):
                    if (
                        key.startswith(("RH_", "REDHAT_SUPPORT_", "XDG_"))
                        or key.upper().endswith("_PROXY")
                        or key.upper()
                        in {
                            "TMPDIR",
                            "TMP",
                            "TEMP",
                            "TEMPDIR",
                            "REQUESTS_CA_BUNDLE",
                            "CURL_CA_BUNDLE",
                            "SSL_CERT_FILE",
                            "SSL_CERT_DIR",
                            "DBUS_SESSION_BUS_ADDRESS",
                            "DBUS_SYSTEM_BUS_ADDRESS",
                            "DISPLAY",
                            "WAYLAND_DISPLAY",
                            "XAUTHORITY",
                            "NETRC",
                        }
                    ):
                        os.environ.pop(key, None)
                os.environ["HOME"] = root
                for key, directory in {
                    "XDG_CONFIG_HOME": "config",
                    "XDG_CACHE_HOME": "cache",
                    "XDG_DATA_HOME": "data",
                    "XDG_STATE_HOME": "state",
                    "XDG_RUNTIME_DIR": "runtime",
                }.items():
                    location = Path(root, directory)
                    location.mkdir(mode=0o700)
                    os.environ[key] = str(location)
                os.environ.update(
                    TMPDIR=root,
                    TMP=root,
                    TEMP=root,
                    RH_TEST_SANDBOX_ACTIVE="1",
                    PYTHONDONTWRITEBYTECODE="1",
                )
                tempfile.tempdir = root
                support = Path(__file__).resolve().parent
                sys.path[:] = [str(support), str(support.parent.parent)] + [
                    os.path.abspath(entry) for entry in path
                ]
                os.environ["PYTHONPATH"] = os.pathsep.join(sys.path)
                os.chdir(root)
                _state.active = True
                with (
                    mock.patch.object(
                        logging.getLogger("asyncio"),
                        "handlers",
                        [_BufferedLogHandler(result)],
                    ),
                    mock.patch.object(logging.getLogger("asyncio"), "propagate", False),
                    network_guard(),
                ):
                    yield
            finally:
                os.chdir(cwd)
                tempfile.tempdir = tempdir
                sys.path[:] = path
                os.environ.clear()
                os.environ.update(environment)
                _state.active = active
    finally:
        tempfile.tempdir = tempdir


class SandboxSuite(unittest.TestSuite):
    """Wrap standard_tests in load_tests; isolation begins only at run()."""

    def run(self, result, debug=False):
        if getattr(_state, "active", False):
            return super().run(result, debug)
        with _sandbox(result):
            return super().run(result, debug)
