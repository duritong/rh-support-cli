from __future__ import annotations

import getpass
import hashlib
import json
import logging
import math
import os
import pathlib
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
import requests.sessions

from rh_support_lib.constants import SSO_URL


def _redact_debug_value(value):
    sensitive = {
        "authorization",
        "proxyauthorization",
        "cookie",
        "setcookie",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "password",
        "clientsecret",
        "presignedurl",
        "downloadurl",
    }
    if isinstance(value, dict):
        return {
            key: "Bearer <HIDDEN>"
            if str(key).lower() == "authorization"
            else "<HIDDEN>"
            if str(key).lower().replace("_", "").replace("-", "") in sensitive
            else _redact_debug_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_debug_value(item) for item in value]
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        try:
            parsed = urlsplit(value)
            query = parse_qsl(parsed.query, keep_blank_values=True)
            if any(
                key.lower().startswith("x-amz-")
                or any(
                    word in key.lower()
                    for word in ("signature", "token", "credential", "accesskey")
                )
                for key, _ in query
            ):
                parsed = parsed._replace(
                    query=urlencode([(key, "<HIDDEN>") for key, _ in query])
                )
            if parsed.username is not None:
                parsed = parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1])
            return urlunsplit(parsed)
        except ValueError:
            return "<HIDDEN URL>"
    return value


def enable_debug_logging(log_file=None):
    if log_file:
        logging.basicConfig(
            filename=log_file, level=logging.DEBUG, format="%(levelname)s: %(message)s"
        )
        truncate_response = False
    else:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
        truncate_response = True

    logger = logging.getLogger("rh-support-cli.debug")
    # urllib3's DEBUG request lines include unredacted signed query strings.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # Store the original request method
    original_request = requests.sessions.Session.request

    def debug_request(self, method, url, *args, **kwargs):
        logger.debug("Request: %s %s", method, _redact_debug_value(url))

        # Log Headers
        headers = kwargs.get("headers", {})
        logger.debug("Request Headers: %s", _redact_debug_value(dict(headers)))

        # Log Body
        if "json" in kwargs:
            logger.debug("Request Body (JSON): %s", _redact_debug_value(kwargs["json"]))
        elif "data" in kwargs:
            logger.debug(
                "Request Body (Data): %s",
                _redact_debug_value(kwargs["data"])
                if isinstance(kwargs["data"], dict)
                else "<non-JSON request body omitted>",
            )

        try:
            response = original_request(self, method, url, *args, **kwargs)

            logger.debug(f"Response Status: {response.status_code}")
            logger.debug(
                "Response Headers: %s", _redact_debug_value(dict(response.headers))
            )

            if kwargs.get("stream"):
                logger.debug("Response Body: <streamed response body omitted>")
                return response

            # Log Response Body
            try:
                content = None

                # Check for sensitive content in JSON response
                try:
                    if response.headers.get("Content-Type", "").startswith(
                        "application/json"
                    ):
                        content = json.dumps(_redact_debug_value(response.json()))
                except (json.JSONDecodeError, TypeError):
                    pass

                if content is None:
                    content = response.text
                if truncate_response and len(content) > 1000:
                    content = content[:1000] + "... (truncated)"

                logger.debug(f"Response Body: {content}")
            except Exception:  # noqa: BLE001
                # Broad exception catch is intentional to prevent logging errors from crashing requests
                logger.debug("Response Body: (Binary or non-text)")

            return response

        except Exception as e:
            logger.error("Request Failed: %s", type(e).__name__)
            raise

    # Patch the method
    requests.sessions.Session.request = debug_request


def _get_token_cache_file():
    cache_dir = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    token_cache_dir = os.path.join(cache_dir, "rh-support-cli")
    cache_filename = "token_cache.json"
    if os.environ.get("RH_SSO_URL") or os.environ.get("RH_API_URL"):
        cache_filename = "token_cache_mock.json"
    return token_cache_dir, os.path.join(token_cache_dir, cache_filename)


class AuthenticationError(Exception):
    """Credential selection or SSO authentication failed safely."""


class RequestReplayError(Exception):
    """An unauthorized request cannot safely replay its body."""


def _get_offline_token(token_file_arg=None, interactive=True):
    """
    Resolves an offline credential without exchanging it for an access token.
    Priority:
    1. CLI Argument (--token-file)
    2. Environment Variable (REDHAT_SUPPORT_OFFLINE_TOKEN)
    3. Config File (~/.config/rh-support-cli/token)
    4. Interactive Prompt
    """
    try:
        # A selected source must never silently fall back to another identity.
        config_path = os.path.expanduser("~/.config/rh-support-cli/token")
        if token_file_arg is not None:
            with open(token_file_arg, "r") as f:
                offline_token = f.read().strip()
        elif "REDHAT_SUPPORT_OFFLINE_TOKEN" in os.environ:
            offline_token = os.environ["REDHAT_SUPPORT_OFFLINE_TOKEN"].strip()
        elif os.path.exists(config_path):
            with open(config_path, "r") as f:
                offline_token = f.read().strip()
        elif interactive and sys.stdin.isatty():
            offline_token = getpass.getpass("Please enter your Red Hat Offline Token: ")
        else:
            offline_token = None
    except (OSError, UnicodeError, ValueError, EOFError):
        if not interactive:
            return None
        raise AuthenticationError(
            "Unable to read the selected offline token."
        ) from None

    if not isinstance(offline_token, str) or not offline_token.strip():
        if not interactive:
            return None
        raise AuthenticationError("An offline token is required.") from None
    return offline_token.strip()


def get_access_token(token_file_arg=None, offline_token=None):
    """Exchanges the selected offline credential, reusing only its bound token cache."""
    if offline_token is None:
        offline_token = _get_offline_token(token_file_arg)
    if not isinstance(offline_token, str) or not offline_token.strip():
        raise AuthenticationError("An offline token is required.") from None

    # 5. Check Cache
    token_cache_dir, token_cache_file = _get_token_cache_file()
    credential_context = hashlib.sha256(
        json.dumps(
            {
                "offline_token": offline_token,
                "sso_url": SSO_URL,
                "client_id": "rhsm-api",
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    if os.path.exists(token_cache_file):
        try:
            with open(token_cache_file, "r") as f:
                cache_data = json.load(f)

            expiry = cache_data.get("expires_at", 0)
            # Check if token is valid (with 30s buffer)
            if cache_data.get("credential_context") == credential_context and (
                not isinstance(expiry, bool)
                and isinstance(expiry, (int, float))
                and math.isfinite(expiry)
                and time.time() < (expiry - 30)
            ):
                # cache hit
                access_token = cache_data.get("access_token")
                # Ensure the cached token is valid (contains 2 periods for real OIDC JWT, or we are in mock/custom SSO mode)
                if (
                    isinstance(access_token, str)
                    and access_token
                    and not any(
                        character.isspace() or ord(character) < 32
                        for character in access_token
                    )
                    and (
                        access_token.count(".") == 2
                        or os.environ.get("RH_SSO_URL")
                        or os.environ.get("RH_API_URL")
                    )
                ):
                    return access_token
        except (OSError, ValueError, TypeError, AttributeError, OverflowError):
            # ignore cache errors
            pass

    payload = {
        "grant_type": "refresh_token",
        "client_id": "rhsm-api",
        "refresh_token": offline_token,
    }

    try:
        response = requests.post(SSO_URL, data=payload, timeout=30)
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, dict):
            raise AuthenticationError(
                "SSO returned an invalid token response."
            ) from None
        access_token = data.get("access_token")
        expires_in = data.get("expires_in")
        if (
            not isinstance(access_token, str)
            or not access_token
            or any(
                character.isspace() or ord(character) < 32 for character in access_token
            )
            or isinstance(expires_in, bool)
            or not isinstance(expires_in, (int, float))
            or expires_in <= 0
            or not math.isfinite(expires_in)
            or not math.isfinite(time.time() + expires_in)
        ):
            raise AuthenticationError(
                "SSO returned an invalid token response."
            ) from None
        refresh_token = data.get("refresh_token")

        # Save to cache
        temporary_path = None
        try:
            pathlib.Path(token_cache_dir).mkdir(parents=True, exist_ok=True)
            # Ensure private permissions
            os.chmod(token_cache_dir, 0o700)

            cache_data = {
                "credential_context": credential_context,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": time.time() + expires_in,
            }

            # Publish a complete credential/token pair, never an in-place update.
            with tempfile.NamedTemporaryFile(
                mode="w", dir=token_cache_dir, prefix=".token_cache-", delete=False
            ) as f:
                temporary_path = f.name
                os.fchmod(f.fileno(), 0o600)
                json.dump(cache_data, f)
            os.replace(temporary_path, token_cache_file)
        except (OSError, TypeError):
            # warn but don't fail
            # print(f"Warning: Failed to write token cache: {e}")
            pass
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

        return access_token

    except (requests.exceptions.RequestException, ValueError, OverflowError):
        raise AuthenticationError("Failed to authenticate with SSO.") from None


def get_json(url, token):
    try:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"Warning: Failed to fetch metadata from {url}: {e}")
        return []


def extract_jwt_username(token: str | None, sso_only: bool = False) -> str | None:
    if not token or token.count(".") < 2:
        return None
    import base64
    import contextlib
    import json

    with contextlib.suppress(Exception):
        payload_part = token.split(".")[1]
        padded = payload_part + "=" * ((4 - len(payload_part) % 4) % 4)
        claims = json.loads(
            base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        )
        if isinstance(claims, dict):
            username = claims.get("preferred_username") or claims.get("username")
            return username or (
                None if sso_only else claims.get("email") or claims.get("sub")
            )
    return None


class RedHatAPIClient:
    """OIDC Client for Red Hat Support Case API with transparent token refreshing."""

    def __init__(
        self, token_file_arg: str | None = None, token_file: str | None = None
    ):
        self.token_file_arg = token_file if token_file is not None else token_file_arg
        self._token = None
        self._offline_token = None
        self._auth_lock = threading.RLock()
        self._token_generation = 0

    def get_cache_scope(self, backend: str) -> str | None:
        """Select private content locally, without prompting or authenticating."""
        with self._auth_lock:
            if self._offline_token is None:
                self._offline_token = _get_offline_token(
                    self.token_file_arg, interactive=False
                )
            if not self._offline_token:
                return None
            return hashlib.sha256(
                json.dumps(
                    ["offline", self._offline_token, SSO_URL, backend],
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()

    def get_token(self, force_refresh=False):
        with self._auth_lock:
            if force_refresh:
                _, token_cache_file = _get_token_cache_file()
                if os.path.exists(token_cache_file):
                    try:
                        os.remove(token_cache_file)
                    except OSError:
                        pass
                self._token = None

            if not self._token:
                if self._offline_token is None:
                    self._offline_token = _get_offline_token(self.token_file_arg)
                self._token = get_access_token(self.token_file_arg, self._offline_token)
                self._token_generation += 1
            return self._token

    def request(self, method, url, **kwargs):
        positions = []
        replayable = True

        def capture(body):
            nonlocal replayable
            if hasattr(body, "read"):
                try:
                    position = body.tell()
                    if (
                        not isinstance(position, int)
                        or position < 0
                        or not callable(getattr(body, "seek", None))
                    ):
                        replayable = False
                    else:
                        positions.append((body, position))
                except Exception:  # noqa: BLE001 - custom streams may fail arbitrarily
                    replayable = False
            elif isinstance(body, dict):
                for value in body.values():
                    capture(value)
            elif isinstance(body, (list, tuple)):
                for value in body:
                    capture(value)
            elif not isinstance(body, (type(None), bytes, str, int, float, bool)):
                replayable = False

        # Snapshot offsets before requests can consume streams; never buffer bodies.
        capture(kwargs.get("data"))
        files = kwargs.get("files")
        if isinstance(files, (dict, list, tuple)):
            items = files.items() if isinstance(files, dict) else files
            for item in items:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    replayable = False
                    continue
                value = item[1]
                if isinstance(value, (list, tuple)):
                    if not 2 <= len(value) <= 4:
                        replayable = False
                        continue
                    value = value[1]
                capture(value)
        elif files is not None:
            replayable = False

        headers = dict(kwargs.get("headers") or {})
        with self._auth_lock:
            token = self.get_token()
            generation = self._token_generation
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Accept", "application/json")
        kwargs["headers"] = headers
        kwargs.setdefault("timeout", 30)

        resp = requests.request(method, url, **kwargs)
        if resp.status_code == 401:
            resp.close()
            if not replayable:
                raise RequestReplayError(
                    "Cannot retry unauthorized request: body is not replayable."
                ) from None
            try:
                for body, position in positions:
                    body.seek(position)
                    if body.tell() != position:
                        raise ValueError("Body position was not restored.")
            except Exception:  # noqa: BLE001 - never expose stream exception details
                raise RequestReplayError(
                    "Cannot retry unauthorized request: body could not be rewound."
                ) from None
            # Another in-flight request may already have refreshed this token.
            with self._auth_lock:
                token = self.get_token(
                    force_refresh=self._token_generation == generation
                )
            headers["Authorization"] = f"Bearer {token}"
            resp = requests.request(method, url, **kwargs)
        return resp

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def get_json(self, url, **kwargs):
        resp = self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def execute_graphql(
        self, query: str, variables: dict | None = None, url: str | None = None
    ) -> dict:
        import rh_support_lib.constants

        target_url = url or rh_support_lib.constants.GRAPHQL_URL
        headers = {
            "Content-Type": "application/json",
            "apollographql-client-name": "rh-support-cli",
            "apollographql-client-version": "1.0.0",
        }
        payload = {"query": query, "variables": variables or {}}
        resp = self.post(target_url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            import html

            error_msgs = [
                html.unescape(str(err.get("message", str(err))))
                for err in data["errors"]
            ]
            raise RuntimeError(f"GraphQL Error: {'; '.join(error_msgs)}")
        return data.get("data", {})


class LegacyAPIClient:
    """A wrapper to adapt a raw legacy token string to the RedHatAPIClient interface."""

    def __init__(self, token):
        self.token = token

    def get_cache_scope(self, backend: str) -> str | None:
        if not isinstance(self.token, str) or not self.token:
            return None
        return hashlib.sha256(
            json.dumps(["access", self.token, backend]).encode("utf-8")
        ).hexdigest()

    def get_token(self, force_refresh=False):
        return self.token

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        headers["Authorization"] = f"Bearer {self.token}"
        headers.setdefault("Accept", "application/json")
        kwargs["headers"] = headers
        kwargs.setdefault("timeout", 30)
        return requests.request(method, url, **kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def get_json(self, url, **kwargs):
        resp = self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def execute_graphql(
        self, query: str, variables: dict | None = None, url: str | None = None
    ) -> dict:
        import rh_support_lib.constants

        target_url = url or rh_support_lib.constants.GRAPHQL_URL
        headers = {
            "Content-Type": "application/json",
            "apollographql-client-name": "rh-support-cli",
            "apollographql-client-version": "1.0.0",
        }
        payload = {"query": query, "variables": variables or {}}
        resp = self.post(target_url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            import html

            error_msgs = [
                html.unescape(str(err.get("message", str(err))))
                for err in data["errors"]
            ]
            raise RuntimeError(f"GraphQL Error: {'; '.join(error_msgs)}")
        return data.get("data", {})
