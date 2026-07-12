import http.server
import json
import pathlib
import re
import tempfile
import urllib.parse
from datetime import datetime, timezone
from rh_support_lib.synthetic_corpus.generator import generate_default_corpus


class StatefulMockHandler(http.server.BaseHTTPRequestHandler):
    """
    Stateful HTTP handler that acts as a mock Red Hat Support Portal.
    Reads and mutates data directly inside the active synthetic corpus directory.
    """

    # Class variables set by the server instantiator
    corpus_dir = None

    def log_message(self, format, *args):
        # Silence HTTP log noise on stdout/stderr, unless running under verbose debug
        pass

    def _send_json(self, data, status_code=200, headers=None):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _send_error(self, message, status_code=400):
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(message.encode("utf-8"))

    def _get_corpus_path(self):
        if not StatefulMockHandler.corpus_dir:
            # Fallback to temp directory if not explicitly initialized
            StatefulMockHandler.corpus_dir = tempfile.mkdtemp(
                prefix="rh-support-corpus-"
            )
        path = pathlib.Path(StatefulMockHandler.corpus_dir)
        # Automatically generate default synthetic corpus if empty/missing cases
        if not (path / "cases").exists() or not (path / "metadata").exists():
            generate_default_corpus(path)
        return path

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        corpus_path = self._get_corpus_path()

        # 1. SSO Token Endpoint
        if self.path == "/auth/token" or self.path.endswith(
            "/protocol/openid-connect/token"
        ):
            self._send_json(
                {
                    "access_token": "mock_access_token_123",
                    "refresh_token": "mock_refresh_token_123",
                    "expires_in": 300,
                }
            )
            return

        # Check Authorization (mocking real portal security behavior)
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_error("Unauthorized - Missing token", 401)
            return

        # 2. List Cases with Filters
        if self.path == "/cases/filter" or self.path.endswith("/cases/filter"):
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                self._send_error("Invalid JSON payload")
                return

            # Read all case files from disk
            cases_dir = corpus_path / "cases"
            cases = []
            if cases_dir.exists():
                for case_file in cases_dir.glob("*.json"):
                    try:
                        with open(case_file, "r") as f:
                            cases.append(json.load(f))
                    except Exception:
                        pass

            # Apply filters
            filtered_cases = []
            for c in cases:
                match = True

                # Account number filter
                if "accountNumber" in payload:
                    acc = str(payload["accountNumber"])
                    if str(c.get("accountNumber")) != acc:
                        match = False

                # Status filter (can be a single string or list of strings)
                if match and ("status" in payload or "statuses" in payload):
                    target_statuses = []
                    if "status" in payload:
                        status_val = payload["status"]
                        target_statuses = (
                            status_val if isinstance(status_val, list) else [status_val]
                        )
                    elif "statuses" in payload:
                        target_statuses = payload["statuses"]

                    # Normalize and compare
                    target_statuses = [s.lower() for s in target_statuses if s]
                    case_status = str(c.get("status", "")).lower()
                    if target_statuses and case_status not in target_statuses:
                        match = False

                # Severity filter
                if match and ("severity" in payload or "severities" in payload):
                    target_sevs = []
                    if "severity" in payload:
                        sev_val = payload["severity"]
                        target_sevs = (
                            sev_val if isinstance(sev_val, list) else [sev_val]
                        )
                    elif "severities" in payload:
                        target_sevs = payload["severities"]

                    target_sevs = [s.lower() for s in target_sevs if s]
                    case_sev = str(c.get("severity", "")).lower()
                    # Some payloads send mapped/unmapped severities. We check substrings/names
                    if target_sevs:
                        matched_sev = False
                        for ts in target_sevs:
                            if ts in case_sev or case_sev in ts:
                                matched_sev = True
                                break
                        if not matched_sev:
                            match = False

                # Owner SSO name filter
                if match and "ownerSSOName" in payload:
                    owner_sso = str(payload["ownerSSOName"]).lower()
                    c_owner_sso = ""
                    owner_obj = c.get("owner") or {}
                    if isinstance(owner_obj, dict):
                        c_owner_sso = str(owner_obj.get("ssoUsername", "")).lower()
                    if c_owner_sso != owner_sso:
                        match = False

                if match:
                    # Construct thin/summary case view similar to Red Hat list API behavior
                    filtered_cases.append(c)

            # Sort cases by caseNumber (reverse or normal) or createdDate
            filtered_cases.sort(key=lambda x: x.get("caseNumber", ""), reverse=True)
            self._send_json({"cases": filtered_cases})
            return

        # 3. Create Support Case
        if self.path == "/cases" or self.path.endswith("/cases"):
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                self._send_error("Invalid JSON payload")
                return

            required = ["product", "version", "summary", "description"]
            missing = [r for r in required if r not in payload]
            if missing:
                self._send_error(f"Missing required fields: {', '.join(missing)}")
                return

            # Determine next case number
            cases_dir = corpus_path / "cases"
            existing_numbers = []
            if cases_dir.exists():
                for case_file in cases_dir.glob("*.json"):
                    num_str = case_file.stem
                    if num_str.isdigit():
                        existing_numbers.append(int(num_str))
            next_num = max(existing_numbers) + 1 if existing_numbers else 1001
            case_num_str = str(next_num)

            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            new_case = {
                "caseNumber": case_num_str,
                "summary": payload["summary"],
                "description": payload["description"],
                "product": payload["product"],
                "version": payload["version"],
                "accountNumber": payload.get("accountNumber", "123456"),
                "status": "Waiting on Red Hat",
                "severity": payload.get("severity", "Normal"),
                "caseType": payload.get("caseType", "Standard"),
                "createdDate": now_str,
                "contact": {"name": "Jane Developer", "ssoUsername": "jdeveloper"},
                "owner": {
                    "name": "Red Hat Support Specialist",
                    "ssoUsername": "rh_spec",
                },
                "lastModifiedBy": "Jane Developer",
                "lastModifiedDate": now_str,
                "comments": [],
                "attachments": [],
                "notifiedusers": [],
            }

            # Persist to disk
            cases_dir.mkdir(parents=True, exist_ok=True)
            with open(cases_dir / f"{case_num_str}.json", "w") as f:
                json.dump(new_case, f, indent=2)

            self._send_json({"caseNumber": case_num_str}, status_code=201)
            return

        # 4. Comments Endpoint (POST /cases/{case}/comments)
        match_comment = re.search(r"/cases/(\d+)/comments$", self.path)
        if match_comment:
            case_num = match_comment.group(1)
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if not case_file.exists():
                self._send_error(f"Case {case_num} not found", 404)
                return

            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                self._send_error("Invalid JSON payload")
                return

            c_body = (
                payload.get("body") or payload.get("commentBody") or payload.get("text")
            )
            if not c_body:
                self._send_error("Missing comment body")
                return

            # Mutate case JSON
            with open(case_file, "r") as f:
                case_data = json.load(f)

            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            comment_id = f"c_{len(case_data.get('comments', [])) + 1}"
            new_comment = {
                "id": comment_id,
                "createdDate": now_str,
                "createdBy": "Customer",
                "isPublic": payload.get("isPublic", True),
                "body": c_body,
            }

            case_data.setdefault("comments", []).append(new_comment)
            case_data["lastModifiedDate"] = now_str
            case_data["lastModifiedBy"] = "Customer"

            # Optional status change if posted during comment
            if "status" in payload:
                case_data["status"] = payload["status"]

            with open(case_file, "w") as f:
                json.dump(case_data, f, indent=2)

            self._send_json({"id": comment_id}, status_code=201)
            return

        # 5. Attachments Endpoint (POST /cases/{case}/attachments)
        match_attach = re.search(r"/cases/(\d+)/attachments$", self.path)
        if match_attach:
            case_num = match_attach.group(1)
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if not case_file.exists():
                self._send_error(f"Case {case_num} not found", 404)
                return

            # In the mock, we simulate attachment addition
            with open(case_file, "r") as f:
                case_data = json.load(f)

            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            attach_id = f"a_{len(case_data.get('attachments', [])) + 1}"

            # Since multipart forms can be tedious to parse in BaseHTTPRequestHandler,
            # we simply extract filename from headers or default it
            filename = "mock_uploaded_file.txt"
            content_disp = self.headers.get("Content-Disposition", "")
            match_fn = re.search(r'filename="([^"]+)"', content_disp)
            if match_fn:
                filename = match_fn.group(1)

            new_attachment = {
                "id": attach_id,
                "name": filename,
                "size": len(body),
                "createdDate": now_str,
            }
            case_data.setdefault("attachments", []).append(new_attachment)
            case_data["lastModifiedDate"] = now_str
            case_data["lastModifiedBy"] = "Customer"

            with open(case_file, "w") as f:
                json.dump(case_data, f, indent=2)

            # Red Hat API returns Location header on 201 Created
            headers = {"Location": f"http://mock/attachment/{attach_id}"}
            self._send_json({"id": attach_id}, status_code=201, headers=headers)
            return

        # 6. Notified Users Endpoint (POST /cases/{case}/notifiedusers)
        match_users = re.search(r"/cases/(\d+)/notifiedusers$", self.path)
        if match_users:
            case_num = match_users.group(1)
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if not case_file.exists():
                self._send_error(f"Case {case_num} not found", 404)
                return

            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                self._send_error("Invalid JSON payload")
                return

            users_list = payload.get("user") or []
            if not isinstance(users_list, list):
                self._send_error("user field must be an array")
                return

            with open(case_file, "r") as f:
                case_data = json.load(f)

            existing_notified = case_data.setdefault("notifiedusers", [])
            existing_usernames = {
                u.get("ssoUsername", "").lower() for u in existing_notified if u
            }

            for u in users_list:
                sso = u.get("ssoUsername")
                if sso and sso.lower() not in existing_usernames:
                    existing_notified.append({"ssoUsername": sso})

            case_data["lastModifiedDate"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

            with open(case_file, "w") as f:
                json.dump(case_data, f, indent=2)

            self.send_response(201)
            self.end_headers()
            return

        self._send_error("Endpoint not found", 404)

    def do_GET(self):
        corpus_path = self._get_corpus_path()

        # Check Authorization
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_error("Unauthorized - Missing token", 401)
            return

        # 1. Metadata: Products
        if self.path == "/products" or self.path.endswith("/products"):
            with open(corpus_path / "metadata" / "products.json", "r") as f:
                self._send_json(json.load(f))
            return

        # 2. Metadata: Product Versions (/products/{product}/versions)
        match_versions = re.search(r"/products/([^/]+)/versions$", self.path)
        if match_versions:
            prod_code = urllib.parse.unquote(match_versions.group(1)).upper()
            versions_file = corpus_path / "metadata" / "versions" / f"{prod_code}.json"
            if versions_file.exists():
                with open(versions_file, "r") as f:
                    self._send_json(json.load(f))
            else:
                self._send_json([])  # Empty list if product not found
            return

        # 3. Metadata: Severities
        if self.path == "/values/severity" or self.path.endswith("/values/severity"):
            with open(corpus_path / "metadata" / "severities.json", "r") as f:
                self._send_json(json.load(f))
            return

        # 4. Metadata: Case Types
        if self.path == "/values/caseType" or self.path.endswith("/values/caseType"):
            with open(corpus_path / "metadata" / "case_types.json", "r") as f:
                self._send_json(json.load(f))
            return

        # 5. Fetch Case Comments (GET /cases/{case}/comments)
        match_comments = re.search(r"/cases/(\d+)/comments$", self.path)
        if match_comments:
            case_num = match_comments.group(1)
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if case_file.exists():
                with open(case_file, "r") as f:
                    case_data = json.load(f)
                self._send_json(case_data.get("comments", []))
            else:
                self._send_error(f"Case {case_num} not found", 404)
            return

        # 6. Fetch Single Case (GET /cases/{case})
        match_case = re.search(r"/cases/(\d+)$", self.path)
        if match_case:
            case_num = match_case.group(1)
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if case_file.exists():
                with open(case_file, "r") as f:
                    case_data = json.load(f)
                # Ensure comments are stripped if frontend requests separate comments fetch (optional)
                # Real Red Hat API show endpoint does not include comments by default
                case_view = case_data.copy()
                if "comments" in case_view:
                    del case_view["comments"]
                self._send_json(case_view)
            else:
                self._send_error(f"Case {case_num} not found", 404)
            return

        self._send_error("Endpoint not found", 404)

    def do_PUT(self):
        corpus_path = self._get_corpus_path()
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # Check Authorization
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_error("Unauthorized - Missing token", 401)
            return

        # 1. Update Case (PUT /cases/{case})
        match_case = re.search(r"/cases/(\d+)$", self.path)
        if match_case:
            case_num = match_case.group(1)
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if not case_file.exists():
                self._send_error(f"Case {case_num} not found", 404)
                return

            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                self._send_error("Invalid JSON payload")
                return

            with open(case_file, "r") as f:
                case_data = json.load(f)

            # Mutate allowed fields
            for key in ["status", "severity", "summary", "description"]:
                if key in payload:
                    case_data[key] = payload[key]

            case_data["lastModifiedDate"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            case_data["lastModifiedBy"] = "Customer"

            with open(case_file, "w") as f:
                json.dump(case_data, f, indent=2)

            self.send_response(200)
            self.end_headers()
            return

        self._send_error("Endpoint not found", 404)


def run_mock_server(corpus_dir=None, port=8080):
    """Starts the stateful mock server synchronously on the given port."""
    StatefulMockHandler.corpus_dir = corpus_dir
    server_address = ("", port)
    httpd = http.server.HTTPServer(server_address, StatefulMockHandler)
    print(f"Starting Stateful Red Hat Support Mock Portal on port {port}...")
    if corpus_dir:
        print(f"Using corpus directory: {corpus_dir}")
    else:
        print("Using transient in-memory temporary directory.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Stateful Mock Portal.")
        httpd.server_close()
