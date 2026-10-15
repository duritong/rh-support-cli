import hashlib
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from rh_support_lib import api


class AuthConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / "private" / "token.json"
        patcher = patch.object(
            api,
            "_get_token_cache_file",
            return_value=(str(self.cache.parent), str(self.cache)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(api, "_get_offline_token", return_value="credential-a")
        self.resolve = patcher.start()
        self.addCleanup(patcher.stop)

    def test_initial_acquisition_is_singleflight(self):
        client = api.RedHatAPIClient()
        entered = threading.Event()
        release = threading.Event()
        duplicate = threading.Event()
        second_started = threading.Event()

        def exchange(*args):
            if entered.is_set():
                duplicate.set()
            entered.set()
            self.assertTrue(release.wait(5))
            return "initial-token"

        def second():
            second_started.set()
            return client.get_token()

        with patch.object(api, "get_access_token", side_effect=exchange) as acquire:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(client.get_token)
                self.assertTrue(entered.wait(5))
                other = pool.submit(second)
                try:
                    self.assertTrue(second_started.wait(5))
                    self.assertFalse(duplicate.wait(0.1))
                finally:
                    release.set()
                self.assertEqual(first.result(5), "initial-token")
                self.assertEqual(other.result(5), "initial-token")
            self.assertEqual(acquire.call_count, 1)
            self.assertEqual(self.resolve.call_count, 1)

    def test_concurrent_401_reuses_refresh_and_isolates_headers(self):
        client = api.RedHatAPIClient()
        client._token = "old-token"
        client._offline_token = "credential-a"
        both_old = threading.Barrier(2)
        refreshed_request = threading.Event()
        shared = {"X-Test": "kept"}
        seen = []

        def request(method, url, **kwargs):
            headers = kwargs["headers"]
            seen.append(headers)
            if headers["Authorization"] == "Bearer old-token":
                both_old.wait(5)
                if url.endswith("late"):
                    self.assertTrue(refreshed_request.wait(5))
                return Mock(status_code=401)
            refreshed_request.set()
            return Mock(status_code=200)

        with (
            patch.object(api, "get_access_token", return_value="new-token") as acquire,
            patch.object(api.requests, "request", side_effect=request),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            futures = [
                pool.submit(client.get, "https://invalid/" + name, headers=shared)
                for name in ("early", "late")
            ]
            self.assertEqual([f.result(5).status_code for f in futures], [200, 200])
            self.assertEqual(acquire.call_count, 1)
        self.assertEqual(shared, {"X-Test": "kept"})
        self.assertIsNot(seen[0], seen[1])
        self.assertTrue(all(headers is not shared for headers in seen))

    def test_overlapping_401_refresh_is_singleflight(self):
        client = api.RedHatAPIClient()
        client._token = "old-token"
        client._offline_token = "credential-a"
        both_old = threading.Barrier(2)
        entered = threading.Event()
        duplicate = threading.Event()
        release = threading.Event()

        def exchange(*args):
            if entered.is_set():
                duplicate.set()
            entered.set()
            self.assertTrue(release.wait(5))
            return "new-token"

        def request(method, url, **kwargs):
            if kwargs["headers"]["Authorization"] == "Bearer old-token":
                both_old.wait(5)
                return Mock(status_code=401)
            return Mock(status_code=200)

        with (
            patch.object(api, "get_access_token", side_effect=exchange) as acquire,
            patch.object(api.requests, "request", side_effect=request),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            futures = [pool.submit(client.get, "https://invalid/") for _ in range(2)]
            try:
                self.assertTrue(entered.wait(5))
                self.assertFalse(duplicate.wait(0.1))
            finally:
                release.set()
            self.assertEqual([f.result(5).status_code for f in futures], [200, 200])
            self.assertEqual(acquire.call_count, 1)

    def test_stale_401_reuses_refresh_even_if_token_string_is_unchanged(self):
        client = api.RedHatAPIClient()
        client._token = "same-token"
        client._offline_token = "credential-a"
        barrier = threading.Barrier(2)
        retried = threading.Event()
        attempts = {}
        lock = threading.Lock()

        def request(method, url, **kwargs):
            with lock:
                attempt = attempts[url] = attempts.get(url, 0) + 1
            if attempt == 1:
                barrier.wait(5)
                if url.endswith("late"):
                    self.assertTrue(retried.wait(5))
                return Mock(status_code=401)
            retried.set()
            return Mock(status_code=200)

        with (
            patch.object(api, "get_access_token", return_value="same-token") as acquire,
            patch.object(api.requests, "request", side_effect=request),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            futures = [
                pool.submit(client.get, "https://invalid/" + name)
                for name in ("early", "late")
            ]
            self.assertEqual([f.result(5).status_code for f in futures], [200, 200])
            self.assertEqual(acquire.call_count, 1)

    def test_none_headers_are_supported_by_both_clients(self):
        client = api.RedHatAPIClient()
        client._token = "token"
        with patch.object(api.requests, "request", return_value=Mock(status_code=200)):
            client.get("https://invalid/", headers=None)
            api.LegacyAPIClient("token").get("https://invalid/", headers=None)

    def test_discarded_401_stream_is_closed_but_final_response_is_owned_by_caller(self):
        client = api.RedHatAPIClient()
        client._token = "old-token"
        old, final = Mock(status_code=401), Mock(status_code=200)
        with (
            patch.object(api, "get_access_token", return_value="new-token"),
            patch.object(api.requests, "request", side_effect=[old, final]),
        ):
            self.assertIs(client.get("https://invalid/", stream=True), final)
        old.close.assert_called_once()
        final.close.assert_not_called()

    def test_retry_is_limited_and_credential_remains_pinned(self):
        client = api.RedHatAPIClient()
        scope = client.get_cache_scope("backend-a")
        self.resolve.return_value = "credential-b"
        response = Mock(status_code=401)
        with (
            patch.object(
                api, "get_access_token", side_effect=["old", "new"]
            ) as acquire,
            patch.object(api.requests, "request", return_value=response) as request,
        ):
            self.assertIs(client.get("https://invalid/"), response)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(acquire.call_args_list[0].args, (None, "credential-a"))
            self.assertEqual(acquire.call_args_list[1].args, (None, "credential-a"))
        self.assertEqual(scope, client.get_cache_scope("backend-a"))
        self.assertNotEqual(scope, client.get_cache_scope("backend-b"))

    def test_legacy_headers_are_not_mutated(self):
        headers = {"Accept": "custom"}
        with patch.object(api.requests, "request") as request:
            api.LegacyAPIClient("token").get("https://invalid/", headers=headers)
        self.assertEqual(headers, {"Accept": "custom"})
        self.assertIsNot(request.call_args.kwargs["headers"], headers)

    def test_request_exception_still_propagates_without_retry(self):
        client = api.RedHatAPIClient()
        failure = api.requests.exceptions.ConnectionError("offline")
        with (
            patch.object(api, "get_access_token", return_value="token"),
            patch.object(api.requests, "request", side_effect=failure) as request,
        ):
            with self.assertRaises(api.requests.exceptions.ConnectionError) as caught:
                client.get("https://invalid/")
            self.assertIs(caught.exception, failure)
            self.assertEqual(request.call_count, 1)


class AtomicTokenCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "private"
        self.directory.mkdir()
        self.cache = self.directory / "token.json"
        patcher = patch.object(
            api,
            "_get_token_cache_file",
            return_value=(str(self.directory), str(self.cache)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(api.requests, "post", side_effect=self.exchange)
        self.post = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def exchange(url, data, timeout):
        return Mock(
            json=lambda: {
                "access_token": data["refresh_token"] + ".access.jwt",
                "expires_in": 300,
            }
        )

    @staticmethod
    def context(credential):
        return hashlib.sha256(
            json.dumps(
                {
                    "offline_token": credential,
                    "sso_url": api.SSO_URL,
                    "client_id": "rhsm-api",
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    def test_publish_is_atomic_and_private(self):
        old = '{"previous": true}'
        self.cache.write_text(old)
        dump = json.dump

        def gated_dump(data, stream):
            self.assertEqual(self.cache.read_text(), old)
            self.assertNotEqual(Path(stream.name), self.cache)
            self.assertEqual(Path(stream.name).parent, self.directory)
            self.assertEqual(stat.S_IMODE(os.fstat(stream.fileno()).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
            dump(data, stream)
            stream.flush()
            self.assertEqual(self.cache.read_text(), old)

        with patch.object(api.json, "dump", side_effect=gated_dump):
            self.assertEqual(api.get_access_token(offline_token="a"), "a.access.jwt")
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o600)
        self.assertEqual(
            json.loads(self.cache.read_text())["credential_context"], self.context("a")
        )
        self.assertEqual(list(self.directory.iterdir()), [self.cache])

    def test_different_writers_publish_only_bound_pairs(self):
        first_prefix = threading.Event()
        second_complete = threading.Event()

        def interleaved_dump(data, stream):
            text = json.dumps(data)
            if data["access_token"] == "a.access.jwt":
                split = text.index('"access_token"')
                stream.write(text[:split])
                stream.flush()
                first_prefix.set()
                self.assertTrue(second_complete.wait(5))
                stream.write(text[split:])
            else:
                self.assertTrue(first_prefix.wait(5))
                stream.write(text)
                stream.flush()
                second_complete.set()

        with (
            patch.object(api.json, "dump", side_effect=interleaved_dump),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            futures = [
                pool.submit(api.get_access_token, offline_token=c) for c in ("a", "b")
            ]
            self.assertEqual(
                [f.result(5) for f in futures], ["a.access.jwt", "b.access.jwt"]
            )
        data = json.loads(self.cache.read_text())
        credential = data["access_token"].split(".")[0]
        self.assertEqual(data["credential_context"], self.context(credential))
        self.assertEqual(list(self.directory.iterdir()), [self.cache])

    def test_failed_write_or_replace_preserves_old_cache(self):
        old = '{"previous": true}'
        for failure in ("dump", "replace"):
            with self.subTest(failure=failure):
                self.cache.write_text(old)
                target = api.json if failure == "dump" else api.os
                with patch.object(target, failure, side_effect=OSError("write failed")):
                    self.assertEqual(
                        api.get_access_token(offline_token="a"), "a.access.jwt"
                    )
                self.assertEqual(self.cache.read_text(), old)
                self.assertEqual(list(self.directory.iterdir()), [self.cache])

    def test_cache_hit_is_bound_to_credential(self):
        self.assertEqual(api.get_access_token(offline_token="a"), "a.access.jwt")
        self.assertEqual(api.get_access_token(offline_token="a"), "a.access.jwt")
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(api.get_access_token(offline_token="b"), "b.access.jwt")
        self.assertEqual(self.post.call_count, 2)

    def test_authentication_failure_keeps_output_and_exit_contract(self):
        self.post.side_effect = api.requests.exceptions.HTTPError(
            response=Mock(text="SSO failure")
        )
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            api.get_access_token(offline_token="a")
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(
            output.getvalue(),
            "Error: Failed to authenticate.\nSSO Response: SSO failure\n",
        )


if __name__ == "__main__":
    unittest.main()
