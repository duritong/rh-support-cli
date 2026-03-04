import os
import sys
import getpass
import json
import time
import pathlib

try:
    import requests
except ImportError:
    print("Error: The 'requests' library is required.")
    print("Please install it using: pip install requests")
    sys.exit(1)

import logging
import requests.sessions


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

    # Store the original request method
    original_request = requests.sessions.Session.request

    def debug_request(self, method, url, *args, **kwargs):
        logger.debug(f"Request: {method} {url}")

        # Log Headers
        headers = kwargs.get("headers", {})
        # Mask authorization header if present
        log_headers = headers.copy()
        if "Authorization" in log_headers:
            log_headers["Authorization"] = "Bearer <HIDDEN>"
        logger.debug(f"Request Headers: {log_headers}")

        # Log Body
        if "json" in kwargs:
            logger.debug(f"Request Body (JSON): {kwargs['json']}")
        elif "data" in kwargs:
            # Mask refresh token in body
            log_data = (
                kwargs["data"].copy()
                if isinstance(kwargs["data"], dict)
                else kwargs["data"]
            )
            if isinstance(log_data, dict) and "refresh_token" in log_data:
                log_data["refresh_token"] = "<HIDDEN>"
            logger.debug(f"Request Body (Data): {log_data}")

        try:
            response = original_request(self, method, url, *args, **kwargs)

            logger.debug(f"Response Status: {response.status_code}")
            logger.debug(f"Response Headers: {dict(response.headers)}")

            # Log Response Body
            try:
                content = None

                # Check for sensitive content in JSON response
                try:
                    if response.headers.get("Content-Type", "").startswith(
                        "application/json"
                    ):
                        resp_json = response.json()
                        if isinstance(resp_json, dict) and (
                            "access_token" in resp_json or "refresh_token" in resp_json
                        ):
                            masked_json = resp_json.copy()
                            if "access_token" in masked_json:
                                masked_json["access_token"] = "<HIDDEN>"
                            if "refresh_token" in masked_json:
                                masked_json["refresh_token"] = "<HIDDEN>"
                            content = json.dumps(masked_json)
                except Exception:
                    pass

                if content is None:
                    content = response.text
                    if truncate_response and len(content) > 1000:
                        content = content[:1000] + "... (truncated)"

                logger.debug(f"Response Body: {content}")
            except Exception:
                logger.debug("Response Body: (Binary or non-text)")

            return response

        except Exception as e:
            logger.error(f"Request Failed: {e}")
            raise

    # Patch the method
    requests.sessions.Session.request = debug_request


from rh_support_lib.constants import SSO_URL  # noqa: E402


def get_access_token(token_file_arg=None):
    """
    Retrieves the access token using the offline token.
    Priority:
    1. CLI Argument (--token-file)
    2. Environment Variable (REDHAT_SUPPORT_OFFLINE_TOKEN)
    3. Config File (~/.config/rh-support-cli/token)
    4. Interactive Prompt
    """
    offline_token = None

    # 1. CLI Argument
    if token_file_arg:
        if not os.path.isfile(token_file_arg):
            sys.exit(f"Error: Token file '{token_file_arg}' not found.")
        try:
            with open(token_file_arg, "r") as f:
                offline_token = f.read().strip()
        except Exception as e:
            sys.exit(f"Error reading token file: {e}")

    # 2. Environment Variable
    if not offline_token:
        offline_token = os.environ.get("REDHAT_SUPPORT_OFFLINE_TOKEN")

    # 3. Config File
    if not offline_token:
        config_path = os.path.expanduser("~/.config/rh-support-cli/token")
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r") as f:
                    offline_token = f.read().strip()
            except Exception as e:
                print(f"Warning: Failed to read token from {config_path}: {e}")

    # 4. Prompt
    if not offline_token:
        if sys.stdin.isatty():
            offline_token = getpass.getpass("Please enter your Red Hat Offline Token: ")
        else:
            print("No 'REDHAT_SUPPORT_OFFLINE_TOKEN' environment variable found.")

    if not offline_token:
        sys.exit("Error: Token cannot be empty.")

    # 5. Check Cache
    cache_dir = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    token_cache_dir = os.path.join(cache_dir, "rh-support-cli")
    token_cache_file = os.path.join(token_cache_dir, "token_cache.json")

    if os.path.exists(token_cache_file):
        try:
            with open(token_cache_file, "r") as f:
                cache_data = json.load(f)

            expiry = cache_data.get("expires_at", 0)
            # Check if token is valid (with 30s buffer)
            if time.time() < (expiry - 30):
                # cache hit
                return cache_data.get("access_token")
        except Exception:
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

        access_token = data.get("access_token")
        expires_in = data.get("expires_in", 300)  # Default 5 min if missing
        refresh_token = data.get("refresh_token")

        # Save to cache
        try:
            pathlib.Path(token_cache_dir).mkdir(parents=True, exist_ok=True)
            # Ensure private permissions
            os.chmod(token_cache_dir, 0o700)

            cache_data = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": time.time() + expires_in,
            }

            with open(token_cache_file, "w") as f:
                # Set file permissions before writing if possible, or after
                os.fchmod(f.fileno(), 0o600)
                json.dump(cache_data, f)
        except Exception:
            # warn but don't fail
            # print(f"Warning: Failed to write token cache: {e}")
            pass

        return access_token

    except requests.exceptions.RequestException as e:
        print("Error: Failed to authenticate.")
        if e.response is not None:
            print(f"SSO Response: {e.response.text}")
        sys.exit(1)


def get_json(url, token):
    try:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"Warning: Failed to fetch metadata from {url}: {e}")
        return []
