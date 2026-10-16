"""Focused tests for authentication, offline tokens, and token caching."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tests.support.mock_server import MockServerTestCase


class TestCliAuth(MockServerTestCase):
    def test_auth_failure_missing_token(self):
        """Test that the CLI fails gracefully if no token is provided"""
        env_no_token = self.env.copy()
        del env_no_token["REDHAT_SUPPORT_OFFLINE_TOKEN"]

        # We also need to prevent it from asking for interactive input
        # The script uses getpass.getpass which reads from stdin.
        # We provide empty input to simulate user just hitting enter if prompted,
        # or rely on the script checking env var first.
        # However, the script exits if env var is missing AND prompt returns empty.

        cmd = [sys.executable, self.cli_path, "comment", "-c", "123", "-s", "redhat"]
        # Pipe empty stdin to simulate EOF or empty return
        result = subprocess.run(
            cmd,
            env=env_no_token,
            capture_output=True,
            text=True,
            input="\n",
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("offline token is required", result.stderr)

    def test_auth_priority(self):
        """Test the priority of token sources: CLI > ENV > FILE"""
        import tempfile

        # Create a temp directory for HOME
        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        # Setup Config File Token
        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        os.makedirs(config_dir)
        with open(os.path.join(config_dir, "token"), "w") as f:
            f.write("file_token")

        # Setup CLI Token File
        with open("cli_token.txt", "w") as f:
            f.write("cli_token")

        try:
            # 1. CLI Arg > Env > File
            self.env["REDHAT_SUPPORT_OFFLINE_TOKEN"] = "env_token"
            result = self.run_cli(["--token-file", "cli_token.txt", "list"])
            if result.returncode != 0:
                print("Failed CLI Arg Test STDOUT:", result.stdout)
                print("Failed CLI Arg Test STDERR:", result.stderr)
            self.assertEqual(result.returncode, 0)
            self.assertIn("AUTH=CLI", result.stdout)

            # Clear cache to force re-auth for next step
            shutil.rmtree(self.test_cache_dir)
            os.makedirs(self.test_cache_dir)

            # 2. Env > File (No CLI arg)
            result = self.run_cli(["list"])
            if result.returncode != 0:
                print("Failed Env Test STDOUT:", result.stdout)
                print("Failed Env Test STDERR:", result.stderr)
            self.assertEqual(result.returncode, 0)
            self.assertIn("AUTH=ENV", result.stdout)

            # Clear cache to force re-auth for next step
            shutil.rmtree(self.test_cache_dir)
            os.makedirs(self.test_cache_dir)

            # 3. File (No CLI arg, No Env)
            del self.env["REDHAT_SUPPORT_OFFLINE_TOKEN"]
            result = self.run_cli(["list"])
            if result.returncode != 0:
                print("Failed File Test STDOUT:", result.stdout)
                print("Failed File Test STDERR:", result.stderr)
            self.assertEqual(result.returncode, 0)
            self.assertIn("AUTH=FILE", result.stdout)

        finally:
            shutil.rmtree(temp_home)
            if os.path.exists("cli_token.txt"):
                os.remove("cli_token.txt")

    def test_token_caching(self):
        """Test that the access token is cached and reused"""
        import time

        # Use the cache dir set in setUp
        cache_path = os.path.join(
            self.env["XDG_CACHE_HOME"], "rh-support-cli", "token_cache_mock.json"
        )

        # 1. First run: Should hit SSO and populate cache
        result = self.run_cli(["list"])
        self.assertEqual(result.returncode, 0)

        self.assertTrue(
            os.path.exists(cache_path), "Token cache file should be created"
        )
        with open(cache_path, "r") as f:
            data = json.load(f)
            self.assertIn("access_token", data)
            self.assertIn("expires_at", data)
            _original_token = data["access_token"]

        # 2. Second run: Should use cache (no SSO request logic check here, but we can verify token is same)
        # To prove it used the cache, we can modify the cache file content manually to a "fake" token
        # and see if the CLI uses it.

        with open(cache_path, "w") as f:
            data["access_token"] = "cached_fake_token"
            # Ensure it's valid for at least another minute
            data["expires_at"] = time.time() + 60
            json.dump(data, f)

        # The list command output prints "AUTH=..." based on the token.
        # MockRedHatHandler returns "AUTH=UNKNOWN" or similar if token is unrecognized,
        # OR we can update the handler to recognize "Bearer cached_fake_token" -> "AUTH=CACHED".

        # But simpler: check the headers sent to the mock server?
        # We can't easily check headers in subprocess output unless we debug log.
        # Let's use debug log.

        result = self.run_cli(["--no-cache", "--debug", "list"])
        self.assertIn("Authorization': 'Bearer <HIDDEN>", result.stderr)
        # Wait, debug output hides the token.

        # Alternative: modifying MockRedHatHandler to return specific data for "cached_fake_token"?
        # Let's modify the list endpoint in the mock to return the auth source for unknown tokens too?
        # The current mock implementation:
        # if auth == "Bearer access_from_cli": source = "CLI" ...
        # else: source = "unknown"

        # If we send "cached_fake_token", source will be "unknown".
        # The output will contain "AUTH=unknown".
        # If we didn't use cache, it would use "mock_access_token_123" (default from SSO) -> "AUTH=DEFAULT".

        self.assertIn("AUTH=unknown", result.stdout)


class TestTokenCacheIdentity(unittest.TestCase):
    def setUp(self):

        self.cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache.cleanup)
        patcher = mock.patch.dict(
            os.environ,
            {
                "XDG_CACHE_HOME": self.cache.name,
                "RH_SSO_URL": "https://sso.example/a",
                "REDHAT_SUPPORT_OFFLINE_TOKEN": "credential-A",
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        post = mock.patch("rh_support_lib.api.requests.post")
        self.post = post.start()
        self.addCleanup(post.stop)
        self.post.return_value.json.return_value = {
            "access_token": "header.identity-A.signature",
            "expires_in": 3600,
        }

    def test_warm_cache_does_not_override_explicit_credentials(self):
        from pathlib import Path

        from rh_support_lib.api import get_access_token

        self.assertEqual(get_access_token(), "header.identity-A.signature")
        token_file = Path(self.cache.name) / "credential-b"
        token_file.write_text("credential-B")
        self.post.return_value.json.return_value["access_token"] = (
            "header.identity-B.signature"
        )
        self.assertEqual(
            get_access_token(str(token_file)), "header.identity-B.signature"
        )
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(
            get_access_token(str(token_file)), "header.identity-B.signature"
        )
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(
            self.post.call_args.kwargs["data"]["refresh_token"], "credential-B"
        )

    def test_cache_is_bound_to_actual_sso_endpoint(self):
        from rh_support_lib.api import get_access_token

        with mock.patch("rh_support_lib.api.SSO_URL", "https://sso.example/a"):
            get_access_token()
        self.post.return_value.json.return_value["access_token"] = (
            "header.backend-B.signature"
        )
        with mock.patch("rh_support_lib.api.SSO_URL", "https://sso.example/b"):
            self.assertEqual(get_access_token(), "header.backend-B.signature")
        self.assertEqual(self.post.call_count, 2)

    def test_unbound_legacy_cache_is_not_reused(self):
        from pathlib import Path

        from rh_support_lib.api import _get_token_cache_file, get_access_token

        directory, cache_file = _get_token_cache_file()
        Path(directory).mkdir(parents=True)
        Path(cache_file).write_text(
            json.dumps(
                {
                    "access_token": "header.legacy.signature",
                    "expires_at": time.time() + 3600,
                }
            )
        )
        self.assertEqual(get_access_token(), "header.identity-A.signature")
        self.post.assert_called_once()


class TestDebugLoggingSafety(unittest.TestCase):
    def test_transport_debug_logs_do_not_bypass_redaction(self):
        import logging

        import requests

        from rh_support_lib.api import enable_debug_logging

        response = mock.Mock(status_code=200, headers={}, text="OK")

        def transport(*args, **kwargs):
            logging.getLogger("urllib3.connectionpool").debug(
                "GET /?token=transport-secret"
            )
            return response

        with (
            mock.patch("requests.sessions.Session.request", side_effect=transport),
            mock.patch("logging.basicConfig"),
            mock.patch.object(logging.getLogger("urllib3"), "level", logging.DEBUG),
            self.assertLogs(level="DEBUG") as logs,
        ):
            enable_debug_logging()
            requests.Session().get("https://storage.example/")
        self.assertNotIn("transport-secret", "\n".join(logs.output))

    def test_streamed_response_body_is_never_consumed(self):
        import requests

        from rh_support_lib.api import enable_debug_logging

        for log_file in (None, "debug.log"):
            with self.subTest(log_file=log_file):
                response = mock.Mock(
                    status_code=200,
                    headers={"Content-Type": "application/octet-stream"},
                )
                text = mock.PropertyMock(
                    side_effect=AssertionError("Consumed streamed body")
                )
                type(response).text = text
                with (
                    mock.patch(
                        "requests.sessions.Session.request", return_value=response
                    ),
                    mock.patch("logging.basicConfig"),
                    self.assertLogs("rh-support-cli.debug", level="DEBUG") as logs,
                ):
                    enable_debug_logging(log_file)
                    result = requests.Session().get(
                        "https://storage.example/bundle", stream=True
                    )
                self.assertIs(result, response)
                text.assert_not_called()
                response.json.assert_not_called()
                self.assertIn("200", "\n".join(logs.output))

    def test_nested_credentials_and_presigned_urls_are_redacted(self):
        import requests

        from rh_support_lib.api import enable_debug_logging

        signed_url = "https://storage.example/bundle?X-Amz-Signature=storage-secret&X-Amz-Credential=credential-secret"
        response = mock.Mock(
            status_code=200,
            headers={"Content-Type": "application/json", "Set-Cookie": "cookie-secret"},
        )
        response.json.return_value = {
            "parts": [{"presignedUrl": signed_url}],
            "downloadUrl": signed_url,
            "nested": {"access_token": "access-secret"},
            "message": "retained-message-" + "x" * 1500,
        }
        with (
            mock.patch("requests.sessions.Session.request", return_value=response),
            mock.patch("logging.basicConfig"),
            self.assertLogs("rh-support-cli.debug", level="DEBUG") as logs,
        ):
            enable_debug_logging("debug.log")
            requests.Session().post(
                signed_url,
                headers={"authorization": "Bearer header-secret"},
                json={"nested": [{"refresh_token": "refresh-secret"}]},
            )
        output = "\n".join(logs.output)
        for secret in (
            "storage-secret",
            "credential-secret",
            "cookie-secret",
            "access-secret",
            "header-secret",
            "refresh-secret",
        ):
            self.assertNotIn(secret, output)
        self.assertIn("retained-message-" + "x" * 1500, output)
