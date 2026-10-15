import io
import json
import os
import tempfile
import traceback
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from rh_support_lib import api


class AuthenticationErrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for patcher in (
            patch.dict(os.environ, {}, clear=True),
            patch.object(
                api.os.path, "expanduser", return_value=str(self.root / "token")
            ),
            patch.object(
                api,
                "_get_token_cache_file",
                return_value=(str(self.root), str(self.root / "cache.json")),
            ),
            patch.object(api.sys.stdin, "isatty", return_value=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(api.requests, "post")
        self.post = patcher.start()
        self.addCleanup(patcher.stop)

    def assert_private_failure(self, operation):
        output, errors = io.StringIO(), io.StringIO()
        with (
            redirect_stdout(output),
            redirect_stderr(errors),
            self.assertRaises(api.AuthenticationError) as caught,
        ):
            operation()
        self.assertEqual(output.getvalue() + errors.getvalue(), "")
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("https://", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        rendered = "".join(
            traceback.format_exception(
                type(caught.exception), caught.exception, caught.exception.__traceback__
            )
        )
        self.assertNotIn("secret https://private", rendered)

    def test_missing_credentials_never_print_or_exit(self):
        self.assert_private_failure(api._get_offline_token)
        self.post.assert_not_called()

    def test_selected_files_fail_closed_and_local_lookup_does_not_prompt(self):
        empty = self.root / "empty"
        empty.write_text("   ")
        with patch.dict(os.environ, {"REDHAT_SUPPORT_OFFLINE_TOKEN": "ambient"}):
            for filename in (empty, self.root / "missing"):
                with self.subTest(filename=filename):
                    self.assert_private_failure(
                        lambda filename=filename: api._get_offline_token(filename)
                    )
                    self.assertIsNone(
                        api._get_offline_token(filename, interactive=False)
                    )
        with patch.object(api.getpass, "getpass") as prompt:
            self.assertIsNone(api._get_offline_token(interactive=False))
            prompt.assert_not_called()

    def test_unreadable_selected_file_is_sanitized(self):
        with (
            patch.object(api.os.path, "isfile", return_value=True),
            patch("builtins.open", side_effect=OSError("secret https://private")),
        ):
            self.assert_private_failure(lambda: api._get_offline_token("secret"))
            self.assertIsNone(api._get_offline_token("secret", interactive=False))

    def test_empty_config_and_environment_do_not_prompt(self):
        (self.root / "token").write_text(" ")
        with (
            patch.object(api.sys.stdin, "isatty", return_value=True),
            patch.object(api.getpass, "getpass") as prompt,
        ):
            self.assert_private_failure(api._get_offline_token)
            with patch.dict(os.environ, {"REDHAT_SUPPORT_OFFLINE_TOKEN": " "}):
                self.assert_private_failure(api._get_offline_token)
            prompt.assert_not_called()

    def test_tty_prompt_is_allowed_but_empty_or_failed_prompt_raises(self):
        with patch.object(api.sys.stdin, "isatty", return_value=True):
            with patch.object(api.getpass, "getpass", return_value="credential"):
                self.assertEqual(api._get_offline_token(), "credential")
            with patch.object(api.getpass, "getpass", return_value=" "):
                self.assert_private_failure(api._get_offline_token)
            with patch.object(api.getpass, "getpass", side_effect=EOFError("secret")):
                self.assert_private_failure(api._get_offline_token)

    def test_empty_explicit_credential_never_falls_back(self):
        with patch.object(api, "_get_offline_token") as resolve:
            self.assert_private_failure(lambda: api.get_access_token(offline_token=""))
            resolve.assert_not_called()
        self.post.assert_not_called()

    def test_http_json_and_malformed_results_raise_private_errors(self):
        results = [
            None,
            [],
            {},
            {"access_token": "", "expires_in": 300},
            {"access_token": 123, "expires_in": 300},
            {"access_token": "secret token", "expires_in": 300},
            {"access_token": "valid"},
        ]
        results += [
            {"access_token": "valid", "expires_in": expiry}
            for expiry in (None, "300", True, 0, -1, float("nan"), float("inf"))
        ]
        for result in results:
            with self.subTest(result=result):
                self.post.return_value = Mock(json=Mock(return_value=result))
                self.assert_private_failure(
                    lambda: api.get_access_token(offline_token="secret")
                )
                self.assertFalse((self.root / "cache.json").exists())
        for failure in (
            api.requests.exceptions.HTTPError(
                "secret https://private", response=Mock(text="secret https://private")
            ),
            api.requests.exceptions.ConnectionError("secret https://private"),
        ):
            self.post.side_effect = failure
            self.assert_private_failure(
                lambda: api.get_access_token(offline_token="secret")
            )
        self.post.side_effect = None
        self.post.return_value = Mock(json=Mock(side_effect=ValueError("secret")))
        self.assert_private_failure(
            lambda: api.get_access_token(offline_token="secret")
        )

    def test_cli_owns_nonzero_error_presentation(self):
        from rh_support_lib.main import main

        for failure in (
            api.AuthenticationError("An offline token is required."),
            api.RequestReplayError("Body is not replayable."),
        ):
            with (
                self.subTest(failure=type(failure).__name__),
                patch("rh_support_lib.main._main", side_effect=failure),
                self.assertRaises(SystemExit) as caught,
            ):
                main()
            self.assertIn(str(failure), str(caught.exception))

    def test_missing_authentication_is_not_hidden_as_optional_editor_context(self):
        from types import SimpleNamespace

        from rh_support_lib.commands.actions import cmd_comment

        args = SimpleNamespace(
            case="123",
            template=None,
            file=None,
            edit=False,
            status=None,
            include_previous_comments=1,
        )
        with (
            patch("rh_support_lib.commands.actions.CaseService") as service,
            patch("rh_support_lib.commands.actions.open_editor") as editor,
            redirect_stdout(io.StringIO()),
            self.assertRaises(api.AuthenticationError),
        ):
            service.return_value.get_case_details.side_effect = api.AuthenticationError(
                "An offline token is required."
            )
            cmd_comment(args, Mock())
        editor.assert_not_called()


class OfflineKcsTests(unittest.TestCase):
    def test_cli_rejects_offline_kcs_before_configuration_or_authentication(self):
        from rh_support_lib.main import main

        with (
            patch(
                "sys.argv",
                ["rh-support-cli", "search", "kernel", "--local", "--type", "kcs"],
            ),
            patch("rh_support_lib.main.argcomplete", None),
            patch("rh_support_lib.main.load_config") as config,
            patch("rh_support_lib.api.RedHatAPIClient") as client,
            redirect_stdout(io.StringIO()) as output,
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            main()
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(output.getvalue(), "")
        config.assert_not_called()
        client.assert_not_called()

    def test_direct_command_rejects_offline_kcs_before_service_construction(self):
        from types import SimpleNamespace

        from rh_support_lib.commands.search import cmd_search

        with (
            patch("rh_support_lib.commands.search.SearchService") as service,
            self.assertRaisesRegex(SystemExit, "cached cases only"),
        ):
            cmd_search(
                SimpleNamespace(query="kernel", local=True, type="kcs"), Mock(), {}
            )
        service.assert_not_called()

    def test_local_identity_survives_an_inflight_access_token_refresh(self):
        import base64

        from rh_support_lib.services.cases import CaseService

        claims = (
            base64.urlsafe_b64encode(
                json.dumps({"preferred_username": "known-user"}).encode()
            )
            .decode()
            .rstrip("=")
        )
        client = Mock(spec=api.RedHatAPIClient)
        client.get_token.return_value = f"header.{claims}.signature"
        with patch.dict(
            os.environ,
            {
                "REDHAT_SUPPORT_USERNAME": "",
                "RH_SSO_USERNAME": "",
                "RH_USERNAME": "",
                "USER": "wrong-user",
            },
        ):
            service = CaseService(client, state_service=Mock())
            self.assertEqual(service.get_current_user(), "known-user")
            client._token = None
            client.get_token.side_effect = api.AuthenticationError("SSO unavailable")
            self.assertEqual(service.get_current_user(local_only=True), "known-user")
        self.assertEqual(client.get_token.call_count, 1)


class TuiAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_render_and_local_actions_do_not_reauthenticate_or_exit(self):
        from rh_support_lib.models import Case, CaseDetails
        from rh_support_lib.tui.app import SupportApp

        service = Mock()
        service.list_cases.return_value = []
        service.state_service.get_tracked_cases.return_value = {}

        def user(local_only=False):
            if not local_only:
                raise api.AuthenticationError("SSO unavailable")
            return "known-user"

        service.get_current_user.side_effect = user
        with (
            patch("rh_support_lib.tui.app.CaseService", return_value=service),
            patch("rh_support_lib.tui.app.AttachmentService"),
        ):
            app = SupportApp(
                "test-token", config={"tui": {"poll_interval": 0}}, layout="horizontal"
            )
            async with app.run_test() as pilot:
                await app.workers.wait_for_complete()
                service.get_current_user.reset_mock()
                app.selected_case_id = "1"
                app.render_case_details(
                    CaseDetails(Case("1", "Case", "Open", "Low"), "description"), []
                )
                await pilot.pause()
                with patch.object(app, "run_worker"):
                    app.action_toggle_follow()
                    app.action_mark_read()
                    app.action_toggle_layout()
                self.assertTrue(service.get_current_user.called)
                for call in service.get_current_user.call_args_list:
                    self.assertEqual(call.kwargs, {"local_only": True})


class RequestReplayTests(unittest.TestCase):
    def setUp(self):
        self.client = api.RedHatAPIClient()
        self.client._token = "old"
        self.client._offline_token = "credential"
        patcher = patch.object(self.client, "get_token", side_effect=["old", "new"])
        self.acquire = patcher.start()
        self.addCleanup(patcher.stop)
        self.old, self.final = Mock(status_code=401), Mock(status_code=200)

    def test_data_rewinds_to_initial_offset_and_preserves_headers(self):
        body = io.BytesIO(b"prefix-payload")
        body.seek(7)
        seen = []
        headers = {"X-Test": "kept"}

        def send(*args, **kwargs):
            seen.append(kwargs["data"].read())
            return self.old if len(seen) == 1 else self.final

        with patch.object(api.requests, "request", side_effect=send):
            result = self.client.post("https://invalid", data=body, headers=headers)
        self.assertIs(result, self.final)
        self.assertEqual(seen, [b"payload", b"payload"])
        self.assertEqual(headers, {"X-Test": "kept"})
        self.old.close.assert_called_once()
        self.final.close.assert_not_called()

    def test_multipart_handles_and_request_file_tuples_rewind(self):
        for tuple_size in (0, 2, 3, 4):
            with self.subTest(tuple_size=tuple_size):
                self.acquire.side_effect = ["old", "new"]
                body = io.BytesIO(b"prefix-payload")
                body.seek(7)
                value = body
                if tuple_size:
                    value = ("name", body, "text/plain", {"X-Test": "yes"})[:tuple_size]
                seen = []

                def send(*args, body=body, seen=seen, **kwargs):
                    seen.append(body.read())
                    return self.old if len(seen) == 1 else self.final

                with patch.object(api.requests, "request", side_effect=send):
                    self.client.post("https://invalid", files=[("upload", value)])
                self.assertEqual(seen, [b"payload", b"payload"])

    def test_simple_payloads_retry_only_once(self):
        for data in (None, b"body", "body", {"key": "value"}, [("key", "value")]):
            with self.subTest(data=data):
                self.acquire.side_effect = ["old", "new"]
                with patch.object(
                    api.requests, "request", return_value=self.old
                ) as send:
                    self.assertIs(
                        self.client.post("https://invalid", data=data), self.old
                    )
                    self.assertEqual(send.call_count, 2)

    def test_requests_multipart_encoding_preserves_content_on_retry(self):
        body = io.BytesIO(b"prefix-payload")
        body.seek(7)
        bodies = []

        def send(session, prepared, **kwargs):
            bodies.append(prepared.body)
            response = api.requests.Response()
            response.status_code = 401 if len(bodies) == 1 else 200
            response._content = b""
            response._content_consumed = True
            return response

        with (
            patch.object(api.requests.sessions.Session, "send", new=send),
            patch.object(api.requests.sessions, "get_netrc_auth", return_value=None),
        ):
            result = self.client.post(
                "https://invalid", files={"upload": ("name", body, "text/plain")}
            )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(bodies), 2)
        for encoded in bodies:
            self.assertIn(b"\r\npayload\r\n", encoded)
            self.assertNotIn(b"prefix-", encoded)

    def test_nonreplayable_bodies_stop_before_refresh_without_consumption(self):
        class Nonseekable:
            def read(self):
                raise AssertionError("must not read for replay inspection")

        class FailedTell(Nonseekable):
            def tell(self):
                raise OSError("secret https://private")

            def seek(self, position):
                raise AssertionError("must not seek after tell failed")

        def chunks():
            raise AssertionError("must not consume for replay inspection")
            yield b"body"

        for kwargs in (
            {"data": chunks()},
            {"data": Nonseekable()},
            {"data": FailedTell()},
            {"data": {"field": chunks()}},
            {"files": {"upload": ("name", Nonseekable(), "text/plain")}},
            {"files": chunks()},
        ):
            with self.subTest(kwargs=kwargs):
                self.acquire.reset_mock(side_effect=True)
                self.acquire.side_effect = ["old", "new"]
                with patch.object(
                    api.requests, "request", return_value=self.old
                ) as send:
                    with self.assertRaises(api.RequestReplayError) as caught:
                        self.client.post("https://secret", **kwargs)
                    self.assertNotIn("secret", str(caught.exception))
                    self.assertEqual(send.call_count, 1)
                    self.assertEqual(self.acquire.call_count, 1)
        self.assertEqual(self.old.close.call_count, 6)

    def test_failed_rewind_stops_before_refresh(self):
        body = io.BytesIO(b"payload")

        def send(*args, **kwargs):
            body.close()
            return self.old

        with (
            patch.object(api.requests, "request", side_effect=send) as request,
            self.assertRaises(api.RequestReplayError),
        ):
            self.client.post("https://invalid", data=body)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.acquire.call_count, 1)
        self.old.close.assert_called_once()

    def test_nonreplayable_success_does_not_refresh(self):
        with patch.object(api.requests, "request", return_value=self.final) as send:
            self.assertIs(
                self.client.post("https://invalid", data=iter([b"body"])), self.final
            )
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.acquire.call_count, 1)

    def test_incorrect_seek_is_rejected_before_refresh(self):
        class IncorrectSeek(io.BytesIO):
            def seek(self, position):
                return self.tell()

        body = IncorrectSeek(b"payload")

        def send(*args, **kwargs):
            body.read()
            return self.old

        with (
            patch.object(api.requests, "request", side_effect=send) as request,
            self.assertRaises(api.RequestReplayError),
        ):
            self.client.post("https://invalid", data=body)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.acquire.call_count, 1)

    def test_debug_logging_does_not_read_or_iterate_stream_bodies(self):
        self.final.headers = {}

        class Unreadable:
            def read(self):
                raise AssertionError("logging must not read")

            def __iter__(self):
                raise AssertionError("logging must not iterate")

        with (
            patch.object(
                api.requests.sessions.Session, "request", return_value=self.final
            ) as original,
            patch.object(api.logging, "basicConfig"),
            patch.object(api.logging, "getLogger"),
        ):
            api.enable_debug_logging()
            session = api.requests.Session()
            for body in (Unreadable(), {"body": Unreadable()}):
                self.assertIs(
                    session.request("POST", "https://invalid", data=body, stream=True),
                    self.final,
                )
            self.assertEqual(original.call_count, 2)
