"""Focused checks for the canonical offline unittest harness."""

import io
import logging
import multiprocessing
import os
import runpy
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest import mock

import requests

from tests.support import SandboxSuite
from tests.support.network_guard import _loopback, network_guard
from tests.support.sandbox import _sandbox, _state


def _spawn_probe(queue):
    try:
        socket.getaddrinfo("offline-probe.invalid", 443)
    except RuntimeError:
        queue.put(True)
    else:
        queue.put(False)


def _multiprocessing_probe():
    for method in ("spawn", "forkserver"):
        context = multiprocessing.get_context(method)
        queue = context.Queue()
        process = context.Process(target=_spawn_probe, args=(queue,))
        try:
            process.start()
            assert queue.get(timeout=15)
            process.join(timeout=15)
            assert process.exitcode == 0
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            queue.close()
            queue.join_thread()
    print(True)


class IsolationTests(unittest.TestCase):
    def test_package_discovery_collects_each_test_once(self):
        def identifiers(suite):
            for test in suite:
                if isinstance(test, unittest.TestSuite):
                    yield from identifiers(test)
                else:
                    yield test.id()

        root = Path(__file__).resolve().parents[1]
        package = list(identifiers(unittest.TestLoader().loadTestsFromName("tests")))
        discovered = list(
            identifiers(
                unittest.TestLoader().discover(str(root), top_level_dir=str(root))
            )
        )
        self.assertTrue(package)
        self.assertEqual(len(package), len(set(package)))
        self.assertEqual(sorted(package), sorted(discovered))
        self.assertTrue(all(name.startswith("tests.test_") for name in package))

    def test_focused_runner_uses_sandbox_and_buffering(self):
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "tests",
                "tests.test_isolation.IsolationTests.test_restore_and_cleanup_on_exception",
                "-q",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        self.assertEqual(process.stdout, "")
        self.assertIn("Ran 1 test", process.stderr)
        self.assertIn("OK", process.stderr)

    def test_asyncio_logging_uses_result_buffer_despite_stderr_redirection(self):
        result = unittest.TestResult()
        result.buffer = True
        # Initialize unittest's per-test buffers as a real runner does.
        result.startTest(unittest.FunctionTestCase(lambda: None))
        try:
            redirected = io.StringIO()
            with _sandbox(result), redirect_stderr(redirected):
                logging.getLogger("asyncio").warning("buffered asyncio warning")
            self.assertEqual(redirected.getvalue(), "")
            self.assertIn("buffered asyncio warning", result._stderr_buffer.getvalue())
        finally:
            result.stopTest(unittest.FunctionTestCase(lambda: None))

    def test_canonical_runner_buffers_noise_but_keeps_failure_diagnostics(self):
        original_main = unittest.main
        for outcome in ("pass", "failure", "error", "subtest"):
            with self.subTest(outcome=outcome):
                module = ModuleType("output_probe")

                class Probe(unittest.TestCase):
                    def test_output(probe, outcome=outcome):
                        print("probe stdout")
                        print("probe stderr", file=sys.stderr)
                        logging.getLogger("asyncio").warning("probe asyncio warning")
                        if outcome == "failure":
                            probe.fail("probe assertion")
                        elif outcome == "error":
                            raise RuntimeError("probe exception")
                        elif outcome == "subtest":
                            with probe.subTest(stage="probe"):
                                probe.fail("probe subtest assertion")

                module.Probe = Probe
                stdout, stderr = io.StringIO(), io.StringIO()

                def run_probe(probe_module=module, **kwargs):
                    kwargs.update(module=probe_module, defaultTest=None)
                    return original_main(argv=["output_probe"], exit=False, **kwargs)

                with (
                    _sandbox(),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                    mock.patch.object(
                        logging.getLogger(), "handlers", [logging.StreamHandler(stderr)]
                    ),
                    mock.patch("unittest.main", side_effect=run_probe) as main,
                ):
                    runpy.run_path(
                        str(Path(__file__).with_name("__main__.py")),
                        run_name="__main__",
                    )
                self.assertTrue(main.call_args.kwargs["buffer"])
                self.assertIs(
                    main.call_args.kwargs["testLoader"].suiteClass, SandboxSuite
                )
                output = stdout.getvalue() + stderr.getvalue()
                self.assertIn("Ran 1 test", output)
                if outcome == "pass":
                    self.assertIn("OK", output)
                    self.assertNotIn("probe stdout", output)
                    self.assertNotIn("probe stderr", output)
                    self.assertNotIn("probe asyncio warning", output)
                else:
                    self.assertIn("FAILED", output)
                    self.assertIn("Traceback", output)
                    self.assertIn("probe stdout", output)
                    self.assertIn("probe stderr", output)
                    self.assertIn("probe asyncio warning", output)

    def test_restore_and_cleanup_on_exception(self):
        with mock.patch.dict(os.environ, {"RH_OFFLINE_TOKEN": "dummy-only-secret"}):
            original_path = sys.path[:]
            original_cwd = os.getcwd()
            original_tempdir = tempfile.tempdir
            original_connect = socket.socket.connect
            logger = logging.getLogger("asyncio")
            original_handlers = logger.handlers
            original_propagate = logger.propagate
            with self.assertRaisesRegex(ValueError, "probe"), _sandbox():
                root = os.environ["HOME"]
                self.assertNotIn("RH_OFFLINE_TOKEN", os.environ)
                self.assertEqual(tempfile.gettempdir(), root)
                self.assertEqual(os.getcwd(), root)
                self.assertEqual(os.environ["XDG_CONFIG_HOME"], root + "/config")
                self.assertFalse(Path(root, ".netrc").exists())
                os.environ["RH_NEW_VALUE"] = "dummy"
                sys.path.append("dummy-path")
                raise ValueError("probe")
            self.assertTrue(os.environ.get("RH_OFFLINE_TOKEN") == "dummy-only-secret")
            self.assertNotIn("RH_NEW_VALUE", os.environ)
            self.assertEqual(sys.path, original_path)
            self.assertEqual(os.getcwd(), original_cwd)
            self.assertEqual(tempfile.tempdir, original_tempdir)
            self.assertIs(socket.socket.connect, original_connect)
            self.assertIs(logger.handlers, original_handlers)
            self.assertEqual(logger.propagate, original_propagate)
            self.assertFalse(Path(root).exists())

    def test_blocks_before_original_functions(self):
        with (
            mock.patch.object(socket, "getaddrinfo") as resolve,
            mock.patch.object(socket.socket, "connect") as connect,
            mock.patch.object(socket.socket, "connect_ex") as connect_ex,
            network_guard(),
            socket.socket() as sock,
        ):
            for host in ("offline-probe.invalid", "192.0.2.1", "::ffff:192.0.2.1"):
                with self.assertRaises(RuntimeError):
                    socket.getaddrinfo(host, 443)
                for method in (sock.connect, sock.connect_ex):
                    with self.assertRaises(RuntimeError):
                        method((host, 443))
            resolve.assert_not_called()
            connect.assert_not_called()
            connect_ex.assert_not_called()

    def test_loopback_and_unix_allowed(self):
        for host in (
            "127.0.0.1",
            "127.42.1.2",
            "::1",
            "::ffff:127.0.0.2",
            "localhost",
            b"localhost.",
        ):
            self.assertTrue(_loopback(host))
        with tempfile.TemporaryDirectory() as directory:
            address = directory + "/socket"
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(address)
                server.listen()
                with socket.socket(socket.AF_UNIX) as client:
                    self.assertEqual(client.connect_ex(address), 0)
                connection, _ = server.accept()
                connection.close()
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            with socket.create_connection(server.getsockname(), timeout=5):
                connection, _ = server.accept()
                connection.close()

    def test_child_inherits_guard_from_sys_path(self):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(sys.path)
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                (
                    "import socket\ntry: socket.getaddrinfo('offline-probe.invalid', 443)\n"
                    "except RuntimeError: print(True)\nelse: print(False)"
                ),
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        self.assertEqual(process.stdout.strip(), "True")

    def test_multiprocessing(self):
        # Let multiprocessing's process-lifetime finalizers precede root cleanup.
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "from tests.test_isolation import _multiprocessing_probe; _multiprocessing_probe()",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        self.assertEqual(process.stdout.strip(), "True")
        self.assertEqual(process.stderr, "")

    def test_unflagged_import_has_no_socket_side_effects(self):
        environment = os.environ.copy()
        environment.pop("RH_TEST_SANDBOX_ACTIVE", None)
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                (
                    "import socket; original = socket.getaddrinfo; "
                    "import tests.support, tests.support.sitecustomize; "
                    "print(socket.getaddrinfo is original)"
                ),
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        self.assertEqual(process.stdout.strip(), "True")

    def test_external_flag_is_not_owned_context(self):
        root = os.environ["HOME"]
        result = unittest.TestResult()
        with mock.patch.object(_state, "active", False):
            suite = SandboxSuite(
                [
                    unittest.FunctionTestCase(
                        lambda: self.assertNotEqual(os.environ["HOME"], root)
                    )
                ]
            )
            suite.run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertTrue(result.wasSuccessful())

    def test_mocked_requests_serialization(self):
        with mock.patch.object(requests.Session, "send") as send:
            requests.post(
                "https://offline-probe.invalid", files={"file": ("test", b"data")}
            )
        request = send.call_args.args[0]
        self.assertIn(b"data", request.body)
        self.assertIn("multipart/form-data", request.headers["Content-Type"])

    def test_nested_suite_reuses_owned_context(self):
        root = os.environ["HOME"]
        result = unittest.TestResult()
        suite = SandboxSuite(
            [
                unittest.FunctionTestCase(
                    lambda: self.assertEqual(os.environ["HOME"], root)
                )
            ]
        )
        self.assertIs(suite.run(result), result)
        self.assertTrue(result.wasSuccessful())


def load_tests(loader, standard_tests, pattern):
    return SandboxSuite(standard_tests)
