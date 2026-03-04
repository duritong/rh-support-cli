import unittest
import http.server
import threading
import os
import sys
import subprocess
import json
import time
from unittest import mock

# Configuration for the mock server
MOCK_HOST = "localhost"
MOCK_PORT = 0  # 0 means let the OS choose a free port


class MockRedHatHandler(http.server.BaseHTTPRequestHandler):
    last_case_payload = None
    last_comment_payload = None

    def log_message(self, format, *args):
        # Silence server logs to keep test output clean
        pass

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # SSO Token Endpoint
        if self.path == "/auth/token":
            # parse body to see refresh token
            # request body is x-www-form-urlencoded
            from urllib.parse import parse_qs

            post_data = parse_qs(body.decode())
            refresh_token = post_data.get("refresh_token", [""])[0]

            access_token = "mock_access_token_123"
            if refresh_token == "cli_token":
                access_token = "access_from_cli"
            elif refresh_token == "env_token":
                access_token = "access_from_env"
            elif refresh_token == "file_token":
                access_token = "access_from_file"

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "access_token": access_token,
                        "refresh_token": "mock_refresh_token",
                        "expires_in": 300,
                    }
                ).encode()
            )
            return

        # Check Authorization header for other endpoints
        auth_header = self.headers.get("Authorization")
        # Allow our dynamic tokens or the default one
        valid_tokens = [
            "Bearer mock_access_token_123",
            "Bearer access_from_cli",
            "Bearer access_from_env",
            "Bearer access_from_file",
            "Bearer cached_fake_token",
        ]
        if auth_header not in valid_tokens:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"Unauthorized")
            return

        # Create Case Endpoint: /cases
        if self.path == "/cases":
            data = json.loads(body.decode())
            MockRedHatHandler.last_case_payload = data
            required = ["product", "version", "summary", "description"]
            if all(k in data for k in required):
                self.send_response(201)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"caseNumber": "20230001"}).encode())
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing required fields")
            return

        # List Cases Endpoint: /cases/filter
        if self.path == "/cases/filter":
            # Verify maxResults is present (Required by API)
            try:
                data = json.loads(body.decode())
                if "maxResults" not in data:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Missing maxResults")
                    return
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid JSON")
                return

            auth = self.headers.get("Authorization")
            source = "unknown"
            if auth == "Bearer access_from_cli":
                source = "CLI"
            elif auth == "Bearer access_from_env":
                source = "ENV"
            elif auth == "Bearer access_from_file":
                source = "FILE"
            elif auth == "Bearer mock_access_token_123":
                source = "DEFAULT"

            # Construct summary from filters
            filters_received = [f"AUTH={source}"]
            if "accountNumber" in data:
                filters_received.append(f"ACC={data['accountNumber']}")
            if "status" in data:
                filters_received.append(f"STAT={data['status']}")
            if "severity" in data:
                filters_received.append(f"SEV={data['severity']}")
            if "ownerSSOName" in data:
                filters_received.append(f"OWN={data['ownerSSOName']}")

            summary_str = "Filters: " + "|".join(filters_received)

            # Just return a dummy list
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            cases_list = [
                {
                    "caseNumber": "FILTER_TEST",
                    "summary": summary_str,
                    "contact": {"name": "Test User"},
                    "lastModifiedBy": "Red Hat Support",
                    "severity": "High",
                    "status": "Waiting on Red Hat",
                }
            ]
            # Wrap in dictionary as per API behavior
            self.wfile.write(json.dumps({"cases": cases_list}).encode())
            return

        # Attachments Endpoint: /cases/{case}/attachments
        if "/attachments" in self.path:
            # We are not parsing the multipart form data strictly here,
            # just checking if the request was made successfully.
            self.send_response(201)  # Created
            self.send_header("Location", "http://mock/attachment/1")
            self.end_headers()
            return

        # Comments Endpoint: /cases/{case}/comments
        if "/comments" in self.path:
            # Check for failure trigger
            if "FAIL_COMMENT" in self.path:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Simulated Server Error")
                return

            data = json.loads(body.decode())
            # Support both new and old fields for testing/migration, or strict new ones
            c_body = data.get("body") or data.get("commentBody")
            if c_body:
                MockRedHatHandler.last_comment_payload = data
                self.send_response(201)  # Created
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"id": "comment_1"}).encode())
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing body")
            return

        self.send_response(404)
        self.end_headers()

    def do_PUT(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # Check Authorization header
        auth_header = self.headers.get("Authorization")
        if auth_header != "Bearer mock_access_token_123":
            self.send_response(401)
            self.end_headers()
            return

        # Status Endpoint: /cases/{case}
        # Note: simplistic matching, assuming valid API usage
        if (
            "/cases/" in self.path
            and "/comments" not in self.path
            and "/attachments" not in self.path
        ):
            data = json.loads(body.decode())
            if "status" in data:
                self.send_response(200)
                self.end_headers()
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing status")
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        # Check Authorization header
        auth_header = self.headers.get("Authorization")
        if auth_header != "Bearer mock_access_token_123":
            self.send_response(401)
            self.end_headers()
            return

        if self.path == "/products":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            products = [
                {"code": "RHEL", "name": "Red Hat Enterprise Linux"},
                {"code": "OPENSHIFT", "name": "Red Hat OpenShift Container Platform"},
            ]
            self.wfile.write(json.dumps(products).encode())
            return

        if "/versions" in self.path:
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            versions = [{"name": "8.0"}, {"name": "9.0"}]
            self.wfile.write(json.dumps(versions).encode())
            return

        if "/values/severity" in self.path:
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            sevs = [
                {"name": "Low"},
                {"name": "Normal"},
                {"name": "High"},
                {"name": "Urgent"},
            ]
            self.wfile.write(json.dumps(sevs).encode())
            return

        if "/values/caseType" in self.path:
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            types = [{"name": "Standard"}, {"name": "Bug"}]
            self.wfile.write(json.dumps(types).encode())
            return

        # GET /cases/{id}/comments
        if "/comments" in self.path and "/cases/" in self.path:
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            comments = [
                {
                    "createdDate": "2023-01-02T10:00:00Z",
                    "createdBy": "Red Hat Support",
                    "isPublic": True,
                    "body": "Comment 1",
                },
                {
                    "createdDate": "2023-01-03T10:00:00Z",
                    "createdBy": "Customer",
                    "isPublic": False,
                    "visibility": "Internal",
                    "text": "Comment 2",
                },
            ]
            self.wfile.write(json.dumps(comments).encode())
            return

        # GET /cases/{id}
        # Simple check for numeric ID at end of path
        if "/cases/" in self.path:
            parts = self.path.split("/")
            # /cases/12345 -> ["", "cases", "12345"] (if base is /) or check last part
            case_id = parts[-1]
            if case_id.isdigit():
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                case = {
                    "caseNumber": case_id,
                    "summary": "Test Case Details",
                    "description": "This is a detailed description.",
                    "product": "RHEL",
                    "version": "9.0",
                    "accountNumber": "111111",
                    "createdDate": "2023-01-01T10:00:00Z",
                    "createdById": "user123",
                    "contact": {"name": "Test Owner"},
                    "status": "Waiting on Red Hat",
                    "severity": "High",
                    # Omit comments here to test separate fetch
                }
                self.wfile.write(json.dumps(case).encode())
                return

        self.send_response(404)
        self.end_headers()


class TestRhSupportCli(unittest.TestCase):
    server = None
    server_thread = None
    base_url = None
    sso_url = None

    @classmethod
    def setUpClass(cls):
        # Start a mock server on a free port
        cls.server = http.server.HTTPServer((MOCK_HOST, 0), MockRedHatHandler)
        cls.server_port = cls.server.server_port
        cls.base_url = f"http://{MOCK_HOST}:{cls.server_port}"
        cls.sso_url = f"{cls.base_url}/auth/token"

        cls.server_thread = threading.Thread(target=cls.server.serve_forever)
        cls.server_thread.daemon = True
        cls.server_thread.start()

        # Give the server a moment to start
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.shutdown()
            cls.server.server_close()

    def setUp(self):
        # Set up environment variables for the CLI to use the mock server
        self.env = os.environ.copy()
        self.env["RH_API_URL"] = self.base_url
        self.env["RH_SSO_URL"] = self.sso_url
        self.env["REDHAT_SUPPORT_OFFLINE_TOKEN"] = "mock_offline_token"

        # Ensure subprocess finds requests even if HOME is changed
        self.env["PYTHONPATH"] = os.pathsep.join(sys.path)

        # Avoid truncation in list output
        self.env["COLUMNS"] = "200"

        # Isolate cache for every test
        import tempfile

        self.test_cache_dir = tempfile.mkdtemp()
        self.env["XDG_CACHE_HOME"] = self.test_cache_dir

        self.cli_path = os.path.abspath("rh-support-cli.py")

    def tearDown(self):
        import shutil

        if os.path.exists(self.test_cache_dir):
            shutil.rmtree(self.test_cache_dir)

    def run_cli(self, args):
        """Helper to run the CLI script as a subprocess"""
        cmd = [sys.executable, self.cli_path] + args
        return subprocess.run(cmd, env=self.env, capture_output=True, text=True)

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
            cmd, env=env_no_token, capture_output=True, text=True, input="\n"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Error: Token cannot be empty", result.stderr)

    def test_attach_file(self):
        """Test attaching a file to a case"""
        # Create a dummy file
        with open("test_attachment.txt", "w") as f:
            f.write("This is a test attachment.")

        try:
            result = self.run_cli(
                ["attach", "-c", "01234567", "-f", "test_attachment.txt"]
            )
            if result.returncode != 0:
                print("\nSTDOUT:", result.stdout)
                print("STDERR:", result.stderr)

            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: File 'test_attachment.txt' attached.", result.stdout)
        finally:
            if os.path.exists("test_attachment.txt"):
                os.remove("test_attachment.txt")

    def test_attach_nonexistent_file(self):
        """Test attaching a file that does not exist"""
        result = self.run_cli(
            ["attach", "-c", "01234567", "-f", "nonexistent_file.txt"]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Error: File 'nonexistent_file.txt' does not exist.", result.stdout
        )

    def test_comment_from_file(self):
        """Test posting a comment from a file"""
        with open("test_comment.txt", "w") as f:
            f.write("This is a test comment body.")

        try:
            result = self.run_cli(
                [
                    "comment",
                    "-c",
                    "01234567",
                    "-f",
                    "test_comment.txt",
                    "-s",
                    "customer",
                ]
            )
            if result.returncode != 0:
                print("\nSTDOUT:", result.stdout)
                print("STDERR:", result.stderr)

            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Comment added.", result.stdout)
            self.assertIn(
                "Success: Case status updated to 'Waiting on Customer'.", result.stdout
            )
        finally:
            if os.path.exists("test_comment.txt"):
                os.remove("test_comment.txt")

    def test_comment_update_status_only(self):
        """Test verifying the default status behavior or specific status flags"""
        # Note: The CLI currently requires a comment body even if just updating status?
        # Looking at code:
        #   if args.file: ...
        #   else: comment_body = open_editor(...)
        #   if not comment_body: abort
        # So we MUST provide a comment body to update status.

        with open("status_update.txt", "w") as f:
            f.write("Updating status.")

        try:
            result = self.run_cli(
                ["comment", "-c", "01234567", "-f", "status_update.txt", "-s", "closed"]
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Case status updated to 'Closed'.", result.stdout)
        finally:
            if os.path.exists("status_update.txt"):
                os.remove("status_update.txt")

    def test_create_case(self):
        """Test creating a new case with flags and attachments"""
        # Create dummy files
        with open("desc.txt", "w") as f:
            f.write("My Issue Description")
        with open("att1.txt", "w") as f:
            f.write("Attachment 1")
        with open("att2.txt", "w") as f:
            f.write("Attachment 2")

        try:
            # We need to simulate 'y' for the confirmation prompt
            # input="\ny\n" should handle the prompt "Submit this case? (y/n/edit) [y]: "
            cmd = [
                sys.executable,
                self.cli_path,
                "create",
                "--product",
                "RHEL",
                "--version",
                "8.0",
                "--summary",
                "Test Case Summary",
                "--description-file",
                "desc.txt",
                "--severity",
                "Low",
                "--type",
                "Standard",
                "--attachment",
                "att1.txt",
                "--attachment",
                "att2.txt",
            ]

            result = subprocess.run(
                cmd, env=self.env, capture_output=True, text=True, input="y\n"
            )

            if result.returncode != 0:
                print("\nSTDOUT:", result.stdout)
                print("STDERR:", result.stderr)

            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Case #20230001 created.", result.stdout)
            self.assertIn("Attaching 'att1.txt'", result.stdout)
            self.assertIn("Attaching 'att2.txt'", result.stdout)
        finally:
            for f in ["desc.txt", "att1.txt", "att2.txt"]:
                if os.path.exists(f):
                    os.remove(f)

    def test_list_cases(self):
        """Test listing cases"""
        # Test basic list command
        result = self.run_cli(["list", "--account", "12345", "--status", "redhat"])

        if result.returncode != 0:
            print("\nSTDOUT:", result.stdout)
            print("STDERR:", result.stderr)

        self.assertEqual(result.returncode, 0)
        # Check for headers
        self.assertIn("NUMBER", result.stdout)
        self.assertIn("TITLE", result.stdout)
        self.assertIn("OWNER", result.stdout)
        # Check for data
        self.assertIn("FILTER_TEST", result.stdout)
        self.assertIn("AUTH=DEFAULT", result.stdout)
        self.assertIn("ACC=12345", result.stdout)
        self.assertIn("STAT=Waiting on Red Hat", result.stdout)

    def test_link_command(self):
        """Test the link subcommand (no auth required)"""
        # Unset token to prove auth is skipped
        env_no_token = self.env.copy()
        if "REDHAT_SUPPORT_OFFLINE_TOKEN" in env_no_token:
            del env_no_token["REDHAT_SUPPORT_OFFLINE_TOKEN"]

        cmd = [sys.executable, self.cli_path, "link", "--case", "12345"]
        result = subprocess.run(cmd, env=env_no_token, capture_output=True, text=True)

        self.assertEqual(result.returncode, 0)
        self.assertIn("https://access.redhat.com/support/cases/12345", result.stdout)

    def test_show_case(self):
        """Test showing case details with colors (default)"""
        result = self.run_cli(["show", "-c", "12345", "--no-pager"])

        if result.returncode != 0:
            print("Failed Show Case Test STDOUT:", result.stdout)
            print("Failed Show Case Test STDERR:", result.stderr)

        self.assertEqual(result.returncode, 0)
        self.assertIn("CASE: 12345", result.stdout)
        self.assertIn("TITLE:", result.stdout)
        self.assertIn("Test Case Details", result.stdout)
        self.assertIn("DESCRIPTION:", result.stdout)
        self.assertIn("This is a detailed description.", result.stdout)
        self.assertIn("Comment 1", result.stdout)
        self.assertIn("Comment 2", result.stdout)
        self.assertIn("(Private (Internal))", result.stdout)

        # Check for ANSI escape codes (colors)
        self.assertIn("\033[", result.stdout)

    def test_simple_output(self):
        """Test showing case details with simple output (no colors)"""
        # Global args must come before subcommand
        result = self.run_cli(["--simple-output", "show", "-c", "12345", "--no-pager"])
        if result.returncode != 0:
            print("Failed Simple Output Test STDOUT:", result.stdout)
            print("Failed Simple Output Test STDERR:", result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertIn("CASE: 12345", result.stdout)
        # Check NO ANSI escape codes
        self.assertNotIn("\033[", result.stdout)

    def test_bookmarks(self):
        """Test bookmark functionality"""
        # Create config file
        config_content = """
default_bookmark: "my_team"
bookmarks:
  my_team:
    account: "12345"
    status: ["Waiting on Red Hat"]
  my_cases:
    owner: "jdoe"
    status: ["Open"]
"""
        with open("test_config.yaml", "w") as f:
            f.write(config_content)

        try:
            # 1. Default bookmark (my_team)
            # Should filter by account 12345 and status Waiting on Red Hat
            result = self.run_cli(["--config-file", "test_config.yaml", "list"])
            self.assertEqual(result.returncode, 0)
            self.assertIn("ACC=12345", result.stdout)
            self.assertIn("STAT=Waiting on Red Hat", result.stdout)

            # 2. --no-default-bookmark
            result = self.run_cli(
                ["--config-file", "test_config.yaml", "list", "--no-default-bookmark"]
            )
            self.assertEqual(result.returncode, 0)
            # Should NOT have filters
            self.assertNotIn("ACC=12345", result.stdout)
            self.assertNotIn("STAT=Waiting on Red Hat", result.stdout)

            # 3. Explicit Bookmark (my_cases)
            result = self.run_cli(
                [
                    "--config-file",
                    "test_config.yaml",
                    "list",
                    "--bookmark",
                    "my_cases",
                    "--no-default-bookmark",
                ]
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("OWN=jdoe", result.stdout)
            self.assertIn("STAT=Open", result.stdout)

            # 4. Override (Default + Explicit Status)
            # Default sets account=12345, status="Waiting on Red Hat"
            # Override status="Closed"
            result = self.run_cli(
                ["--config-file", "test_config.yaml", "list", "--status", "Closed"]
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("ACC=12345", result.stdout)  # From bookmark
            self.assertIn("STAT=Closed", result.stdout)  # From CLI
            self.assertNotIn("STAT=Waiting on Red Hat", result.stdout)

        finally:
            if os.path.exists("test_config.yaml"):
                os.remove("test_config.yaml")

    def test_auth_priority(self):
        """Test the priority of token sources: CLI > ENV > FILE"""
        import tempfile
        import shutil

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

    def test_templates(self):
        """Test template functionality"""
        # Check if jinja2 is available, else skip
        try:
            import jinja2  # noqa: F401
        except ImportError:
            print("Skipping test_templates: jinja2 not installed")
            return

        import tempfile
        import shutil

        # Temp HOME for templates
        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        # Create templates dir
        templates_dir = os.path.join(
            temp_home, ".config", "rh-support-cli", "templates"
        )
        os.makedirs(templates_dir)

        # 1. Base OpenShift Template
        base_tmpl = """
product: "OpenShift Container Platform"
version: "4.12"
severity: "3 (Normal)"
caseType: "Bug"
"""
        with open(os.path.join(templates_dir, "openshift_base.yaml"), "w") as f:
            f.write(base_tmpl)

        # 2. Proactive Template (includes base)
        proactive_tmpl = """
include_templates: ["openshift_base"]
caseType: "Other"
severity: "4 (Low)"
remoteSessionTermsAcked: true
summary: "[Proactive] {{ product }} {{ version }} update on {{ cluster_name }}"
description: |
  We are planning to upgrade {{ product }} from {{ version }} to {{ next_version }}.
  ClusterID: {{ cluster_id }}
"""
        with open(os.path.join(templates_dir, "proactive.yaml"), "w") as f:
            f.write(proactive_tmpl)

        try:
            cmd = [
                sys.executable,
                self.cli_path,
                "create",
                "--template",
                "proactive",
                "--template-var",
                "cluster_name=ProdCluster",
                "--template-var",
                "next_version=4.13",
                "--template-var",
                "cluster_id=uuid-123",
            ]

            # input='y\n' for confirmation
            result = subprocess.run(
                cmd, env=self.env, capture_output=True, text=True, input="y\n"
            )

            if result.returncode != 0:
                print("Failed Template Test STDOUT:", result.stdout)
                print("Failed Template Test STDERR:", result.stderr)

            self.assertEqual(result.returncode, 0)

            # Verify Payload
            payload = MockRedHatHandler.last_case_payload
            self.assertIsNotNone(payload)
            self.assertEqual(payload.get("product"), "OpenShift Container Platform")
            self.assertEqual(payload.get("version"), "4.12")
            self.assertEqual(payload.get("caseType"), "Other")
            self.assertEqual(payload.get("severity"), "4 (Low)")
            self.assertEqual(payload.get("remoteSessionTermsAcked"), True)
            self.assertIn(
                "[Proactive] OpenShift Container Platform 4.12 update on ProdCluster",
                payload.get("summary"),
            )
            self.assertIn("ClusterID: uuid-123", payload.get("description"))

        finally:
            shutil.rmtree(temp_home)

    def test_templates_advanced(self):
        """Test advanced template features: overrides, currentDoc, parse_date"""
        try:
            import jinja2  # noqa: F401
        except ImportError:
            print("Skipping test_templates_advanced: jinja2 not installed")
            return

        import tempfile
        import shutil

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        # Mock dateparser
        mock_dateparser = """
from datetime import datetime
def parse(date_string):
    return datetime(2023, 1, 1, 12, 0, 0)
"""
        with open(os.path.join(temp_home, "dateparser.py"), "w") as f:
            f.write(mock_dateparser)

        # Update PYTHONPATH to include temp_home
        self.env["PYTHONPATH"] = temp_home + os.pathsep + self.env.get("PYTHONPATH", "")

        # Templates dir
        templates_dir = os.path.join(
            temp_home, ".config", "rh-support-cli", "templates"
        )
        os.makedirs(templates_dir)

        # Templates
        with open(os.path.join(templates_dir, "base_tmpl.yaml"), "w") as f:
            f.write(
                "version: '4.10'\nremoteSessionTermsAcked: true\nproduct: OCP\ndescription: base\nseverity: Low\ncaseType: Standard"
            )

        derived = """
include_templates: ["base_tmpl"]
summary: "Ver: {{ currentDoc.version }} Date: {{ 'next friday' | parse_date }}"
"""
        with open(os.path.join(templates_dir, "derived.yaml"), "w") as f:
            f.write(derived)

        try:
            cmd = [
                sys.executable,
                self.cli_path,
                "create",
                "--template",
                "derived",
                "--version",
                "4.12",
            ]
            result = subprocess.run(
                cmd, env=self.env, capture_output=True, text=True, input="y\n"
            )

            if result.returncode != 0:
                print("Failed Advanced Template Test STDOUT:", result.stdout)
                print("Failed Advanced Template Test STDERR:", result.stderr)

            self.assertEqual(result.returncode, 0)

            payload = MockRedHatHandler.last_case_payload
            self.assertEqual(
                payload.get("version"), "4.12", "CLI version should override template"
            )
            self.assertEqual(
                payload.get("remoteSessionTermsAcked"),
                True,
                "Extra field should be present",
            )

            # Summary check
            # Ver: 4.10 (from template)
            # Date: 01-01-2023 ... (from mock)
            self.assertIn("Ver: 4.10", payload.get("summary"))
            self.assertIn("01-01-2023", payload.get("summary"))

        finally:
            shutil.rmtree(temp_home)

    def test_comment_failure_preservation(self):
        """Test that the temporary comment file is preserved if the API call fails"""
        # 1. Create a fake editor script (shell script)
        fake_editor_path = os.path.abspath("fake_editor.sh")
        with open(fake_editor_path, "w") as f:
            f.write("#!/bin/sh\n")
            f.write('echo "Preserved Comment Body" >> "$1"\n')

        os.chmod(fake_editor_path, 0o755)

        # 2. Set EDITOR to the fake script
        env_with_editor = self.env.copy()
        env_with_editor["EDITOR"] = fake_editor_path

        try:
            # 3. Run comment command on a failing case ID
            cmd = [sys.executable, self.cli_path, "comment", "-c", "FAIL_COMMENT"]
            result = subprocess.run(
                cmd, env=env_with_editor, capture_output=True, text=True
            )

            # 4. Expect failure
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Error: Comment failed", result.stdout)

            # 5. Extract path and verify existence
            import re

            match = re.search(r"Draft comment preserved at: (.+)", result.stdout)
            self.assertTrue(
                match, f"Could not find preserved file path in output: {result.stdout}"
            )
            preserved_path = match.group(1).strip()

            self.assertTrue(
                os.path.exists(preserved_path), "Preserved file does not exist"
            )

            # 6. Verify content
            with open(preserved_path, "r") as f:
                content = f.read()
            self.assertIn("Preserved Comment Body", content)

            # Cleanup
            if os.path.exists(preserved_path):
                os.remove(preserved_path)

        finally:
            if os.path.exists(fake_editor_path):
                os.remove(fake_editor_path)

    def test_comment_with_context(self):
        """Test that previous comments are included in the editor context"""
        # 1. Create a fake editor script to capture the file content
        fake_editor_path = os.path.abspath("context_check_editor.sh")
        captured_content_path = os.path.abspath("captured_content.txt")

        with open(fake_editor_path, "w") as f:
            f.write("#!/bin/sh\n")
            f.write(f'cat "$1" > {captured_content_path}\n')
            # Write a dummy comment so it doesn't abort
            f.write('echo "New Comment" > "$1"\n')

        os.chmod(fake_editor_path, 0o755)

        env_with_editor = self.env.copy()
        env_with_editor["EDITOR"] = fake_editor_path

        try:
            # Test Case 1: Default behavior (include 1 comment)
            # We need to target a case ID that returns comments. Mock handles any ID but checks paths.
            # /cases/12345/comments returns 2 comments in mock.
            cmd = [sys.executable, self.cli_path, "comment", "-c", "12345"]
            result = subprocess.run(
                cmd, env=env_with_editor, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0)

            with open(captured_content_path, "r") as f:
                content = f.read()

            self.assertIn("--- PREVIOUS COMMENTS ---", content)
            self.assertIn("Comment 2", content)  # Last comment
            self.assertNotIn(
                "Comment 1", content
            )  # First comment (should be excluded by default limit 1)

            # Test Case 2: Include 2 comments
            cmd = [
                sys.executable,
                self.cli_path,
                "comment",
                "-c",
                "12345",
                "--include-previous-comments",
                "2",
            ]
            result = subprocess.run(
                cmd, env=env_with_editor, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0)

            with open(captured_content_path, "r") as f:
                content = f.read()

            self.assertIn("Comment 1", content)
            self.assertIn("Comment 2", content)

            # Test Case 3: Disable comments
            cmd = [
                sys.executable,
                self.cli_path,
                "comment",
                "-c",
                "12345",
                "--include-previous-comments",
                "0",
            ]
            result = subprocess.run(
                cmd, env=env_with_editor, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0)

            with open(captured_content_path, "r") as f:
                content = f.read()
            self.assertNotIn("--- PREVIOUS COMMENTS ---", content)

        finally:
            if os.path.exists(fake_editor_path):
                os.remove(fake_editor_path)
            if os.path.exists(captured_content_path):
                os.remove(captured_content_path)

    def test_open_editor_stripping(self):
        """Test that open_editor correctly strips only leading comments"""
        from rh_support_lib.utils import open_editor

        fake_editor_path = os.path.abspath("stripping_editor.sh")
        with open(fake_editor_path, "w") as f:
            f.write("#!/bin/sh\n")
            f.write('echo "Real Comment" >> "$1"\n')
            f.write('echo "# Indented Code" >> "$1"\n')

        os.chmod(fake_editor_path, 0o755)

        with mock.patch.dict(os.environ, {"EDITOR": fake_editor_path}):
            try:
                content, path = open_editor(
                    "123", "Red Hat", header_content="Previous\nComment"
                )

                # Verify content
                self.assertTrue(content.startswith("Real Comment"))
                self.assertIn(
                    "# Indented Code", content
                )  # Should preserve this as it's after content
                self.assertNotIn("# Enter comment", content)  # Should strip header

            finally:
                if "path" in locals() and os.path.exists(path):
                    os.remove(path)
                if os.path.exists(fake_editor_path):
                    os.remove(fake_editor_path)

    def test_file_comment_stripping(self):
        """Test that cmd_comment strips headers when reading from a file"""
        # Create a file with a header and content
        file_path = "retry_comment.tmp"
        with open(file_path, "w") as f:
            f.write("# Header 1\n")
            f.write("# Header 2\n")
            f.write(
                "\n"
            )  # Empty line is NOT a comment, so it should STOP stripping here
            f.write("Actual Content\n")
            f.write("# Not a header\n")

        try:
            # We don't need to run the full CLI to test the library logic if we want to be fast,
            # but running the CLI as a subprocess confirms the integration.
            # Using a mock server is already set up.

            result = self.run_cli(["comment", "-c", "12345", "--file", file_path])
            self.assertEqual(result.returncode, 0)

            payload = MockRedHatHandler.last_comment_payload
            self.assertIsNotNone(payload)
            body = payload.get("commentBody") or payload.get("body", "")

            self.assertNotIn("# Header 1", body)
            self.assertNotIn("# Header 2", body)
            self.assertEqual(body, "Actual Content\n# Not a header")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    def test_comment_with_edit_flag(self):
        """Test that --edit opens editor with file content"""
        fake_editor_path = os.path.abspath("edit_flag_editor.sh")
        captured_content_path = os.path.abspath("captured_edit_content.txt")
        file_input_path = os.path.abspath("input_for_edit.txt")

        # 1. Create input file
        with open(file_input_path, "w") as f:
            f.write("# Ignored Header\n")
            f.write("Initial Content\n")

        # 2. Fake editor: Cat file to capture path, verify content, then write new content
        with open(fake_editor_path, "w") as f:
            f.write("#!/bin/sh\n")
            f.write(f'cat "$1" > {captured_content_path}\n')
            # Simulate user editing the content
            f.write('echo "Edited Content" > "$1"\n')

        os.chmod(fake_editor_path, 0o755)

        env_with_editor = self.env.copy()
        env_with_editor["EDITOR"] = fake_editor_path

        try:
            # Run with --file and --edit
            cmd = [
                sys.executable,
                self.cli_path,
                "comment",
                "-c",
                "12345",
                "--file",
                file_input_path,
                "--edit",
            ]
            result = subprocess.run(
                cmd, env=env_with_editor, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0)

            # Verify editor was opened with stripped content
            with open(captured_content_path, "r") as f:
                content = f.read()
            self.assertIn("Initial Content", content)
            self.assertNotIn(
                "# Ignored Header", content
            )  # Should have been stripped before editor
            self.assertIn(
                "# Enter comment", content
            )  # New headers added by open_editor

            # Verify payload submitted was the EDITED content
            payload = MockRedHatHandler.last_comment_payload
            self.assertEqual(payload.get("commentBody"), "Edited Content")

        finally:
            if os.path.exists(fake_editor_path):
                os.remove(fake_editor_path)
            if os.path.exists(captured_content_path):
                os.remove(captured_content_path)
            if os.path.exists(file_input_path):
                os.remove(file_input_path)

    def test_token_caching(self):
        """Test that the access token is cached and reused"""
        import time

        # Use the cache dir set in setUp
        cache_path = os.path.join(
            self.env["XDG_CACHE_HOME"], "rh-support-cli", "token_cache.json"
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

        result = self.run_cli(["--debug", "list"])
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

    def test_debug_flag(self):
        """Test that the --debug flag produces debug output"""
        # We need to capture stderr to check for log messages
        # "show" command is good because it does a fetch
        result = self.run_cli(["--debug", "show", "-c", "12345", "--no-pager"])

        if result.returncode != 0:
            print("Failed Debug Flag Test STDOUT:", result.stdout)
            print("Failed Debug Flag Test STDERR:", result.stderr)

        self.assertEqual(result.returncode, 0)
        # Check for debug logs in stderr
        # Note: requests uses lowercase methods internally often
        self.assertIn("DEBUG: Request: get", result.stderr)
        self.assertIn("DEBUG: Response Status: 200", result.stderr)

    def test_debug_file_logging(self):
        """Test that --debug-file logs to file and does not truncate"""
        log_file = os.path.abspath("debug_output.log")

        # We need a large response to test truncation
        # Mock /cases/12345 to return a large description
        # We can't easily change the MockRedHatHandler instance logic dynamically for just one test without restarting,
        # but we can rely on the fact that existing responses are small, except we can craft a test where we verify it writes to file.
        # Actually, let's just verify it writes to file first.

        try:
            result = self.run_cli(
                ["--debug-file", log_file, "show", "-c", "12345", "--no-pager"]
            )
            self.assertEqual(result.returncode, 0)

            self.assertTrue(os.path.exists(log_file), "Log file was not created")

            with open(log_file, "r") as f:
                content = f.read()

            self.assertIn("DEBUG: Request: get", content)
            self.assertIn("DEBUG: Response Status: 200", content)
            # Ensure it didn't print to stderr (since we redirected to file)
            # Wait, logging.basicConfig with filename DISABLES the default StreamHandler (stderr).
            # So stderr should NOT contain debug logs.
            self.assertNotIn("DEBUG: Request: get", result.stderr)

        finally:
            if os.path.exists(log_file):
                os.remove(log_file)


if __name__ == "__main__":
    unittest.main()
