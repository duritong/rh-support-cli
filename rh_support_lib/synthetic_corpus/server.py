import http.server
import json
import pathlib
import re
import tempfile
from datetime import datetime, timezone
from typing import ClassVar

from rh_support_lib.synthetic_corpus.generator import generate_default_corpus


class StatefulMockHandler(http.server.BaseHTTPRequestHandler):
    """
    Stateful HTTP handler that acts as a mock Red Hat Support Portal.
    Supports both GraphQL API (redhat_support_uiapi) and Attachment Service REST API.
    Reads and mutates data directly inside the active synthetic corpus directory.
    """

    corpus_dir = None
    mock_storage: ClassVar[dict] = {}

    def log_message(self, format, *args):
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
            StatefulMockHandler.corpus_dir = tempfile.mkdtemp(
                prefix="rh-support-corpus-"
            )
        path = pathlib.Path(StatefulMockHandler.corpus_dir)
        if not (path / "cases").exists() or not (path / "metadata").exists():
            generate_default_corpus(path)
        return path

    def _load_all_cases(self, corpus_path):
        cases_dir = corpus_path / "cases"
        cases = []
        if cases_dir.exists():
            for case_file in sorted(cases_dir.glob("*.json")):
                import contextlib

                with (
                    contextlib.suppress(OSError, json.JSONDecodeError),
                    open(case_file, "r") as f,
                ):
                    cases.append(json.load(f))
        return cases

    def _handle_graphql(self, payload, corpus_path):
        query = payload.get("query", "")
        variables = payload.get("variables") or {}

        # 1. GetActiveProducts
        if "GetActiveProducts" in query or "RedHatSupportProduct2" in query:
            products_file = corpus_path / "metadata" / "products.json"
            products = []
            if products_file.exists():
                with open(products_file, "r") as f:
                    products = json.load(f)
            edges = []
            for p in products:
                edges.append(
                    {
                        "node": {
                            "Id": f"prod_{p.get('name')}",
                            "Name": {"value": p.get("name")},
                            "VersionName__c": {"value": "9.0"},
                            "Family": {"value": "Linux"},
                            "ParentProduct__c": {"value": None},
                        },
                        "cursor": f"cur_{p.get('name')}",
                    }
                )
            return {
                "data": {
                    "redhat_support_uiapi": {
                        "query": {
                            "RedHatSupportProduct2": {
                                "totalCount": len(edges),
                                "edges": edges,
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            }

        # 2. ResolveCaseLookupData
        if "ResolveCaseLookupData" in query:
            return {
                "data": {
                    "redhat_support_uiapi": {
                        "Contact": {
                            "RedHatSupportContact": {
                                "edges": [
                                    {
                                        "node": {
                                            "Id": "003ContactMockId",
                                            "AccountId": {"value": "001AccountMockId"},
                                        }
                                    }
                                ]
                            }
                        },
                        "Account": {
                            "RedHatSupportAccount": {
                                "edges": [{"node": {"Id": "001AccountMockId"}}]
                            }
                        },
                        "RecordType": {
                            "RedHatSupportRecordType": {
                                "edges": [{"node": {"Id": "012RecordTypeMockId"}}]
                            }
                        },
                        "Product": {
                            "RedHatSupportProduct2": {
                                "edges": [
                                    {
                                        "node": {
                                            "Id": "01tProductMockId",
                                            "Name": {
                                                "value": variables.get(
                                                    "productName", "RHEL"
                                                )
                                            },
                                            "VersionName__c": {
                                                "value": variables.get(
                                                    "versionName", "9.0"
                                                )
                                            },
                                        }
                                    }
                                ]
                            }
                        },
                    }
                }
            }

        # 3. CreateNewCase (Mutation)
        if "CreateNewCase" in query or "CaseCreate" in query:
            case_input = variables.get("input", {}).get("RedHatSupportCase", {})
            cases = self._load_all_cases(corpus_path)
            existing_numbers = []
            for c in cases:
                num = str(c.get("caseNumber", ""))
                if num.isdigit():
                    existing_numbers.append(int(num))
            next_num = max(existing_numbers) + 1 if existing_numbers else 1001
            case_num_str = str(next_num)
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            new_case = {
                "caseNumber": case_num_str,
                "id": f"500{case_num_str}",
                "summary": case_input.get("Subject", "Untitled Case"),
                "description": case_input.get("Description", ""),
                "product": case_input.get("ProductId", "RHEL"),
                "version": case_input.get("Version", "9.0"),
                "accountNumber": "123456",
                "status": case_input.get("Status", "Waiting on Red Hat"),
                "severity": case_input.get("Priority", "3 (Normal)"),
                "caseType": "Standard",
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
            with open(corpus_path / "cases" / f"{case_num_str}.json", "w") as f:
                json.dump(new_case, f, indent=2)

            return {
                "data": {
                    "redhat_support_uiapi": {
                        "CaseCreate": {
                            "RedHatSupportRecord": {
                                "Id": f"500{case_num_str}",
                                "CaseNumber__c": {"value": case_num_str},
                                "Subject": {"value": new_case["summary"]},
                                "Status": {"value": new_case["status"]},
                                "CreatedDate": {"value": now_str},
                            }
                        }
                    }
                }
            }

        # 4. UpdateCase (Mutation)
        if "UpdateCase" in query or "CaseUpdate" in query:
            update_input = variables.get("input", {})
            record_id = update_input.get("Id", "")
            patch_case = update_input.get("RedHatSupportCase", {})

            # Find case by Id or CaseNumber
            cases = self._load_all_cases(corpus_path)
            matched_case = None
            for c in cases:
                if (
                    str(c.get("id")) == str(record_id)
                    or f"500{c.get('caseNumber')}" == str(record_id)
                    or str(c.get("caseNumber")) == str(record_id)
                ):
                    matched_case = c
                    break

            if matched_case:
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                for k, v in patch_case.items():
                    if k == "Status":
                        matched_case["status"] = v
                    elif k == "Priority":
                        matched_case["severity"] = v
                    elif k == "Subject":
                        matched_case["summary"] = v
                    elif k == "Description":
                        matched_case["description"] = v
                matched_case["lastModifiedDate"] = now_str
                case_num_str = str(matched_case["caseNumber"])
                with open(corpus_path / "cases" / f"{case_num_str}.json", "w") as f:
                    json.dump(matched_case, f, indent=2)

                return {
                    "data": {
                        "redhat_support_uiapi": {
                            "CaseUpdate": {
                                "RedHatSupportRecord": {
                                    "Id": str(record_id),
                                    "CaseNumber__c": {"value": case_num_str},
                                    "Status": {"value": matched_case.get("status")},
                                    "Priority": {"value": matched_case.get("severity")},
                                    "LastModifiedDate": {"value": now_str},
                                }
                            }
                        }
                    }
                }
            return {"errors": [{"message": f"Case record {record_id} not found"}]}

        # 5. CreateCaseComment (Mutation)
        if "CreateCaseComment" in query or "CaseComment__cCreate" in query:
            comment_input = variables.get("input", {}).get(
                "RedHatSupportCaseComment__c", {}
            )
            case_ref = comment_input.get("Case__c", "")
            c_body = comment_input.get("Body__c", "")

            cases = self._load_all_cases(corpus_path)
            matched_case = None
            for c in cases:
                if (
                    str(c.get("id")) == str(case_ref)
                    or f"500{c.get('caseNumber')}" == str(case_ref)
                    or str(c.get("caseNumber")) == str(case_ref)
                ):
                    matched_case = c
                    break

            if matched_case:
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                comment_id = f"c_{len(matched_case.get('comments', [])) + 1}"
                new_comment = {
                    "id": comment_id,
                    "createdDate": now_str,
                    "createdBy": "Jane Developer",
                    "isPublic": comment_input.get("Visibility__c", "Public")
                    == "Public",
                    "body": c_body,
                }
                matched_case.setdefault("comments", []).append(new_comment)
                matched_case["lastModifiedDate"] = now_str
                case_num_str = str(matched_case["caseNumber"])
                with open(corpus_path / "cases" / f"{case_num_str}.json", "w") as f:
                    json.dump(matched_case, f, indent=2)

                return {
                    "data": {
                        "redhat_support_uiapi": {
                            "CaseComment__cCreate": {
                                "RedHatSupportRecord": {
                                    "Id": comment_id,
                                    "Body__c": {"value": c_body},
                                    "Case__c": {"value": case_ref},
                                    "CreatedDate": {"value": now_str},
                                }
                            }
                        }
                    }
                }
            return {"errors": [{"message": f"Case record {case_ref} not found"}]}

        # 6. GetCaseAndComments (Query Single Case)
        if "GetCaseAndComments" in query or (
            "CaseNumber__c" in query and "CaseComments__r" in query
        ):
            case_num = variables.get("caseNumber") or ""
            cases = self._load_all_cases(corpus_path)
            matched_case = None
            for c in cases:
                if str(c.get("caseNumber")) == str(case_num):
                    matched_case = c
                    break
            if not matched_case:
                return {
                    "data": {
                        "redhat_support_uiapi": {
                            "query": {"RedHatSupportCase": {"edges": []}}
                        }
                    }
                }

            comment_edges = []
            for com in matched_case.get("comments", []):
                comment_edges.append(
                    {
                        "node": {
                            "Id": com.get("id", "c1"),
                            "Body__c": {"value": com.get("body", "")},
                            "LastModifiedByName__c": {
                                "value": com.get("createdBy", "Support Specialist")
                            },
                            "IsAssociate__c": {
                                "value": com.get("createdBy") != "Jane Developer"
                            },
                            "IsCustomer__c": {
                                "value": com.get("createdBy") == "Jane Developer"
                            },
                            "CreatedDate": {"value": com.get("createdDate", "")},
                        },
                        "cursor": f"cur_{com.get('id')}",
                    }
                )

            node = {
                "Id": matched_case.get("id", f"500{matched_case.get('caseNumber')}"),
                "CaseNumber__c": {"value": str(matched_case.get("caseNumber"))},
                "Subject": {"value": matched_case.get("summary", "")},
                "Status": {"value": matched_case.get("status", "")},
                "Priority": {"value": matched_case.get("severity", "")},
                "Type": {"value": matched_case.get("caseType", "Standard")},
                "Description": {"value": matched_case.get("description", "")},
                "Product": {
                    "Id": f"prod_{matched_case.get('product')}",
                    "Name": {"value": matched_case.get("product", "")},
                    "ParentProduct__r": {
                        "Id": "parent_prod",
                        "Name": {"value": matched_case.get("product", "")},
                    },
                },
                "RedHatSupportAccount": {
                    "Id": "acc_1",
                    "Name": {"value": "Acme Corp"},
                    "AccountNumber": {
                        "value": str(matched_case.get("accountNumber", "123456"))
                    },
                },
                "RedHatSupportContact": {
                    "Id": "cont_1",
                    "Name": {
                        "value": matched_case.get("contact", {}).get(
                            "name", "Jane Developer"
                        )
                    },
                    "Email": {"value": "jane@example.com"},
                    "Phone": {"value": "+1-555-0100"},
                    "SSOUserName__c": {
                        "value": matched_case.get("contact", {}).get(
                            "ssoUsername", "jdeveloper"
                        )
                    },
                },
                "Owner": {
                    "Id": "owner_1",
                    "Name": {
                        "value": matched_case.get("owner", {}).get(
                            "name", "Red Hat Support"
                        )
                    },
                },
                "CreatedDate": {"value": matched_case.get("createdDate", "")},
                "LastModifiedDate": {"value": matched_case.get("lastModifiedDate", "")},
                "LastModifiedBy": {
                    "Name": {"value": matched_case.get("lastModifiedBy", "System")}
                },
                "CaseComments__r": {
                    "edges": comment_edges,
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                },
            }

            return {
                "data": {
                    "redhat_support_uiapi": {
                        "query": {
                            "RedHatSupportCase": {
                                "edges": [{"node": node, "cursor": "cur_1"}]
                            }
                        }
                    }
                }
            }

        # 7. GetCasesByFilters / GetSupportCases
        where_clause = variables.get("where") or {}
        cases = self._load_all_cases(corpus_path)
        filtered_cases = []

        for c in cases:
            match = True
            # Check where clause filters
            if "Status" in where_clause:
                stat_filter = where_clause["Status"]
                c_status = str(c.get("status", "")).lower()
                if isinstance(stat_filter, dict):
                    if (
                        "eq" in stat_filter
                        and c_status != str(stat_filter["eq"]).lower()
                    ):
                        match = False
                    if (
                        "ne" in stat_filter
                        and c_status == str(stat_filter["ne"]).lower()
                    ):
                        match = False
                    if "in" in stat_filter:
                        allowed = [str(x).lower() for x in stat_filter["in"]]
                        if c_status not in allowed:
                            match = False
                elif isinstance(stat_filter, str) and c_status != stat_filter.lower():
                    match = False

            if match and "Priority" in where_clause:
                prio_filter = where_clause["Priority"]
                c_prio = str(c.get("severity", "")).lower()
                if isinstance(prio_filter, dict):
                    if "in" in prio_filter:
                        allowed = [str(x).lower() for x in prio_filter["in"]]
                        # Match against severity strings (e.g., 'high', '2 (high)')
                        matched_prio = False
                        for a in allowed:
                            if a in c_prio or c_prio in a:
                                matched_prio = True
                                break
                        if not matched_prio:
                            match = False
                    elif "eq" in prio_filter:
                        eq_val = str(prio_filter["eq"]).lower()
                        if eq_val not in c_prio and c_prio not in eq_val:
                            match = False

            if match and "RedHatSupportAccount" in where_clause:
                acc_clause = where_clause["RedHatSupportAccount"]
                if isinstance(acc_clause, dict) and "AccountNumber" in acc_clause:
                    acc_filter = acc_clause["AccountNumber"].get("eq")
                    if acc_filter and str(c.get("accountNumber")) != str(acc_filter):
                        match = False

            if match:
                filtered_cases.append(c)

        filtered_cases.sort(key=lambda x: str(x.get("caseNumber", "")), reverse=True)

        edges = []
        for c in filtered_cases:
            edges.append(
                {
                    "node": {
                        "Id": c.get("id", f"500{c.get('caseNumber')}"),
                        "CaseNumber__c": {"value": str(c.get("caseNumber"))},
                        "Subject": {"value": c.get("summary", "")},
                        "Status": {"value": c.get("status", "")},
                        "Priority": {"value": c.get("severity", "")},
                        "CreatedDate": {"value": c.get("createdDate", "")},
                        "LastModifiedDate": {"value": c.get("lastModifiedDate", "")},
                    },
                    "cursor": f"cur_{c.get('caseNumber')}",
                }
            )

        return {
            "data": {
                "redhat_support_uiapi": {
                    "query": {
                        "RedHatSupportCase": {
                            "edges": edges,
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        }

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

        # Check Authorization
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_error("Unauthorized - Missing token", 401)
            return

        # 2. GraphQL Endpoint
        if (
            self.path == "/graphql"
            or self.path == "/"
            or (
                self.path.startswith("/graphql")
                or body.startswith(b'{"query"')
                or b'"query"' in body[:100]
            )
        ):
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except json.JSONDecodeError:
                self._send_error("Invalid JSON payload")
                return
            if "query" in payload:
                gql_response = self._handle_graphql(payload, corpus_path)
                self._send_json(gql_response)
                return

        # 3. Attachment Upload Initiation (POST /v3/cases/attachments/upload)
        if (
            "/attachments/upload" in self.path
            and not self.path.endswith("/complete")
            and not self.path.endswith("/abort")
        ):
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_error("Invalid JSON payload")
                return

            filename = payload.get("fileName")
            filesize = payload.get("fileSize", 0)
            case_number = payload.get("caseNumber")
            description = payload.get("description", "")

            if not filename or not case_number:
                self._send_error("fileName and caseNumber are required", 400)
                return

            # Generate attachment ID
            attach_uuid = f"att_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{len(filename)}"
            host = self.headers.get("Host", "127.0.0.1:8080")

            # Store attachment record
            case_file = corpus_path / "cases" / f"{case_number}.json"
            if case_file.exists():
                with open(case_file, "r") as f:
                    case_data = json.load(f)
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                new_att = {
                    "caseNumber": str(case_number),
                    "uuid": attach_uuid,
                    "fileName": filename,
                    "size": filesize,
                    "sizeKB": round(filesize / 1024.0, 1),
                    "createdDate": now_str,
                    "createdBy": "Jane Developer",
                    "description": description,
                    "link": f"http://{host}/v3/cases/attachments/downloadFile/{case_number}/{attach_uuid}",
                    "uploadStatus": "Completed",
                }
                case_data.setdefault("attachments", []).append(new_att)
                case_data["lastModifiedDate"] = now_str
                with open(case_file, "w") as f:
                    json.dump(case_data, f, indent=2)

            # Multipart vs Single PUT
            if filesize > 5368709120:  # > 5 GB
                upload_id = f"upload_{attach_uuid}"
                parts = [
                    {
                        "partNumber": 1,
                        "presignedUrl": f"http://{host}/mock-storage/{attach_uuid}?partNumber=1",
                    },
                    {
                        "partNumber": 2,
                        "presignedUrl": f"http://{host}/mock-storage/{attach_uuid}?partNumber=2",
                    },
                ]
                self._send_json(
                    {
                        "attachmentId": attach_uuid,
                        "uploadStrategy": "MULTIPART",
                        "uploadId": upload_id,
                        "expirationTime": "2026-12-31T23:59:59.000Z",
                        "parts": parts,
                    }
                )
            else:
                self._send_json(
                    {
                        "attachmentId": attach_uuid,
                        "uploadStrategy": "SINGLE_PUT",
                        "presignedUrl": f"http://{host}/mock-storage/{attach_uuid}",
                        "expirationTime": "2026-12-31T23:59:59.000Z",
                    }
                )
            return

        # 4. Attachment Upload Complete (POST /v3/cases/attachments/upload/complete)
        if "/attachments/upload/complete" in self.path:
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_error("Invalid JSON payload")
                return
            attach_uuid = payload.get("attachmentId")
            self._send_json(
                {
                    "status": "success",
                    "attachmentId": attach_uuid,
                    "message": "Upload completed. File processing in progress.",
                }
            )
            return

        # Legacy endpoints fallback for existing tests / backward-compatibility
        if self.path == "/cases/filter" or self.path.endswith("/cases/filter"):
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except json.JSONDecodeError:
                self._send_error("Invalid JSON payload")
                return
            cases = self._load_all_cases(corpus_path)
            self._send_json({"cases": cases})
            return

        if self.path == "/cases" or self.path.endswith("/cases"):
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_error("Invalid JSON payload")
                return
            cases = self._load_all_cases(corpus_path)
            existing_numbers = [
                int(c["caseNumber"])
                for c in cases
                if str(c.get("caseNumber", "")).isdigit()
            ]
            next_num = max(existing_numbers) + 1 if existing_numbers else 1001
            case_num_str = str(next_num)
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            new_case = {
                "caseNumber": case_num_str,
                "summary": payload.get("summary", ""),
                "description": payload.get("description", ""),
                "product": payload.get("product", "RHEL"),
                "version": payload.get("version", "9.0"),
                "accountNumber": "123456",
                "status": "Waiting on Red Hat",
                "severity": payload.get("severity", "Normal"),
                "caseType": "Standard",
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
            with open(corpus_path / "cases" / f"{case_num_str}.json", "w") as f:
                json.dump(new_case, f, indent=2)
            self._send_json({"caseNumber": case_num_str}, status_code=201)
            return

        match_comment = re.search(r"/cases/(\d+)/comments$", self.path)
        if match_comment:
            case_num = match_comment.group(1)
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if case_file.exists():
                try:
                    payload = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    payload = {}
                c_body = (
                    payload.get("body")
                    or payload.get("commentBody")
                    or payload.get("text")
                    or ""
                )
                with open(case_file, "r") as f:
                    case_data = json.load(f)
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                comment_id = f"c_{len(case_data.get('comments', [])) + 1}"
                case_data.setdefault("comments", []).append(
                    {
                        "id": comment_id,
                        "createdDate": now_str,
                        "createdBy": "Jane Developer",
                        "isPublic": True,
                        "body": c_body,
                    }
                )
                case_data["lastModifiedDate"] = now_str
                with open(case_file, "w") as f:
                    json.dump(case_data, f, indent=2)
                self._send_json({"id": comment_id}, status_code=201)
            else:
                self._send_error("Case not found", 404)
            return

        match_users = re.search(r"/cases/(\d+)/notifiedusers$", self.path)
        if match_users:
            case_num = match_users.group(1)
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if not case_file.exists():
                self._send_error(f"Case {case_num} not found", 404)
                return
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except json.JSONDecodeError:
                payload = {}
            users_list = payload.get("user") or []
            with open(case_file, "r") as f:
                case_data = json.load(f)
            existing = case_data.setdefault("notifiedusers", [])
            existing_ssos = {
                (u.get("ssoUsername", "") if isinstance(u, dict) else str(u)).lower()
                for u in existing
            }
            for u in users_list:
                sso = u.get("ssoUsername") if isinstance(u, dict) else str(u)
                if sso and sso.lower() not in existing_ssos:
                    existing.append({"ssoUsername": sso})
            case_data["lastModifiedDate"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            with open(case_file, "w") as f:
                json.dump(case_data, f, indent=2)
            self._send_json({"status": "success"}, status_code=201)
            return

        match_attach = re.search(r"/cases/(\d+)/attachments$", self.path)
        if match_attach:
            self._send_json({"id": "a_mock"}, status_code=201)
            return

    def do_GET(self):
        corpus_path = self._get_corpus_path()

        # Storage download mock (presigned URL download)
        if self.path.startswith("/mock-storage/"):
            attach_id = self.path.split("/mock-storage/")[1].split("?")[0]
            data = StatefulMockHandler.mock_storage.get(attach_id, b"mock file content")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # Check Authorization
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_error("Unauthorized - Missing token", 401)
            return

        # Attachment Download URL endpoint: GET /v3/cases/attachments/downloadFile/{caseNumber}/{attachmentId}
        match_download = re.search(
            r"/attachments/downloadFile/([^/]+)/([^/]+)$", self.path
        )
        if match_download:
            case_num = match_download.group(1)
            att_id = match_download.group(2)
            host = self.headers.get("Host", "127.0.0.1:8080")
            self._send_json(
                {
                    "downloadUrl": f"http://{host}/mock-storage/{att_id}",
                    "fileName": "downloaded_file.txt",
                    "expiresInSeconds": "3600",
                }
            )
            return

        # Attachment Status for Single Attachment: GET /v3/cases/{caseNumber}/attachments/{attachmentId}/status
        match_single_status = re.search(
            r"/cases/([^/]+)/attachments/([^/]+)/status$", self.path
        )
        if match_single_status:
            case_num = match_single_status.group(1)
            att_id = match_single_status.group(2)
            self._send_json(
                [
                    {
                        "fileName": "mock_file.txt",
                        "caseNumber": case_num,
                        "attachmentId": att_id,
                        "status": "COMPLETED",
                    }
                ]
            )
            return

        # Attachment Status for Case: GET /v3/cases/{caseNumber}/attachments/status
        match_case_status = re.search(r"/cases/([^/]+)/attachments/status$", self.path)
        if match_case_status:
            case_num = match_case_status.group(1)
            self._send_json(
                [
                    {
                        "fileName": "mock_file.txt",
                        "caseNumber": case_num,
                        "attachmentId": "att_1",
                        "status": "COMPLETED",
                    }
                ]
            )
            return

        # List Attachments for Case: GET /v3/cases/{caseNumber}/attachments
        match_list_att = re.search(r"/cases/([^/]+)/attachments(?:\?.*)?$", self.path)
        if match_list_att:
            case_num = match_list_att.group(1)
            case_file = corpus_path / "cases" / f"{case_num}.json"
            attachments = []
            if case_file.exists():
                with open(case_file, "r") as f:
                    case_data = json.load(f)
                host = self.headers.get("Host", "127.0.0.1:8080")
                for raw_att in case_data.get("attachments", []):
                    uuid_str = raw_att.get("uuid") or raw_att.get("id") or "att_1"
                    attachments.append(
                        {
                            "caseNumber": str(case_num),
                            "uuid": uuid_str,
                            "createdDate": raw_att.get(
                                "createdDate", "2026-07-10T16:20:00Z"
                            ),
                            "createdBy": raw_att.get("createdBy", "Jane Developer"),
                            "description": raw_att.get("description", ""),
                            "fileName": raw_att.get("fileName")
                            or raw_att.get("name")
                            or "file.txt",
                            "lastModifiedDate": raw_att.get(
                                "createdDate", "2026-07-10T16:20:00Z"
                            ),
                            "link": f"http://{host}/v3/cases/attachments/downloadFile/{case_num}/{uuid_str}",
                            "size": raw_att.get("size", 1024),
                            "sizeKB": round(raw_att.get("size", 1024) / 1024.0, 1),
                            "accountNumber": "123456",
                            "uploadStatus": "Completed",
                        }
                    )
            self._send_json(attachments)
            return

        # Legacy metadata fallbacks
        if self.path == "/products" or self.path.endswith("/products"):
            with open(corpus_path / "metadata" / "products.json", "r") as f:
                self._send_json(json.load(f))
            return
        match_versions = re.search(r"/products/([^/]+)/versions$", self.path)
        if match_versions:
            self._send_json([{"name": "9.0"}, {"name": "9.1"}])
            return
        if "/values/severity" in self.path:
            self._send_json(
                [
                    {"name": "1 (Urgent)"},
                    {"name": "2 (High)"},
                    {"name": "3 (Normal)"},
                    {"name": "4 (Low)"},
                ]
            )
            return
        if "/values/caseType" in self.path:
            self._send_json([{"name": "Standard"}, {"name": "Bug"}])
            return
        match_notified = re.search(r"/cases/(\d+)/notifiedusers$", self.path)
        if match_notified:
            case_num = match_notified.group(1)
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if case_file.exists():
                with open(case_file, "r") as f:
                    case_data = json.load(f)
                    self._send_json(case_data.get("notifiedusers", []))
            else:
                self._send_error("Case not found", 404)
            return
        match_comments = re.search(r"/cases/(\d+)/comments$", self.path)
        if match_comments:
            case_num = match_comments.group(1)
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if case_file.exists():
                with open(case_file, "r") as f:
                    self._send_json(json.load(f).get("comments", []))
            else:
                self._send_error("Case not found", 404)
            return
        match_case = re.search(r"/cases/(\d+)$", self.path)
        if match_case:
            case_num = match_case.group(1)
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if case_file.exists():
                with open(case_file, "r") as f:
                    self._send_json(json.load(f))
            else:
                self._send_error("Case not found", 404)
            return

        self._send_error("Endpoint not found", 404)

    def do_PUT(self):
        corpus_path = self._get_corpus_path()
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        # Direct S3 Storage PUT upload mock
        if self.path.startswith("/mock-storage/"):
            att_id = self.path.split("/mock-storage/")[1].split("?")[0]
            StatefulMockHandler.mock_storage[att_id] = body
            self.send_response(200)
            self.send_header("ETag", f'"{att_id}_etag"')
            self.end_headers()
            return

        # Check Authorization
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_error("Unauthorized - Missing token", 401)
            return

        match_case = re.search(r"/cases/(\d+)$", self.path)
        if match_case:
            case_num = match_case.group(1)
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if case_file.exists():
                try:
                    payload = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    payload = {}
                with open(case_file, "r") as f:
                    case_data = json.load(f)
                for key in ["status", "severity", "summary", "description"]:
                    if key in payload:
                        case_data[key] = payload[key]
                case_data["lastModifiedDate"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                with open(case_file, "w") as f:
                    json.dump(case_data, f, indent=2)
            self.send_response(200)
            self.end_headers()
            return
        self._send_error("Endpoint not found", 404)

    def do_DELETE(self):
        # Check Authorization
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_error("Unauthorized - Missing token", 401)
            return

        # 1. Attachment Delete File: DELETE /v3/cases/attachments/deleteFile/{caseNumber}/{attachmentId}
        match_del = re.search(r"/attachments/deleteFile/([^/]+)/([^/]+)$", self.path)
        if match_del:
            case_num = match_del.group(1)
            att_id = match_del.group(2)
            corpus_path = self._get_corpus_path()
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if case_file.exists():
                with open(case_file, "r") as f:
                    case_data = json.load(f)
                att_list = case_data.get("attachments", [])
                case_data["attachments"] = [
                    a
                    for a in att_list
                    if a.get("uuid") != att_id and a.get("id") != att_id
                ]
                with open(case_file, "w") as f:
                    json.dump(case_data, f, indent=2)
            if att_id in StatefulMockHandler.mock_storage:
                del StatefulMockHandler.mock_storage[att_id]
            self.send_response(204)
            self.end_headers()
            return

        match_del_user = re.search(
            r"/cases/(\d+)/notifiedusers(?:/([^/]+))?$", self.path
        )
        if match_del_user:
            case_num = match_del_user.group(1)
            target_user = match_del_user.group(2)
            corpus_path = self._get_corpus_path()
            case_file = corpus_path / "cases" / f"{case_num}.json"
            if case_file.exists():
                with open(case_file, "r") as f:
                    case_data = json.load(f)
                existing = case_data.get("notifiedusers", [])
                if target_user:
                    case_data["notifiedusers"] = [
                        u
                        for u in existing
                        if (
                            u.get("ssoUsername", "") if isinstance(u, dict) else str(u)
                        ).lower()
                        != target_user.lower()
                    ]
                else:
                    case_data["notifiedusers"] = []
                with open(case_file, "w") as f:
                    json.dump(case_data, f, indent=2)
            self.send_response(204)
            self.end_headers()
            return

        # 3. Attachment Abort: DELETE /v3/cases/attachments/upload/abort
        if "/attachments/upload/abort" in self.path:
            self._send_json({"status": "aborted"})
            return
        self._send_error("Endpoint not found", 404)

    def do_HEAD(self):
        # Check Authorization
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_error("Unauthorized - Missing token", 401)
            return

        match_head = re.search(r"/cases/([^/]+)/attachments/([^/]+)$", self.path)
        if match_head:
            self.send_response(200)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


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
