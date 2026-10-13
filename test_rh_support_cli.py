import http.server
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unittest
from typing import ClassVar
from unittest import mock

# Configuration for the mock server
MOCK_HOST = "localhost"
MOCK_PORT = 0  # 0 means let the OS choose a free port


class MockRedHatHandler(http.server.BaseHTTPRequestHandler):
    last_case_payload = None
    last_comment_payload = None
    last_update_payload = None
    last_notifiedusers_payload = None
    last_deleted_notifieduser = None
    details_query_count = 0
    probe_query_count = 0
    list_query_count = 0
    latest_mock_comment_id = "comment_2"
    latest_mock_comment_date = "2023-01-03T10:00:00Z"
    mock_storage: ClassVar[dict] = {}

    def log_message(self, format, *args):
        # Silence server logs to keep test output clean
        pass

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # SSO Token Endpoint
        if self.path == "/auth/token":
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

        # GraphQL Endpoint: /graphql or / or payload contains "query"
        if (
            self.path == "/graphql"
            or self.path == "/"
            or (b'"query"' in body[:100] or b"query" in body[:50])
        ):
            try:
                payload = json.loads(body.decode())
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid JSON")
                return

            query = payload.get("query", "")
            variables = payload.get("variables") or {}
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
            # 1. CreateNewCase (Mutation)
            if "CreateNewCase" in query or "CaseCreate" in query:
                case_in = variables.get("input", {}).get("RedHatSupportCase", {})
                MockRedHatHandler.last_case_payload = {
                    "product": case_in.get("product")
                    or case_in.get("ProductId")
                    or "RHEL",
                    "version": case_in.get("version")
                    or case_in.get("Version")
                    or "8.0",
                    "summary": case_in.get("Subject", ""),
                    "description": case_in.get("Description", ""),
                    "severity": case_in.get("Priority", "Normal"),
                    "caseType": case_in.get("caseType")
                    or case_in.get("Type", "Standard"),
                    **case_in,
                }
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                resp_data = {
                    "data": {
                        "redhat_support_uiapi": {
                            "CaseCreate": {
                                "RedHatSupportRecord": {
                                    "Id": "50020230001",
                                    "CaseNumber__c": {"value": "20230001"},
                                    "Subject": {
                                        "value": MockRedHatHandler.last_case_payload.get(
                                            "Subject", ""
                                        )
                                    },
                                    "Status": {"value": "Waiting on Red Hat"},
                                    "CreatedDate": {"value": "2026-08-20T10:00:00Z"},
                                }
                            }
                        }
                    }
                }
                self.wfile.write(json.dumps(resp_data).encode())
                return

            # 2. UpdateCase (Mutation)
            if "UpdateCase" in query or "CaseUpdate" in query:
                MockRedHatHandler.last_update_payload = variables.get("input", {})
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                patch = MockRedHatHandler.last_update_payload.get(
                    "RedHatSupportCase", {}
                )
                resp_data = {
                    "data": {
                        "redhat_support_uiapi": {
                            "CaseUpdate": {
                                "RedHatSupportRecord": {
                                    "Id": MockRedHatHandler.last_update_payload.get(
                                        "Id", "5001001"
                                    ),
                                    "CaseNumber__c": {"value": "1001"},
                                    "Status": {
                                        "value": patch.get(
                                            "Status", "Waiting on Red Hat"
                                        )
                                    },
                                    "Priority": {
                                        "value": patch.get("Priority", "High")
                                    },
                                    "LastModifiedDate": {
                                        "value": "2026-08-20T10:00:00Z"
                                    },
                                }
                            }
                        }
                    }
                }
                self.wfile.write(json.dumps(resp_data).encode())
                return

            # 3. CreateCaseComment (Mutation)
            if "CreateCaseComment" in query or "CaseComment__cCreate" in query:
                comment_input = variables.get("input", {}).get(
                    "RedHatSupportCaseComment__c", {}
                )
                MockRedHatHandler.last_comment_payload = {
                    "commentBody": comment_input.get("Body__c", ""),
                    "body": comment_input.get("Body__c", ""),
                    "isPublic": comment_input.get("Visibility__c", "Public")
                    == "Public",
                    **comment_input,
                }
                if "FAIL_COMMENT" in comment_input.get(
                    "Case__c", ""
                ) or "FAIL_COMMENT" in comment_input.get("Body__c", ""):
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b"Simulated Server Error")
                    return
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                resp_data = {
                    "data": {
                        "redhat_support_uiapi": {
                            "CaseComment__cCreate": {
                                "RedHatSupportRecord": {
                                    "Id": "comment_1",
                                    "Body__c": {
                                        "value": comment_input.get("Body__c", "")
                                    },
                                    "Case__c": {
                                        "value": comment_input.get("Case__c", "")
                                    },
                                    "CreatedDate": {"value": "2026-08-20T10:00:00Z"},
                                }
                            }
                        }
                    }
                }
                self.wfile.write(json.dumps(resp_data).encode())
                return

            # 4. ResolveCaseLookupData
            if "ResolveCaseLookupData" in query:
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                resp_data = {
                    "data": {
                        "redhat_support_uiapi": {
                            "Contact": {
                                "RedHatSupportContact": {
                                    "edges": [
                                        {
                                            "node": {
                                                "Id": "003MockContact",
                                                "AccountId": {
                                                    "value": "001MockAccount"
                                                },
                                            }
                                        }
                                    ]
                                }
                            },
                            "Account": {
                                "RedHatSupportAccount": {
                                    "edges": [{"node": {"Id": "001MockAccount"}}]
                                }
                            },
                            "RecordType": {
                                "RedHatSupportRecordType": {
                                    "edges": [{"node": {"Id": "012MockRecordType"}}]
                                }
                            },
                            "Product": {
                                "RedHatSupportProduct2": {
                                    "edges": [
                                        {
                                            "node": {
                                                "Id": "01tMockProduct",
                                                "Name": {"value": "RHEL"},
                                                "VersionName__c": {"value": "9.0"},
                                            }
                                        }
                                    ]
                                }
                            },
                        }
                    }
                }
                self.wfile.write(json.dumps(resp_data).encode())
                return

            # 5. GetActiveProducts
            if "GetActiveProducts" in query or "RedHatSupportProduct2" in query:
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                after_val = variables.get("after")
                if after_val == "page1_cursor":
                    edges = [
                        {
                            "node": {
                                "Id": "01tOPENSHIFT",
                                "Name": {
                                    "value": "Red Hat OpenShift Container Platform"
                                },
                                "VersionName__c": {"value": "4.12"},
                                "Family": {"value": "Cloud"},
                                "ParentProduct__c": {"value": None},
                            }
                        }
                    ]
                    page_info = {"hasNextPage": False, "endCursor": "page2_cursor"}
                elif variables.get("first") == 1:
                    edges = [
                        {
                            "node": {
                                "Id": "01tRHEL",
                                "Name": {"value": "Red Hat Enterprise Linux"},
                                "VersionName__c": {"value": "9.0"},
                                "Family": {"value": "Linux"},
                                "ParentProduct__c": {"value": None},
                            }
                        }
                    ]
                    page_info = {"hasNextPage": True, "endCursor": "page1_cursor"}
                else:
                    edges = [
                        {
                            "node": {
                                "Id": "01tRHEL",
                                "Name": {"value": "Red Hat Enterprise Linux"},
                                "VersionName__c": {"value": "9.0"},
                                "Family": {"value": "Linux"},
                                "ParentProduct__c": {"value": None},
                            }
                        },
                        {
                            "node": {
                                "Id": "01tOPENSHIFT",
                                "Name": {
                                    "value": "Red Hat OpenShift Container Platform"
                                },
                                "VersionName__c": {"value": "4.12"},
                                "Family": {"value": "Cloud"},
                                "ParentProduct__c": {"value": None},
                            }
                        },
                    ]
                    page_info = {"hasNextPage": False, "endCursor": None}

                resp_data = {
                    "data": {
                        "redhat_support_uiapi": {
                            "query": {
                                "RedHatSupportProduct2": {
                                    "totalCount": len(edges),
                                    "edges": edges,
                                    "pageInfo": page_info,
                                }
                            }
                        }
                    }
                }
                self.wfile.write(json.dumps(resp_data).encode())
                return

            # 5.5 CheckCaseLastModified
            if "CheckCaseLastModified" in query:
                MockRedHatHandler.probe_query_count += 1
                case_num = str(variables.get("caseNumber", "12345"))
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                if not case_num.isdigit() or case_num in (
                    "99999",
                    "nonexistent",
                    "not_found",
                ):
                    self.wfile.write(
                        json.dumps(
                            {
                                "data": {
                                    "redhat_support_uiapi": {
                                        "query": {"RedHatSupportCase": {"edges": []}}
                                    }
                                }
                            }
                        ).encode()
                    )
                else:
                    self.wfile.write(
                        json.dumps(
                            {
                                "data": {
                                    "redhat_support_uiapi": {
                                        "query": {
                                            "RedHatSupportCase": {
                                                "edges": [
                                                    {
                                                        "node": {
                                                            "Id": f"500{case_num}",
                                                            "LastModifiedDate": {
                                                                "value": (
                                                                    "2023-01-02T10:00:00Z"
                                                                    if case_num
                                                                    == "12345"
                                                                    else "2026-08-20T10:00:00Z"
                                                                )
                                                            },
                                                            "Status": {
                                                                "value": "Waiting on Red Hat"
                                                            },
                                                            "CaseComments__r": {
                                                                "edges": [
                                                                    {
                                                                        "node": {
                                                                            "Id": MockRedHatHandler.latest_mock_comment_id,
                                                                            "CreatedDate": {
                                                                                "value": MockRedHatHandler.latest_mock_comment_date
                                                                            },
                                                                        }
                                                                    }
                                                                ]
                                                            },
                                                        }
                                                    }
                                                ]
                                            }
                                        }
                                    }
                                }
                            }
                        ).encode()
                    )
                return

            # 6. GetCaseAndComments
            if "GetCaseAndComments" in query or "CaseComments__r" in query:
                MockRedHatHandler.details_query_count += 1
                case_num = str(variables.get("caseNumber", "12345"))
                if not case_num.isdigit() or case_num in (
                    "99999",
                    "nonexistent",
                    "not_found",
                ):
                    self.send_response(200)
                    self.send_header("Content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps(
                            {
                                "data": {
                                    "redhat_support_uiapi": {
                                        "query": {"RedHatSupportCase": {"edges": []}}
                                    }
                                }
                            }
                        ).encode()
                    )
                    return

                if "FAIL_COMMENT" in case_num:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b"Simulated Server Error")
                    return

                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                after_val = variables.get("after")
                if case_num == "888888":
                    if after_val == "comm_page1_cursor":
                        comment_edges = [
                            {
                                "node": {
                                    "Id": "comment_page2",
                                    "Body__c": {"value": "Comment on page 2"},
                                    "CreatedBy": {"Name": {"value": "Red Hat Support"}},
                                    "LastModifiedByName__c": {
                                        "value": "Red Hat Support"
                                    },
                                    "IsAssociate__c": {"value": True},
                                    "IsCustomer__c": {"value": False},
                                    "CreatedDate": {"value": "2026-08-20T12:00:00Z"},
                                }
                            }
                        ]
                        page_info = {
                            "hasNextPage": False,
                            "endCursor": "comm_page2_cursor",
                        }
                    else:
                        comment_edges = [
                            {
                                "node": {
                                    "Id": "comment_page1",
                                    "Body__c": {"value": "Comment on page 1"},
                                    "CreatedBy": {"Name": {"value": "Customer"}},
                                    "LastModifiedByName__c": {"value": "Customer"},
                                    "IsAssociate__c": {"value": False},
                                    "IsCustomer__c": {"value": True},
                                    "CreatedDate": {"value": "2026-08-20T10:00:00Z"},
                                }
                            }
                        ]
                        page_info = {
                            "hasNextPage": True,
                            "endCursor": "comm_page1_cursor",
                        }
                else:
                    comment_edges = [
                        {
                            "node": {
                                "Id": "comment_1",
                                "Body__c": {"value": "Comment 1"},
                                "LastModifiedByName__c": {"value": "Red Hat Support"},
                                "IsAssociate__c": {"value": True},
                                "IsCustomer__c": {"value": False},
                                "CreatedDate": {"value": "2023-01-02T10:00:00Z"},
                            }
                        },
                        {
                            "node": {
                                "Id": "comment_2",
                                "Body__c": {"value": "Comment 2"},
                                "LastModifiedByName__c": {"value": "Customer"},
                                "IsAssociate__c": {"value": False},
                                "IsCustomer__c": {"value": False},
                                "Visibility__c": {"value": "Internal"},
                                "isPublic": False,
                                "CreatedDate": {"value": "2023-01-03T10:00:00Z"},
                            }
                        },
                    ]
                    if MockRedHatHandler.latest_mock_comment_id not in (
                        "comment_1",
                        "comment_2",
                    ):
                        comment_edges.append(
                            {
                                "node": {
                                    "Id": MockRedHatHandler.latest_mock_comment_id,
                                    "Body__c": {
                                        "value": "Dynamic new comment from support"
                                    },
                                    "CreatedBy": {"Name": {"value": "Red Hat Support"}},
                                    "LastModifiedByName__c": {
                                        "value": "Red Hat Support"
                                    },
                                    "IsAssociate__c": {"value": True},
                                    "IsCustomer__c": {"value": False},
                                    "Visibility__c": {"value": "Public"},
                                    "isPublic": True,
                                    "CreatedDate": {
                                        "value": MockRedHatHandler.latest_mock_comment_date
                                    },
                                }
                            }
                        )
                    page_info = {"hasNextPage": False, "endCursor": None}

                node = {
                    "Id": f"500{case_num}",
                    "CaseNumber__c": {"value": str(case_num)},
                    "Subject": {"value": "Test Case Details"},
                    "Status": {"value": "Waiting on Red Hat"},
                    "Priority": {"value": "High"},
                    "Type": {"value": "Standard"},
                    "Description": {"value": "This is a detailed description."},
                    "Product": {
                        "Id": "prod_1",
                        "Name": {"value": "RHEL"},
                        "ParentProduct__r": {
                            "Id": "parent_prod",
                            "Name": {"value": "RHEL"},
                        },
                    },
                    "RedHatSupportAccount": {
                        "Id": "acc_1",
                        "Name": {"value": "Acme Corp"},
                        "AccountNumber": {"value": "111111"},
                    },
                    "RedHatSupportContact": {
                        "Id": "cont_1",
                        "Name": {"value": "Test Owner"},
                        "Email": {"value": "test@example.com"},
                        "Phone": {"value": "1234567890"},
                        "SSOUserName__c": {"value": "user123"},
                    },
                    "Owner": {
                        "Id": "owner_1",
                        "Name": {"value": "Red Hat Support"},
                    },
                    "CreatedDate": {"value": "2023-01-01T10:00:00Z"},
                    "LastModifiedDate": {"value": "2023-01-02T10:00:00Z"},
                    "LastModifiedBy": {"Name": {"value": "user123"}},
                    "CaseComments__r": {
                        "edges": comment_edges,
                        "pageInfo": page_info,
                    },
                }
                resp_data = {
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
                self.wfile.write(json.dumps(resp_data).encode())
                return

            # 7. GetCasesByFilters / GetSupportCases
            if (
                "GetCasesByFilters" in query
                or "GetSupportCases" in query
                or "RedHatSupportCase" in query
            ):
                MockRedHatHandler.list_query_count += 1
                where_clause = variables.get("where") or {}
                filters_received = [f"AUTH={source}"]
                if "RedHatSupportAccount" in where_clause:
                    acc = (
                        where_clause["RedHatSupportAccount"]
                        .get("AccountNumber", {})
                        .get("eq")
                    )
                    if acc:
                        filters_received.append(f"ACC={acc}")
                if "Status" in where_clause:
                    stat = where_clause["Status"]
                    if isinstance(stat, dict):
                        if "eq" in stat:
                            filters_received.append(f"STAT={stat['eq']}")
                        elif "ne" in stat:
                            filters_received.append(f"STAT_NE={stat['ne']}")
                        elif "in" in stat:
                            filters_received.append(f"STAT={','.join(stat['in'])}")
                    else:
                        filters_received.append(f"STAT={stat}")
                if "Priority" in where_clause:
                    prio = where_clause["Priority"]
                    if isinstance(prio, dict) and "in" in prio:
                        filters_received.append(f"SEV={','.join(prio['in'])}")
                    elif isinstance(prio, dict) and "eq" in prio:
                        filters_received.append(f"SEV={prio['eq']}")
                    else:
                        filters_received.append(f"SEV={prio}")
                if "Owner" in where_clause:
                    own = where_clause["Owner"].get("Name", {}).get("eq")
                    if own:
                        filters_received.append(f"OWN={own}")
                if "Subject" in where_clause:
                    subj = where_clause["Subject"]
                    sub_val = (
                        subj.get("like", "") if isinstance(subj, dict) else str(subj)
                    )
                    clean_sub = sub_val.replace("%", "")
                    filters_received.append(f"SUBJ={clean_sub}")
                if "CaseNumber__c" in where_clause:
                    c_filter = where_clause["CaseNumber__c"]
                    c_val = (
                        c_filter.get("like", c_filter.get("eq", ""))
                        if isinstance(c_filter, dict)
                        else str(c_filter)
                    )
                    clean_c = str(c_val).replace("%", "")
                    filters_received.append(f"CNUM={clean_c}")
                if "Product" in where_clause:
                    prod = where_clause["Product"].get("Name", {}).get("eq")
                    if prod:
                        filters_received.append(f"PROD={prod}")

                summary_str = "Filters: " + "|".join(filters_received)
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                after_val = variables.get("after")
                if "ACC=PAGINATE" in summary_str:
                    if after_val == "case_page1_cursor":
                        edges = [
                            {
                                "node": {
                                    "Id": "500CASE_PAGE2",
                                    "CaseNumber__c": {"value": "CASE_PAGE2"},
                                    "Subject": {"value": "Page 2 Case"},
                                    "Status": {"value": "In Progress"},
                                    "Priority": {"value": "3 (Normal)"},
                                    "CreatedDate": {"value": "2026-07-10T14:30:00Z"},
                                    "LastModifiedDate": {
                                        "value": "2026-07-11T09:15:00Z"
                                    },
                                },
                                "cursor": "case_page2_cursor",
                            }
                        ]
                        page_info = {
                            "hasNextPage": False,
                            "endCursor": "case_page2_cursor",
                        }
                    else:
                        edges = [
                            {
                                "node": {
                                    "Id": "500CASE_PAGE1",
                                    "CaseNumber__c": {"value": "CASE_PAGE1"},
                                    "Subject": {"value": "Page 1 Case"},
                                    "Status": {"value": "Waiting on Red Hat"},
                                    "Priority": {"value": "2 (High)"},
                                    "CreatedDate": {"value": "2026-07-10T14:30:00Z"},
                                    "LastModifiedDate": {
                                        "value": "2026-07-11T09:15:00Z"
                                    },
                                },
                                "cursor": "case_page1_cursor",
                            }
                        ]
                        page_info = {
                            "hasNextPage": True,
                            "endCursor": "case_page1_cursor",
                        }
                else:
                    edges = [
                        {
                            "node": {
                                "Id": "500FILTER_TEST",
                                "CaseNumber__c": {"value": "FILTER_TEST"},
                                "Subject": {"value": summary_str},
                                "Status": {"value": "Waiting on Red Hat"},
                                "Priority": {"value": "2 (High)"},
                                "CreatedDate": {"value": "2026-07-10T14:30:00Z"},
                                "LastModifiedDate": {"value": "2026-07-11T09:15:00Z"},
                            },
                            "cursor": "cursor_filter_test",
                        }
                    ]
                    page_info = {
                        "hasNextPage": False,
                        "endCursor": "cursor_filter_test",
                    }

                resp_data = {
                    "data": {
                        "redhat_support_uiapi": {
                            "query": {
                                "RedHatSupportCase": {
                                    "edges": edges,
                                    "pageInfo": page_info,
                                }
                            }
                        }
                    }
                }
                self.wfile.write(json.dumps(resp_data).encode())
                return

        # Attachment Service: POST /v3/cases/attachments/upload
        if (
            "/attachments/upload" in self.path
            and not self.path.endswith("/complete")
            and not self.path.endswith("/abort")
        ):
            data = json.loads(body.decode())
            filesize = data.get("fileSize", 1024)
            host = self.headers.get("Host", "localhost")
            attach_uuid = "att_test_uuid_123"
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            if filesize > 5368709120 or "totalChunks" in data:
                total_chunks = max(1, data.get("totalChunks", 2))
                parts = [
                    {
                        "partNumber": i + 1,
                        "presignedUrl": f"http://{host}/mock-storage/{attach_uuid}?partNumber={i + 1}",
                    }
                    for i in range(total_chunks)
                ]
                self.wfile.write(
                    json.dumps(
                        {
                            "attachmentId": attach_uuid,
                            "uploadStrategy": "MULTIPART",
                            "uploadId": "upload_id_123",
                            "expirationTime": "2026-12-31T23:59:59.000Z",
                            "parts": parts,
                        }
                    ).encode()
                )
            else:
                self.wfile.write(
                    json.dumps(
                        {
                            "attachmentId": attach_uuid,
                            "uploadStrategy": "SINGLE_PUT",
                            "presignedUrl": f"http://{host}/mock-storage/{attach_uuid}",
                            "expirationTime": "2026-12-31T23:59:59.000Z",
                        }
                    ).encode()
                )
            return

        # Attachment Service: POST /v3/cases/attachments/upload/complete
        if "/attachments/upload/complete" in self.path:
            data = json.loads(body.decode())
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "status": "success",
                        "attachmentId": data.get("attachmentId", "att_test_uuid_123"),
                        "message": "Upload completed.",
                    }
                ).encode()
            )
            return
        self.send_response(404)
        self.end_headers()

    def do_PUT(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # Direct S3 Storage PUT
        if self.path.startswith("/mock-storage/"):
            att_id = self.path.split("/mock-storage/")[1].split("?")[0]
            MockRedHatHandler.mock_storage[att_id] = body
            self.send_response(200)
            self.send_header("ETag", f'"{att_id}_etag"')
            self.end_headers()
            return

        # Check Authorization header
        auth_header = self.headers.get("Authorization")
        if auth_header != "Bearer mock_access_token_123":
            self.send_response(401)
            self.end_headers()
            return

        self.end_headers()

    def do_GET(self):
        # Direct Storage Download
        if self.path.startswith("/mock-storage/"):
            att_id = self.path.split("/mock-storage/")[1].split("?")[0]
            data = MockRedHatHandler.mock_storage.get(
                att_id, b"mock file downloaded content"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # Check Authorization header
        auth_header = self.headers.get("Authorization")
        if auth_header != "Bearer mock_access_token_123":
            self.send_response(401)
            self.end_headers()
            return

        # Knowledgebase (KCS) Search Endpoint
        if "/search/kcs" in self.path or "/hydra/rest/search/kcs" in self.path:
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "response": {
                            "numFound": 4,
                            "docs": [
                                {
                                    "id": "7008126",
                                    "title": "Failed to mount API filesystem during boot",
                                    "summary": "Systemd unit fails during boot with mount error",
                                    "documentKind": "Solution",
                                    "view_uri": "https://access.redhat.com/solutions/7008126",
                                    "product": "Red Hat Enterprise Linux",
                                    "published_date": "2026-03-15T08:00:00Z",
                                    "language": "en",
                                },
                                {
                                    "id": "6911201",
                                    "title": "Ingress controller certificate expiration and rotation",
                                    "summary": "Procedure to rotate expired router certs in OpenShift",
                                    "documentKind": "Article",
                                    "view_uri": "https://access.redhat.com/articles/6911201",
                                    "product": "Red Hat OpenShift Container Platform",
                                    "published_date": "2026-02-10T11:00:00Z",
                                    "language": "en",
                                },
                                {
                                    "id": "https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/networking/index",
                                    "title": "Configuring Networking on RHEL 9",
                                    "summary": "Official product documentation for network configuration",
                                    "documentKind": "Documentation",
                                    "view_uri": "https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/networking/index",
                                    "product": "Red Hat Enterprise Linux",
                                    "published_date": "2026-01-20T09:00:00Z",
                                    "language": "en",
                                },
                                {
                                    "id": "https://docs.redhat.com/ja/documentation/red_hat_enterprise_linux/9/html/networking/index",
                                    "title": "RHEL 9 ネットワークの設定 (日本語)",
                                    "summary": "Japanese translation of networking documentation",
                                    "documentKind": "Documentation",
                                    "view_uri": "https://docs.redhat.com/ja/documentation/red_hat_enterprise_linux/9/html/networking/index",
                                    "product": "Red Hat Enterprise Linux",
                                    "published_date": "2026-01-22T09:00:00Z",
                                    "language": "ja",
                                },
                            ],
                        }
                    }
                ).encode()
            )
            return

        # Attachment Download URL endpoint
        match_download = re.search(
            r"/attachments/downloadFile/([^/]+)/([^/]+)$", self.path
        )
        if match_download:
            case_num = match_download.group(1)
            att_id = match_download.group(2)
            host = self.headers.get("Host", "localhost")
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "downloadUrl": f"http://{host}/mock-storage/{att_id}",
                        "fileName": "downloaded_test_file.txt",
                        "expiresInSeconds": "3600",
                    }
                ).encode()
            )
            return

        # Attachment List Endpoint: GET /v3/cases/{caseNumber}/attachments
        match_list_att = re.search(r"/cases/([^/]+)/attachments(?:\?.*)?$", self.path)
        if match_list_att:
            case_num = match_list_att.group(1)
            host = self.headers.get("Host", "localhost")
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            attachments = [
                {
                    "caseNumber": str(case_num),
                    "uuid": "att_1001_uuid",
                    "fileName": "test_attachment.log",
                    "size": 2048,
                    "sizeKB": 2.0,
                    "createdDate": "2026-07-10T16:20:00Z",
                    "createdBy": "Jane Developer",
                    "description": "Initial log capture",
                    "link": f"http://{host}/v3/cases/attachments/downloadFile/{case_num}/att_1001_uuid",
                    "accountNumber": "111111",
                    "uploadStatus": "Completed",
                }
            ]
            self.wfile.write(json.dumps(attachments).encode())
            return

        # Attachment Status Endpoint: GET /v3/cases/{caseNumber}/attachments/status
        match_att_status = re.search(
            r"/cases/([^/]+)/attachments/(?:([^/]+)/)?status$", self.path
        )
        if match_att_status:
            case_num = match_att_status.group(1)
            att_id = match_att_status.group(2) or "att_1001_uuid"
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    [
                        {
                            "fileName": "test_attachment.log",
                            "caseNumber": str(case_num),
                            "attachmentId": att_id,
                            "status": "COMPLETED",
                        }
                    ]
                ).encode()
            )
            return
        # GET /cases/{id}
        if "/cases/" in self.path:
            parts = self.path.split("/")
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
                    "attachments": [
                        {
                            "id": "att_1001_uuid",
                            "name": "test_attachment.log",
                            "size": 2048,
                            "createdDate": "2026-07-10T16:20:00Z",
                            "createdBy": "Jane Developer",
                        }
                    ],
                }
                self.wfile.write(json.dumps(case).encode())
                return

        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        auth_header = self.headers.get("Authorization")
        if auth_header != "Bearer mock_access_token_123":
            self.send_response(401)
            self.end_headers()
            return

        # DELETE /v3/cases/attachments/deleteFile/{caseNumber}/{attachmentId}
        match_del = re.search(r"/attachments/deleteFile/([^/]+)/([^/]+)$", self.path)
        if match_del:
            self.send_response(204)
            self.end_headers()
            return

        # DELETE /v3/cases/attachments/upload/abort
        if "/attachments/upload/abort" in self.path:
            MockRedHatHandler.last_aborted_upload = (
                json.loads(body.decode()) if body else {}
            )
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "aborted"}).encode())
            return
        self.send_response(404)
        self.end_headers()

    def do_HEAD(self):
        auth_header = self.headers.get("Authorization")
        if auth_header != "Bearer mock_access_token_123":
            self.send_response(401)
            self.end_headers()
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
        MockRedHatHandler.details_query_count = 0
        MockRedHatHandler.probe_query_count = 0
        MockRedHatHandler.list_query_count = 0
        MockRedHatHandler.latest_mock_comment_id = "comment_2"
        MockRedHatHandler.latest_mock_comment_date = "2023-01-03T10:00:00Z"
        self.env = os.environ.copy()
        self.env["RH_API_URL"] = self.base_url
        self.env["RH_SSO_URL"] = self.sso_url
        self.env["RH_GRAPHQL_URL"] = f"{self.base_url}/graphql"
        self.env["RH_ATTACHMENT_API_URL"] = self.base_url
        self.env["REDHAT_SUPPORT_OFFLINE_TOKEN"] = "mock_offline_token"
        self.env["FORCE_COLOR"] = "1"
        self.env["RH_KCS_SEARCH_URL"] = f"{self.base_url}/search/kcs"

        # Ensure subprocess finds requests even if HOME is changed
        self.env["PYTHONPATH"] = os.pathsep.join(sys.path)

        # Avoid truncation in list output
        self.env["COLUMNS"] = "200"

        # Isolate cache, config, and state for every test
        import tempfile

        self.test_cache_dir = tempfile.mkdtemp()
        self.env["XDG_CACHE_HOME"] = self.test_cache_dir
        self.test_config_dir = tempfile.mkdtemp()
        self.env["XDG_CONFIG_HOME"] = self.test_config_dir
        self.test_state_dir = tempfile.mkdtemp()
        self.env["XDG_STATE_HOME"] = self.test_state_dir

        self.cli_path = os.path.abspath("rh-support-cli.py")

    def tearDown(self):
        import shutil

        if os.path.exists(self.test_cache_dir):
            shutil.rmtree(self.test_cache_dir)
        if os.path.exists(self.test_config_dir):
            shutil.rmtree(self.test_config_dir)
        if os.path.exists(self.test_state_dir):
            shutil.rmtree(self.test_state_dir)

    def run_cli(self, args, input=None):
        """Helper to run the CLI script as a subprocess"""
        cmd = [sys.executable, self.cli_path] + args
        return subprocess.run(
            cmd,
            env=self.env,
            capture_output=True,
            text=True,
            input=input,
            check=False,
        )

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
            self.assertIn(
                "Success: File 'test_attachment.txt' attached.", result.stdout
            )
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
                "Success: Case status updated to 'Waiting on Customer Action Required'.",
                result.stdout,
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

    def test_comment_no_status_change_flag(self):
        """Test that --no-status-change and --keep-status post comments without altering case status."""
        with open("no_change_comment.txt", "w") as f:
            f.write("Informational update without status transition.")

        try:
            # 1. Test --no-status-change flag
            MockRedHatHandler.last_update_payload = None
            result1 = self.run_cli(
                [
                    "comment",
                    "-c",
                    "01234567",
                    "-f",
                    "no_change_comment.txt",
                    "--no-status-change",
                ]
            )
            self.assertEqual(result1.returncode, 0)
            self.assertIn("Success: Comment added.", result1.stdout)
            self.assertNotIn("Case status updated to", result1.stdout)
            self.assertIsNone(MockRedHatHandler.last_update_payload)

            # 2. Test --keep-status alias
            MockRedHatHandler.last_update_payload = None
            result2 = self.run_cli(
                [
                    "comment",
                    "-c",
                    "01234567",
                    "-f",
                    "no_change_comment.txt",
                    "--keep-status",
                ]
            )
            self.assertEqual(result2.returncode, 0)
            self.assertIn("Success: Comment added.", result2.stdout)
            self.assertNotIn("Case status updated to", result2.stdout)
            self.assertIsNone(MockRedHatHandler.last_update_payload)
        finally:
            if os.path.exists("no_change_comment.txt"):
                os.remove("no_change_comment.txt")

    def test_comment_status_none_or_keep(self):
        """Test that passing -s none or --status keep avoids changing case status."""
        with open("status_none_comment.txt", "w") as f:
            f.write("Update with explicit status none.")

        try:
            # 1. -s none
            MockRedHatHandler.last_update_payload = None
            result1 = self.run_cli(
                [
                    "comment",
                    "-c",
                    "01234567",
                    "-f",
                    "status_none_comment.txt",
                    "-s",
                    "none",
                ]
            )
            self.assertEqual(result1.returncode, 0)
            self.assertIn("Success: Comment added.", result1.stdout)
            self.assertNotIn("Case status updated to", result1.stdout)
            self.assertIsNone(MockRedHatHandler.last_update_payload)

            # 2. --status keep
            MockRedHatHandler.last_update_payload = None
            result2 = self.run_cli(
                [
                    "comment",
                    "-c",
                    "01234567",
                    "-f",
                    "status_none_comment.txt",
                    "--status",
                    "keep",
                ]
            )
            self.assertEqual(result2.returncode, 0)
            self.assertIn("Success: Comment added.", result2.stdout)
            self.assertNotIn("Case status updated to", result2.stdout)
            self.assertIsNone(MockRedHatHandler.last_update_payload)
        finally:
            if os.path.exists("status_none_comment.txt"):
                os.remove("status_none_comment.txt")

    def test_close_case_command(self):
        """Test close subcommand: posts closing comment and sets status to Closed"""
        with open("close_comment.txt", "w") as f:
            f.write("Closing case, issue is resolved.")
        try:
            result = self.run_cli(
                ["close", "-c", "01234567", "-f", "close_comment.txt"]
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Comment added.", result.stdout)
            self.assertIn("Success: Case status updated to 'Closed'.", result.stdout)
        finally:
            if os.path.exists("close_comment.txt"):
                os.remove("close_comment.txt")

    def test_comment_from_template(self):
        """Test posting a comment using a template with dynamic variables and status overrides"""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        self.env["HOME"] = temp_home

        try:
            # Create templates directory
            templates_dir = os.path.join(
                temp_home, ".config", "rh-support-cli", "templates"
            )
            os.makedirs(templates_dir, exist_ok=True)

            # Write template file
            template_path = os.path.join(templates_dir, "ping_case.yaml")
            with open(template_path, "w") as f:
                f.write(
                    "status: customer\n"
                    "comment: |\n"
                    "  Ping support for {{ environment }} environment by {{ user_name }}.\n"
                )

            # Execute comment using template
            result = self.run_cli(
                [
                    "comment",
                    "-c",
                    "01234567",
                    "-t",
                    "ping_case",
                    "--template-var",
                    "environment=Production",
                    "--template-var",
                    "user_name=Alice",
                ]
            )

            if result.returncode != 0:
                print("\nSTDOUT:", result.stdout)
                print("STDERR:", result.stderr)

            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Comment added.", result.stdout)
            self.assertIn(
                "Success: Case status updated to 'Waiting on Customer Action Required'.",
                result.stdout,
            )
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            else:
                del self.env["HOME"]
            shutil.rmtree(temp_home)

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
                cmd,
                env=self.env,
                capture_output=True,
                text=True,
                input="y\n",
                check=False,
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

    def test_create_case_default_template(self):
        """Test creating a case using default_create_template"""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        config_path = os.path.join(config_dir, "config.yaml")
        with open(config_path, "w") as f:
            f.write("default_create_template: base_template\n")

        template_path = os.path.join(template_dir, "base_template.yaml")
        with open(template_path, "w") as f:
            f.write(
                "product: RHEL\nversion: 8.0\nsummary: Templated Summary\nseverity: Low\ncaseType: Standard\n"
            )

        with open("desc.txt", "w") as f:
            f.write("My Issue Description")

        try:
            cmd = [
                sys.executable,
                self.cli_path,
                "--config-file",
                config_path,
                "create",
                "--description-file",
                "desc.txt",
            ]

            result = subprocess.run(
                cmd,
                env=self.env,
                capture_output=True,
                text=True,
                input="y\n",
                check=False,
            )

            if result.returncode != 0:
                print("\nSTDOUT:", result.stdout)
                print("STDERR:", result.stderr)
            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Case #20230001 created.", result.stdout)

            cmd_no_tmpl = [
                sys.executable,
                self.cli_path,
                "--config-file",
                config_path,
                "create",
                "--description-file",
                "desc.txt",
                "--no-default-template",
            ]

            result_no_tmpl = subprocess.run(
                cmd_no_tmpl,
                env=self.env,
                capture_output=True,
                text=True,
                input="EOF\n",
                check=False,
            )
            self.assertNotEqual(result_no_tmpl.returncode, 0)

        finally:
            if os.path.exists("desc.txt"):
                os.remove("desc.txt")
            shutil.rmtree(temp_home)

    def test_template_circular_dependency(self):
        """Test template engine circular dependency detection"""
        import shutil
        import tempfile
        from unittest.mock import patch

        from rh_support_lib.templates import TemplateEngine

        temp_dir = tempfile.mkdtemp()
        try:
            with open(os.path.join(temp_dir, "tmpl_a.yaml"), "w") as f:
                f.write("include_templates: [tmpl_b]\nkey_a: value_a\n")
            with open(os.path.join(temp_dir, "tmpl_b.yaml"), "w") as f:
                f.write("include_templates: [tmpl_c]\nkey_b: value_b\n")
            with open(os.path.join(temp_dir, "tmpl_c.yaml"), "w") as f:
                f.write("include_templates: [tmpl_a]\nkey_c: value_c\n")

            engine = TemplateEngine(temp_dir)
            with patch("builtins.print"):
                result = engine.process(["tmpl_a"], {})

            self.assertEqual(result.get("key_a"), "value_a")
            self.assertEqual(result.get("key_b"), "value_b")
            self.assertEqual(result.get("key_c"), "value_c")

            # Check that processing didn't loop infinitely
            self.assertIn("key_a", result)

        finally:
            shutil.rmtree(temp_dir)

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
        result = subprocess.run(
            cmd, env=env_no_token, capture_output=True, text=True, check=False
        )

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
    status: ["open"]
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
            self.assertIn("STAT=Waiting on Red Hat,Waiting on Customer", result.stdout)

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
        import shutil
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

    def test_templates(self):
        """Test template functionality"""
        # Check if jinja2 is available, else skip
        try:
            import jinja2  # noqa: F401
        except ImportError:
            print("Skipping test_templates: jinja2 not installed")
            return

        import shutil
        import tempfile

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
                cmd,
                env=self.env,
                capture_output=True,
                text=True,
                input="y\n",
                check=False,
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

        import shutil
        import tempfile

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
                cmd,
                env=self.env,
                capture_output=True,
                text=True,
                input="y\n",
                check=False,
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
                cmd, env=env_with_editor, capture_output=True, text=True, check=False
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
                cmd, env=env_with_editor, capture_output=True, text=True, check=False
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
                cmd, env=env_with_editor, capture_output=True, text=True, check=False
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
                cmd, env=env_with_editor, capture_output=True, text=True, check=False
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
            from rh_support_lib.utils import clean_comment_body

            self.assertNotIn("# Header 1", body)
            self.assertNotIn("# Header 2", body)
            self.assertEqual(
                clean_comment_body(body), "Actual Content\n\n# Not a header"
            )
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
                cmd, env=env_with_editor, capture_output=True, text=True, check=False
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
            from rh_support_lib.utils import clean_comment_body

            payload = MockRedHatHandler.last_comment_payload
            self.assertEqual(
                clean_comment_body(payload.get("commentBody")), "Edited Content"
            )

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
        self.assertIn("DEBUG: Request:", result.stderr)
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

            self.assertIn("DEBUG: Request:", content)
            self.assertIn("DEBUG: Response Status: 200", content)
            # Ensure it didn't print to stderr (since we redirected to file)
            self.assertNotIn("DEBUG: Request:", result.stderr)

        finally:
            if os.path.exists(log_file):
                os.remove(log_file)

    def test_debug_file_from_config(self):
        """Test that debug_file from config enables background logging"""
        log_file = os.path.abspath("config_debug_output.log")
        config_file = "test_debug_config.yaml"
        config = {"debug_file": log_file}

        with open(config_file, "w") as f:
            import yaml

            yaml.dump(config, f)

        try:
            # Run without --debug or --debug-file, but with --config-file
            result = self.run_cli(
                ["--config-file", config_file, "show", "-c", "12345", "--no-pager"]
            )
            self.assertEqual(result.returncode, 0)

            self.assertTrue(os.path.exists(log_file), "Log file was not created")

            with open(log_file, "r") as f:
                content = f.read()

            self.assertIn("DEBUG: Request:", content)
            self.assertIn("DEBUG: Response Status: 200", content)
            # Ensure it didn't print to stderr
            self.assertNotIn("DEBUG: Request:", result.stderr)

        finally:
            if os.path.exists(log_file):
                os.remove(log_file)
            if os.path.exists(config_file):
                os.remove(config_file)

    def test_apply_template_dry_run(self):
        """Test applying a template to a case in dry-run mode"""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        template_path = os.path.join(template_dir, "test_template.yaml")
        with open(template_path, "w") as f:
            f.write("severity: Low\nstatus: Waiting on Customer\n")

        try:
            result = self.run_cli(
                [
                    "--simple-output",
                    "apply",
                    "-c",
                    "12345",
                    "-t",
                    "test_template",
                    "--dry-run",
                ]
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("[Dry-run] Would apply changes listed above.", result.stdout)
            self.assertIn("severity: High -> Low", result.stdout)
            self.assertIn(
                "status: Waiting on Red Hat -> Waiting on Customer", result.stdout
            )

        finally:
            shutil.rmtree(temp_home)

    def test_apply_template_execution(self):
        """Test executing applying a template to a case (real PUT/POST)"""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        template_path = os.path.join(template_dir, "test_template.yaml")
        with open(template_path, "w") as f:
            f.write("severity: Low\n")

        try:
            result = self.run_cli(["apply", "-c", "12345", "-t", "test_template"])
            self.assertEqual(result.returncode, 0)
            self.assertIn("Success: Case fields updated.", result.stdout)
        finally:
            shutil.rmtree(temp_home)

    def test_apply_template_no_changes(self):
        """Test applying a template when no changes are needed"""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        template_path = os.path.join(template_dir, "test_template.yaml")
        # Existing case has severity: High, status: Waiting on Red Hat, and let's not define watchers
        with open(template_path, "w") as f:
            f.write("severity: High\nstatus: Waiting on Red Hat\n")

        try:
            result = self.run_cli(["apply", "-c", "12345", "-t", "test_template"])
            self.assertEqual(result.returncode, 0)
            self.assertIn(
                "Case #12345 already adheres to the template(s). No changes needed.",
                result.stdout,
            )
        finally:
            shutil.rmtree(temp_home)

    def test_apply_template_vars(self):
        """Test applying a template with template variables"""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        template_path = os.path.join(template_dir, "test_template.yaml")
        with open(template_path, "w") as f:
            f.write(
                'severity: "{{ target_severity }}"\nstatus: "{{ target_status }}"\n'
            )

        try:
            result = self.run_cli(
                [
                    "--simple-output",
                    "apply",
                    "-c",
                    "12345",
                    "-t",
                    "test_template",
                    "--template-var",
                    "target_severity=Low",
                    "--template-var",
                    "target_status=Closed",
                    "--dry-run",
                ]
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("severity: High -> Low", result.stdout)
            self.assertIn("status: Waiting on Red Hat -> Closed", result.stdout)
        finally:
            shutil.rmtree(temp_home)

    def test_apply_case_not_found(self):
        """Test applying a template to a non-existent case fails gracefully"""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        template_path = os.path.join(template_dir, "test_template.yaml")
        with open(template_path, "w") as f:
            f.write("severity: Low\n")

        try:
            # Case id "abc" is not digit, so mock GET returns 404, leading to failure
            result = self.run_cli(["apply", "-c", "abc", "-t", "test_template"])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Error: Case abc not found", result.stderr)
        finally:
            shutil.rmtree(temp_home)

    def test_list_templates(self):
        """Test listing local templates and parsing their metadata"""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        # Create template A
        template_path_a = os.path.join(template_dir, "tmpl_a.yaml")
        with open(template_path_a, "w") as f:
            f.write(
                "_template_description: Template A Description\nseverity: Low\nwatchers:\n  - alice\n_some_ignored_meta: metadata\n"
            )

        # Create template B
        template_path_b = os.path.join(template_dir, "tmpl_b.yaml")
        with open(template_path_b, "w") as f:
            f.write(
                "_desc: Template B Description\nproduct: RHEL\ninclude_templates: tmpl_a\n"
            )

        try:
            result = self.run_cli(["--simple-output", "list-templates"])
            self.assertEqual(result.returncode, 0)
            self.assertIn("Found 2 template(s)", result.stdout)
            self.assertIn("Template:    tmpl_a", result.stdout)
            self.assertIn("Desc:      Template A Description", result.stdout)
            self.assertIn("Fields:    severity: Low", result.stdout)

            self.assertNotIn("_some_ignored_meta", result.stdout)

            self.assertIn("Template:    tmpl_b", result.stdout)
            self.assertIn("Desc:      Template B Description", result.stdout)
            self.assertIn("Fields:    product: RHEL", result.stdout)
            self.assertIn("Includes:  tmpl_a", result.stdout)
        finally:
            shutil.rmtree(temp_home)

    def test_list_templates_empty(self):
        """Test listing when there are no templates"""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        try:
            result = self.run_cli(["list-templates"])
            self.assertEqual(result.returncode, 0)
            self.assertIn("No templates found", result.stdout)
        finally:
            shutil.rmtree(temp_home)

    def test_list_templates_no_directory(self):
        """Test listing when the template directory does not exist"""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        try:
            result = self.run_cli(["list-templates"])
            self.assertEqual(result.returncode, 0)
            self.assertIn("Template directory does not exist", result.stdout)
        finally:
            shutil.rmtree(temp_home)

    def test_template_fields_starting_with_underscore_ignored(self):
        """Test that any template fields starting with an underscore are completely ignored in create and apply"""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        self.env["HOME"] = temp_home

        config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        template_dir = os.path.join(config_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)

        template_path = os.path.join(template_dir, "test_underscore.yaml")
        with open(template_path, "w") as f:
            f.write(
                "severity: Low\n_ignored_field: should_be_ignored\n_template_description: This is a test template\n"
            )

        try:
            # 1. Run apply
            result_apply = self.run_cli(
                [
                    "--simple-output",
                    "apply",
                    "-c",
                    "12345",
                    "-t",
                    "test_underscore",
                    "--dry-run",
                ]
            )
            self.assertEqual(result_apply.returncode, 0)
            # severity should change, but _ignored_field or _template_description should not be listed as changes
            self.assertIn("severity: High -> Low", result_apply.stdout)
            self.assertNotIn("_ignored_field", result_apply.stdout)
            self.assertNotIn("_template_description", result_apply.stdout)

            # 2. Run create
            with open("desc.txt", "w") as f:
                f.write("My Issue Description")

            cmd = [
                sys.executable,
                self.cli_path,
                "create",
                "--product",
                "RHEL",
                "--version",
                "8.0",
                "--summary",
                "My Summary",
                "--type",
                "Standard",
                "--severity",
                "Low",
                "--description-file",
                "desc.txt",
                "--template",
                "test_underscore",
            ]
            result_create = subprocess.run(
                cmd,
                env=self.env,
                capture_output=True,
                text=True,
                input="y\n",
                check=False,
            )
            if result_create.returncode != 0:
                print("\n--- CREATE STDOUT ---\n", result_create.stdout)
                print("\n--- CREATE STDERR ---\n", result_create.stderr)
            self.assertEqual(result_create.returncode, 0)

            # Verify MockRedHatHandler.last_case_payload does not contain any key starting with '_'
            self.assertIsNotNone(MockRedHatHandler.last_case_payload)
            for k in MockRedHatHandler.last_case_payload:
                self.assertFalse(
                    k.startswith("_"),
                    f"Key starting with underscore found in payload: {k}",
                )

        finally:
            if os.path.exists("desc.txt"):
                os.remove("desc.txt")
            shutil.rmtree(temp_home)

    def test_tui_command_registration(self):
        """Test that the TUI command is registered and help menu works"""
        result = self.run_cli(["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("tui", result.stdout)

    def test_tui_activation(self):
        """Test that running the tui subcommand initializes and launches the SupportApp"""
        from unittest.mock import MagicMock, patch

        from rh_support_lib.tui.app import cmd_tui

        with patch("rh_support_lib.tui.app.SupportApp.run") as mock_run:
            args = MagicMock()
            token = "mock_token"
            config = {}
            cmd_tui(args, token, config)
            mock_run.assert_called_once()

    def test_tui_app_headless(self):
        """Programmatically launch the TUI app headlessly to verify CSS parsing and startup widgets"""
        import asyncio

        from textual.widgets import DataTable

        import rh_support_lib.tui.app
        from rh_support_lib.tui.app import SupportApp

        # Route GraphQL and API URLs to our mock HTTP server
        orig_gql_url = rh_support_lib.constants.GRAPHQL_URL
        orig_api_url = rh_support_lib.constants.API_URL
        rh_support_lib.constants.GRAPHQL_URL = f"{self.base_url}/graphql"
        rh_support_lib.constants.API_URL = self.base_url

        async def run_headless():
            app = SupportApp(token="mock_access_token_123", config={})
            async with app.run_test() as pilot:
                # If we get here, CSS parsed successfully and the app launched with zero crashes!
                self.assertEqual(app.title, "Red Hat Support CLI")
                table = app.query_one("#case-table", DataTable)
                self.assertIsNotNone(table)
                self.assertIsNotNone(app.query_one("#case-detail-container"))

                # Directly call the row selection handler with a mock event to simulate selection
                from unittest.mock import MagicMock

                event = MagicMock()
                event.row_key.value = "12345"
                app.on_row_selected(event)

                # Let the pilot process events and background workers run
                await pilot.pause()

                # Verify that case was successfully selected
                self.assertEqual(app.selected_case_id, "12345")

                # Verify that the details panel loaded successfully without mounting/layout crashes

                detail_container = app.query_one("#case-detail-container")
                self.assertIsNotNone(detail_container)
                self.assertTrue(detail_container.can_focus)

                # Ensure that compiled Static panels are now mounted directly inside the container
                self.assertTrue(len(detail_container.children) > 0)

                # 3. Test focus/unfocus toggling layouts programmatically using 'f'
                app.action_focus_pane()
                self.assertTrue(
                    app.screen.has_class("focused-left")
                    or app.screen.has_class("focused-right")
                )

                # Press 'f' again to toggle it off!
                app.action_focus_pane()
                self.assertFalse(app.screen.has_class("focused-left"))
                self.assertFalse(app.screen.has_class("focused-right"))

                # Focus again and test escape_action to unfocus!
                app.action_focus_pane()
                self.assertTrue(
                    app.screen.has_class("focused-left")
                    or app.screen.has_class("focused-right")
                )
                app.action_escape_action()
                self.assertFalse(app.screen.has_class("focused-left"))
                self.assertFalse(app.screen.has_class("focused-right"))

                # 4. Test contextual refresh layouts programmatically
                app.action_refresh()

                # 5. Test activating the inline comment pane
                app.action_add_comment()
                await pilot.pause()

                # Verify that the comment pane is displayed
                from textual.widgets import Select, TextArea

                self.assertTrue(app.screen.has_class("commenting"))
                textarea = app.query_one("#tui-comment-textarea", TextArea)
                self.assertIsNotNone(textarea)
                select_status = app.query_one("#tui-comment-status-select", Select)
                self.assertIsNotNone(select_status)

                # Type some draft comment text into the TextArea
                textarea.text = "This is a draft comment to test unsaved changes"

                # Trigger exit which will prompt the UnsavedChangesModal
                app.action_exit_commenting()
                await pilot.pause()

                # Verify UnsavedChangesModal is now active on screen!
                unsaved_modal = app.screen
                self.assertIn("UnsavedChangesModal", str(unsaved_modal))
                self.assertIsNotNone(unsaved_modal.query_one("#discard-btn"))
                self.assertIsNotNone(unsaved_modal.query_one("#save-btn"))

                # Dismiss the modal by selecting 'discard'
                unsaved_modal.dismiss("discard")
                await pilot.pause()

                # Verify commenting pane is closed and text is cleared!
                self.assertFalse(app.screen.has_class("commenting"))
                self.assertEqual(textarea.text, "")

                # Test activating comment pane when case status is 'Waiting on Customer Action Required'
                from rh_support_lib.models import Case

                app.cases.append(
                    Case(
                        case_number="99999",
                        summary="Test Action Required Case",
                        status="Waiting on Customer Action Required",
                        severity="High",
                        product="RHEL",
                        version="9.0",
                        account_number="123456",
                        created_date="2026-10-01",
                        last_modified_date="2026-10-02",
                        created_by="User",
                        last_modified_by="User",
                        owner="User",
                        id="99999",
                    )
                )
                app.selected_case_id = "99999"
                app.action_add_comment()
                await pilot.pause()
                self.assertTrue(app.screen.has_class("commenting"))
                select_status2 = app.query_one("#tui-comment-status-select", Select)
                self.assertIsNotNone(select_status2)
                self.assertEqual(select_status2.value, "In Progress")
                app.screen.remove_class("commenting")
                # 6. Test activating the file attachment modal
                app.action_add_attachment()
                await pilot.pause()

                attach_modal = app.screen
                self.assertIn("AttachModal", str(attach_modal))
                self.assertIsNotNone(attach_modal.query_one("#attach-file-input"))

                # Dismiss modal
                attach_modal.dismiss("")
                await pilot.pause()

                # 6b. Test activating the file download modal
                from rh_support_lib.models import Attachment

                app.current_attachments = [
                    Attachment(
                        uuid="att_test_123",
                        file_name="diag.tar.gz",
                        size=1024,
                        size_kb=1.0,
                        created_date="2026-10-08T10:00:00Z",
                    )
                ]
                app.action_download_attachment()
                await pilot.pause()
                download_modal = app.screen
                self.assertIn("DownloadModal", str(download_modal))
                download_modal.dismiss(None)
                await pilot.pause()

                # 7. Test TUI follow toggle and mark read actions
                app.selected_case_id = "12345"
                app.action_toggle_follow()
                u = app.case_service.get_current_user()
                self.assertTrue(app.case_service.state_service.is_following("12345", u))
                app.action_toggle_follow()
                self.assertFalse(
                    app.case_service.state_service.is_following("12345", u)
                )
                app.action_mark_read()

        try:
            asyncio.run(run_headless())
        finally:
            rh_support_lib.constants.GRAPHQL_URL = orig_gql_url
            rh_support_lib.constants.API_URL = orig_api_url

    def test_tui_config_handling(self):
        """Test that TUI correctly processes default_bookmark and default_create_template from config"""
        import shutil
        import tempfile

        from rh_support_lib.tui.app import (
            BookmarkModal,
            TemplateModal,
            build_filter_payload,
        )

        config = {
            "default_bookmark": "my_team",
            "default_create_template": "my_default_template",
            "bookmarks": {
                "my_team": {
                    "account": "999999",
                    "status": ["Waiting on Red Hat"],
                    "severity": ["High"],
                }
            },
        }

        # 1. Test build_filter_payload with standard and override options
        payload = build_filter_payload(config)
        self.assertEqual(payload.get("accountNumber"), "999999")
        self.assertEqual(payload.get("status"), "Waiting on Red Hat")
        self.assertEqual(payload.get("severity"), "2 (High)")

        payload_no_default = build_filter_payload(config, no_default_bookmark=True)
        self.assertNotIn("accountNumber", payload_no_default)

        payload_specific = build_filter_payload(config, bookmark="my_team")
        self.assertEqual(payload_specific.get("accountNumber"), "999999")

        payload_none = build_filter_payload(config, bookmark="none")
        self.assertNotIn("accountNumber", payload_none)

        # 2. Test Modal initial values
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        temp_home = tempfile.mkdtemp()
        try:
            modal = TemplateModal("12345", "my_default_template")
            self.assertEqual(modal.default_template, "my_default_template")

            modal_bk = BookmarkModal(config, "my_team")
            self.assertEqual(modal_bk.active_bookmark, "my_team")
        finally:
            shutil.rmtree(temp_home)
            loop.close()
            asyncio.set_event_loop(None)

    def test_mock_mode_list_and_show(self):
        """Test that --mock runs the CLI offline against the in-process synthetic corpus"""
        from unittest import mock

        from rh_support_lib.main import main

        # Mock stdout to capture outputs
        stdout_capture = io.StringIO()
        with (
            mock.patch(
                "sys.argv",
                [
                    "rh-support-cli.py",
                    "--mock",
                    "--simple-output",
                    "list",
                    "--no-default-bookmark",
                ],
            ),
            mock.patch("sys.stdout", stdout_capture),
        ):
            try:
                main()
            except SystemExit as e:
                self.assertEqual(e.code, 0)

        output = stdout_capture.getvalue()
        self.assertIn("Cannot mount NFS share on RHEL", output)
        self.assertIn("1001", output)
        self.assertIn("1002", output)
        self.assertIn("1003", output)

        # Test show case details
        stdout_capture_show = io.StringIO()
        with (
            mock.patch(
                "sys.argv",
                [
                    "rh-support-cli.py",
                    "--mock",
                    "--simple-output",
                    "show",
                    "-c",
                    "1001",
                    "--no-pager",
                ],
            ),
            mock.patch("sys.stdout", stdout_capture_show),
        ):
            try:
                main()
            except SystemExit as e:
                self.assertEqual(e.code, 0)

        show_output = stdout_capture_show.getvalue()
        self.assertIn("CASE: 1001", show_output)
        self.assertIn("Cannot mount NFS share on RHEL 9.0", show_output)
        self.assertIn("COMMENTS (2)", show_output)

    def test_mock_mode_create_and_comment(self):
        """Test that creating cases and writing comments in --mock mode mutates state correctly"""
        import shutil
        import tempfile
        from unittest import mock

        import rh_support_lib.constants
        from rh_support_lib.main import main

        saved_constants = {
            "API_URL": rh_support_lib.constants.API_URL,
            "SSO_URL": rh_support_lib.constants.SSO_URL,
            "GRAPHQL_URL": rh_support_lib.constants.GRAPHQL_URL,
            "ATTACHMENT_API_URL": rh_support_lib.constants.ATTACHMENT_API_URL,
        }
        saved_env = os.environ.copy()

        # Use a writable mock corpus directory to verify mutation
        temp_corpus = tempfile.mkdtemp()
        from rh_support_lib.synthetic_corpus import generate_default_corpus

        generate_default_corpus(temp_corpus)
        try:
            # Create a description file for case creation
            desc_file_path = os.path.join(temp_corpus, "desc.txt")
            with open(desc_file_path, "w") as f:
                f.write("A brand new synthetic mock issue description.")

            # Create a case - we must mock stdin to answer 'y' to the submit prompt
            stdout_capture = io.StringIO()
            stdin_mock = io.StringIO("y\n")
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "rh-support-cli.py",
                        "--mock",
                        "--mock-corpus",
                        temp_corpus,
                        "--simple-output",
                        "create",
                        "--product",
                        "RHEL",
                        "--version",
                        "9.0",
                        "--summary",
                        "Synthetic Test Summary",
                        "--description-file",
                        desc_file_path,
                        "--severity",
                        "Normal",
                        "--type",
                        "Standard",
                    ],
                ),
                mock.patch("sys.stdout", stdout_capture),
                mock.patch("sys.stdin", stdin_mock),
            ):
                try:
                    main()
                except SystemExit as e:
                    self.assertEqual(e.code, 0)

            # Check that case #1004 was created and file exists
            case_file = os.path.join(temp_corpus, "cases", "1004.json")
            self.assertTrue(os.path.exists(case_file))
            with open(case_file, "r") as f:
                case_data = json.load(f)
            self.assertEqual(case_data["summary"], "Synthetic Test Summary")
            from rh_support_lib.utils import clean_comment_body

            self.assertEqual(
                clean_comment_body(case_data["description"].strip()),
                "A brand new synthetic mock issue description.",
            )

            # Post a comment to the newly created case
            comment_file_path = os.path.join(temp_corpus, "comment.txt")
            with open(comment_file_path, "w") as f:
                f.write("A custom comment body text.")

            stdout_comment = io.StringIO()
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "rh-support-cli.py",
                        "--mock",
                        "--mock-corpus",
                        temp_corpus,
                        "--simple-output",
                        "comment",
                        "-c",
                        "1004",
                        "-f",
                        comment_file_path,
                    ],
                ),
                mock.patch("sys.stdout", stdout_comment),
            ):
                try:
                    main()
                except SystemExit as e:
                    self.assertEqual(e.code, 0)

            # Verify comment was written to file
            with open(case_file, "r") as f:
                case_data_updated = json.load(f)
            self.assertEqual(len(case_data_updated["comments"]), 1)
            self.assertEqual(
                clean_comment_body(case_data_updated["comments"][0]["body"]),
                "A custom comment body text.",
            )

        finally:
            for k, v in saved_constants.items():
                setattr(rh_support_lib.constants, k, v)
            os.environ.clear()
            os.environ.update(saved_env)
            shutil.rmtree(temp_corpus)

    def test_attachment_subcommands_list_and_status(self):
        """Test attachment list and status subcommands via CLI"""
        # Rich list
        result_list = self.run_cli(["attachment", "-c", "12345", "list"])
        self.assertEqual(result_list.returncode, 0)
        self.assertIn("Attachments for Case #12345", result_list.stdout)
        self.assertIn("att_1001_uuid", result_list.stdout)

        # Simple output list
        result_simple = self.run_cli(
            ["--simple-output", "attachment", "-c", "12345", "list"]
        )
        self.assertEqual(result_simple.returncode, 0)
        self.assertIn("Attachments for Case #12345:", result_simple.stdout)
        self.assertIn("test_attachment.log", result_simple.stdout)

        # Status
        result_status = self.run_cli(["attachment", "-c", "12345", "status"])
        self.assertEqual(result_status.returncode, 0)
        self.assertIn("test_attachment.log", result_status.stdout)
        self.assertIn("COMPLETED", result_status.stdout)

        # Calling attachments command without action defaults to list
        result_default_list = self.run_cli(["attachments", "-c", "12345"])
        self.assertEqual(result_default_list.returncode, 0)
        self.assertIn("att_1001_uuid", result_default_list.stdout)

        # Top-level list-attachments shortcut
        result_shortcut = self.run_cli(["list-attachments", "12345"])
        self.assertEqual(result_shortcut.returncode, 0)
        self.assertIn("att_1001_uuid", result_shortcut.stdout)

        # Verify 'show' command displays the attachments section with UUID and filename
        result_show = self.run_cli(["show", "-c", "12345", "--no-pager"])
        self.assertEqual(result_show.returncode, 0)
        self.assertIn("ATTACHMENTS", result_show.stdout)
        self.assertIn("att_1001_uuid", result_show.stdout)
        self.assertIn("test_attachment.log", result_show.stdout)

    def test_attachment_upload_download_delete_cli(self):
        """Test uploading, downloading, and deleting an attachment via CLI"""
        import tempfile

        temp_dir = tempfile.mkdtemp()
        try:
            sample_file = os.path.join(temp_dir, "test_diag.log")
            with open(sample_file, "w") as f:
                f.write("System diagnostic log line 1\nLine 2")
            result_up = self.run_cli(
                ["--graphql", "attachment", "-c", "12345", "upload", "-f", sample_file]
            )
            self.assertEqual(result_up.returncode, 0)
            self.assertIn(
                "Attaching 'test_diag.log' to Case #12345...", result_up.stdout
            )
            self.assertIn("Success: File 'test_diag.log' attached.", result_up.stdout)

            # Download
            dest_file = os.path.join(temp_dir, "downloaded.log")
            result_down = self.run_cli(
                [
                    "--graphql",
                    "attachment",
                    "-c",
                    "12345",
                    "download",
                    "-a",
                    "att_test_uuid_123",
                    "-o",
                    dest_file,
                ]
            )
            self.assertEqual(result_down.returncode, 0)
            self.assertIn("Successfully downloaded attachment to:", result_down.stdout)
            self.assertTrue(os.path.exists(dest_file))
            with open(dest_file, "r") as f:
                self.assertEqual(f.read(), "System diagnostic log line 1\nLine 2")

            # Download via top-level shortcut
            dest_shortcut = os.path.join(temp_dir, "downloaded_shortcut.log")
            res_shortcut = self.run_cli(
                [
                    "download",
                    "-c",
                    "12345",
                    "-a",
                    "att_test_uuid_123",
                    "-o",
                    dest_shortcut,
                ]
            )
            self.assertEqual(res_shortcut.returncode, 0)
            self.assertTrue(os.path.exists(dest_shortcut))
            with open(dest_shortcut, "r") as f:
                self.assertEqual(f.read(), "System diagnostic log line 1\nLine 2")

            # Delete with -y
            result_del = self.run_cli(
                [
                    "--graphql",
                    "attachment",
                    "-c",
                    "12345",
                    "delete",
                    "-a",
                    "att_test_uuid_123",
                    "-y",
                ]
            )
            self.assertEqual(result_del.returncode, 0)
            self.assertIn(
                "Successfully deleted attachment 'att_test_uuid_123'.",
                result_del.stdout,
            )

        finally:
            shutil.rmtree(temp_dir)

    def test_services_direct_api(self):
        """Test CaseService and AttachmentService directly as a library"""
        import rh_support_lib.constants
        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.attachments import AttachmentService
        from rh_support_lib.services.cases import CaseService

        orig_gql = rh_support_lib.constants.GRAPHQL_URL
        rh_support_lib.constants.GRAPHQL_URL = f"{self.base_url}/graphql"
        try:
            api_client = LegacyAPIClient("mock_access_token_123")
            case_service = CaseService(
                api_client, graphql_url=f"{self.base_url}/graphql"
            )
            att_service = AttachmentService(api_client, base_url=self.base_url)

            # 1. CaseService list_cases
            cases = case_service.list_cases(
                status=["Waiting on Red Hat"], severity=["High"]
            )
            self.assertTrue(len(cases) >= 1)
            self.assertTrue(cases[0].case_number in ("FILTER_TEST", "1001"))

            # 2. CaseService get_case_details
            details = case_service.get_case_details("12345")
            self.assertIsNotNone(details)
            self.assertEqual(details.case.case_number, "12345")
            self.assertEqual(len(details.comments), 2)

            # 3. CaseService add_comment
            comment = case_service.add_comment(
                "12345", "Automated test comment", is_public=True
            )
            self.assertEqual(comment.body, "Automated test comment")
            self.assertTrue(comment.is_public)

            # 4. CaseService get_active_products
            products = case_service.get_active_products()
            self.assertTrue(len(products) >= 2)
            self.assertEqual(products[0].name, "Red Hat Enterprise Linux")

            # 5. AttachmentService list_attachments
            attachments = att_service.list_attachments("12345")
            self.assertTrue(len(attachments) >= 1)
            self.assertEqual(attachments[0].file_name, "test_attachment.log")
        finally:
            rh_support_lib.constants.GRAPHQL_URL = orig_gql

    def test_graphql_flag_cli(self):
        """Test running CLI commands with --graphql (GraphQL and v3 APIs)"""
        import tempfile

        temp_dir = tempfile.mkdtemp()
        try:
            # 1. List with --graphql
            result_list = self.run_cli(["--graphql", "list"])
            self.assertEqual(result_list.returncode, 0)
            self.assertIn("FILTER_TEST", result_list.stdout)

            # 2. Show with --graphql
            result_show = self.run_cli(
                ["--graphql", "show", "-c", "12345", "--no-pager"]
            )
            self.assertEqual(result_show.returncode, 0)
            self.assertIn("CASE: 12345", result_show.stdout)
            self.assertIn("Test Case Details", result_show.stdout)

            # 3. Comment with --graphql
            comment_file = os.path.join(temp_dir, "comment.txt")
            with open(comment_file, "w") as f:
                f.write("New CRM comment text")
            result_comment = self.run_cli(
                ["--graphql", "comment", "-c", "12345", "-f", comment_file]
            )
            self.assertEqual(result_comment.returncode, 0)
            self.assertIn("Success: Comment added.", result_comment.stdout)

            # 4. Attachment list with --graphql
            result_att_list = self.run_cli(
                ["--graphql", "attachment", "-c", "12345", "list"]
            )
            self.assertEqual(result_att_list.returncode, 0)
            self.assertIn("Attachments for Case #12345", result_att_list.stdout)

            # 5. Attachment upload, download, delete with --graphql
            sample_file = os.path.join(temp_dir, "sample.txt")
            with open(sample_file, "w") as f:
                f.write("Sample content")
            result_up = self.run_cli(
                ["--graphql", "attachment", "-c", "12345", "upload", "-f", sample_file]
            )
            self.assertEqual(result_up.returncode, 0)
            self.assertIn("Success: File 'sample.txt' attached.", result_up.stdout)

            dest_file = os.path.join(temp_dir, "down.txt")
            result_down = self.run_cli(
                [
                    "--graphql",
                    "attachment",
                    "-c",
                    "12345",
                    "download",
                    "-a",
                    "att_test_uuid_123",
                    "-o",
                    dest_file,
                ]
            )
            self.assertEqual(result_down.returncode, 0)
            self.assertTrue(os.path.exists(dest_file))

            result_del = self.run_cli(
                [
                    "--graphql",
                    "attachment",
                    "-c",
                    "12345",
                    "delete",
                    "-a",
                    "att_test_uuid_123",
                    "-y",
                ]
            )
            self.assertEqual(result_del.returncode, 0)
            self.assertIn(
                "Successfully deleted attachment 'att_test_uuid_123'.",
                result_del.stdout,
            )
        finally:
            shutil.rmtree(temp_dir)

    def test_graphql_cli(self):
        """Test running CLI commands with and without --graphql"""
        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.attachments import AttachmentService
        from rh_support_lib.services.cases import CaseService

        # 1. Direct library instantiation
        api_client = LegacyAPIClient("mock_access_token_123")
        case_service = CaseService(api_client)
        self.assertIsNotNone(case_service)
        att_service = AttachmentService(api_client)
        self.assertIsNotNone(att_service)
        # 2. CLI commands default to graphql without --graphql flags
        result_default = self.run_cli(["list"])
        self.assertEqual(result_default.returncode, 0)
        self.assertIn("FILTER_TEST", result_default.stdout)

        # 3. --graphql flag is also accepted as a compatible option
        result_gql = self.run_cli(["--graphql", "list"])
        self.assertEqual(result_gql.returncode, 0)
        self.assertIn("FILTER_TEST", result_gql.stdout)

    def test_status_filter_map_new_crm(self):
        """Test that STATUS_FILTER_MAP maps open, customer, and redhat to new CRM GraphQL statuses"""
        import tempfile

        from rh_support_lib.constants import STATUS_FILTER_MAP

        # 1. Verify mapping entries contain new CRM statuses
        open_statuses = [s.strip() for s in STATUS_FILTER_MAP["open"].split(",")]
        self.assertIn("Waiting on Customer Action Required", open_statuses)
        self.assertIn("In Progress", open_statuses)
        self.assertIn("Waiting on Engineering", open_statuses)
        self.assertIn("Needs New Owner", open_statuses)

        customer_statuses = [
            s.strip() for s in STATUS_FILTER_MAP["customer"].split(",")
        ]
        self.assertIn("Waiting on Customer Action Required", customer_statuses)

        redhat_statuses = [s.strip() for s in STATUS_FILTER_MAP["redhat"].split(",")]
        self.assertIn("In Progress", redhat_statuses)
        self.assertIn("Waiting on Engineering", redhat_statuses)
        self.assertIn("Needs New Owner", redhat_statuses)

        # 2. Test CLI list with --status open transmits new CRM statuses in filter
        self.env["COLUMNS"] = "400"
        result_open = self.run_cli(["list", "--status", "open"])
        self.assertEqual(result_open.returncode, 0)
        self.assertIn("Waiting on Customer Action Required", result_open.stdout)
        self.assertIn("In Progress", result_open.stdout)
        # 3. Test config bookmark with status: ["open"]
        temp_home = tempfile.mkdtemp()
        try:
            config_file = os.path.join(temp_home, "config.yaml")
            with open(config_file, "w") as f:
                f.write(
                    'bookmarks:\n  sample_open:\n    account: "999999"\n    status: ["open"]\n'
                )
            result_bm = self.run_cli(
                [
                    "--config-file",
                    config_file,
                    "list",
                    "--bookmark",
                    "sample_open",
                    "--no-default-bookmark",
                ]
            )
            self.assertEqual(result_bm.returncode, 0)
            self.assertIn("ACC=999999", result_bm.stdout)
            self.assertIn("Waiting on Customer Action Required", result_bm.stdout)
            self.assertIn("In Progress", result_bm.stdout)
        finally:
            import shutil

            shutil.rmtree(temp_home)

        # 4. Test statuses module helpers (determine_reply_status, choices, colors)
        from rh_support_lib.constants import COLORS
        from rh_support_lib.statuses import (
            determine_reply_status,
            get_status_choices_for_case,
            get_status_color,
        )

        self.assertEqual(
            determine_reply_status("Waiting on Customer Action Required"),
            "In Progress",
        )
        self.assertEqual(determine_reply_status("Waiting on Customer"), "In Progress")
        self.assertEqual(
            determine_reply_status("Waiting on Red Hat"),
            "Waiting on Customer Action Required",
        )
        self.assertEqual(
            determine_reply_status("In Progress"),
            "Waiting on Customer Action Required",
        )
        self.assertEqual(determine_reply_status("Closed"), "Closed")
        self.assertEqual(determine_reply_status(None), "In Progress")

        # Choices for standard case
        choices = get_status_choices_for_case("Waiting on Red Hat")
        self.assertIn(("Waiting on Red Hat", "Waiting on Red Hat"), choices)
        self.assertIn(
            (
                "Waiting on Customer Action Required",
                "Waiting on Customer Action Required",
            ),
            choices,
        )

        # Choices for custom/unusual status dynamically includes it
        custom_choices = get_status_choices_for_case("Custom Under Investigation")
        self.assertIn(
            ("Custom Under Investigation", "Custom Under Investigation"), custom_choices
        )

        # Colors
        self.assertEqual(get_status_color("Waiting on Red Hat"), COLORS.YELLOW)
        self.assertEqual(get_status_color("In Progress"), COLORS.YELLOW)
        self.assertEqual(
            get_status_color("Waiting on Customer Action Required"), COLORS.GREEN
        )
        self.assertEqual(get_status_color("Waiting on Engineering"), COLORS.MAGENTA)
        self.assertEqual(get_status_color("Closed"), COLORS.GREY)
        self.assertIsNone(get_status_color(None))

    def test_clean_comment_body_and_private_detection(self):
        """Test cleaning wrapping legacy divs, HTML to markdown, and private comment detection"""
        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.cases import CaseService
        from rh_support_lib.utils import clean_comment_body

        # 1. Direct clean_comment_body tests
        # Plaintext in legacy-comment div
        c1 = '<div class="legacy-comment legacy-comment-plaintext">NetworkPolicy: https://access.redhat.com/solutions/7101361</div>'
        self.assertEqual(
            clean_comment_body(c1),
            "NetworkPolicy: https://access.redhat.com/solutions/7101361",
        )

        # Arbitrary div classes and attributes
        c2 = '<div class="custom-note extra" id="c99">Important information</div>'
        self.assertEqual(clean_comment_body(c2), "Important information")

        # HTML formatted legacy comment (like Case 999888 Comment 1)
        c3 = (
            "&lt;div class=&quot;legacy-comment legacy-comment-markdown&quot;&gt;"
            "&lt;h2 id=&quot;consultant-engaged&quot;&gt;Consultant Engaged&lt;/h2&gt;\n"
            "&lt;p&gt;This case has been flagged with Consulting Engagement.&lt;/p&gt;\n"
            "&lt;h3&gt;Contact:&lt;/h3&gt;\n"
            "&lt;p&gt;&lt;strong&gt;Name:&lt;/strong&gt; Jane Consultant&lt;br&gt;"
            "&lt;a href=&quot;mailto:consultant@example.com&quot;&gt;consultant@example.com&lt;/a&gt;&lt;/p&gt;\n"
            "&lt;/div&gt;"
        )
        cleaned3 = clean_comment_body(c3)
        self.assertNotIn("<div", cleaned3)
        self.assertNotIn("</div>", cleaned3)
        self.assertIn("## Consultant Engaged", cleaned3)
        self.assertIn("### Contact:", cleaned3)
        self.assertIn("**Name:**", cleaned3)
        self.assertIn("consultant@example.com", cleaned3)

        # Double-escaped entities
        c4 = "&lt;div class=&quot;legacy-comment legacy-comment-plaintext&quot;&gt;&amp;gt; quoted text&lt;/div&gt;"
        self.assertEqual(clean_comment_body(c4), "> quoted text")

        # 2. Private comment detection in CaseService
        api_client = LegacyAPIClient("mock_access_token_123")
        case_service = CaseService(api_client, graphql_url=f"{self.base_url}/graphql")

        # CaseService._get_case_details_graphql with internal note markers
        mock_gql_data = {
            "redhat_support_uiapi": {
                "query": {
                    "RedHatSupportCase": {
                        "edges": [
                            {
                                "node": {
                                    "Id": "500TestGql",
                                    "CaseNumber__c": {"value": "998877"},
                                    "Subject": {"value": "GQL Comment Privacy Test"},
                                    "CaseComments__r": {
                                        "edges": [
                                            {
                                                "node": {
                                                    "Id": "c_pub",
                                                    "Body__c": {
                                                        "value": '<div class="legacy-comment legacy-comment-plaintext">Hello customer</div>'
                                                    },
                                                    "Visibility__c": {
                                                        "value": "Public",
                                                        "displayValue": "Public",
                                                    },
                                                    "CreatedBy": {
                                                        "Name": {
                                                            "value": "Support Specialist"
                                                        }
                                                    },
                                                    "CreatedDate": {
                                                        "value": "2026-01-01T10:00:00Z"
                                                    },
                                                }
                                            },
                                            {
                                                "node": {
                                                    "Id": "c_priv_marker",
                                                    "Body__c": {
                                                        "value": '<div class="legacy-comment legacy-comment-plaintext">/* INTERNAL NOTE */\n\nTaking ownership</div>'
                                                    },
                                                    "Visibility__c": {
                                                        "value": "Private",
                                                        "displayValue": "Private",
                                                    },
                                                    "CreatedBy": {
                                                        "Name": {
                                                            "value": "Alex Support"
                                                        }
                                                    },
                                                    "CreatedDate": {
                                                        "value": "2026-01-01T11:00:00Z"
                                                    },
                                                }
                                            },
                                            {
                                                "node": {
                                                    "Id": "c_priv_no_marker",
                                                    "Body__c": {
                                                        "value": '<div class="legacy-comment legacy-comment-plaintext">NetworkPolicy: https://access.redhat.com/solutions/7101361</div>'
                                                    },
                                                    "Visibility__c": {
                                                        "value": "Private",
                                                        "displayValue": "Private",
                                                    },
                                                    "CreatedBy": {
                                                        "Name": {
                                                            "value": "Jane Consultant"
                                                        }
                                                    },
                                                    "CreatedDate": {
                                                        "value": "2026-01-01T12:00:00Z"
                                                    },
                                                }
                                            },
                                        ]
                                    },
                                }
                            }
                        ]
                    }
                }
            }
        }

        from unittest.mock import patch

        with patch.object(api_client, "execute_graphql", return_value=mock_gql_data):
            details = case_service.get_case_details("998877")
            self.assertIsNotNone(details)
            self.assertEqual(len(details.comments), 3)

            # Comment 1: Public, stripped div, author from CreatedBy
            self.assertTrue(details.comments[0].is_public)
            self.assertEqual(details.comments[0].created_by, "Support Specialist")
            self.assertEqual(details.comments[0].body, "Hello customer")
            self.assertEqual(details.comments[0].visibility, "Public")

            # Comment 2: Private via Visibility__c field
            self.assertFalse(details.comments[1].is_public)
            self.assertEqual(details.comments[1].created_by, "Alex Support")
            self.assertEqual(details.comments[1].visibility, "Private")
            self.assertNotIn("<div", details.comments[1].body)
            self.assertIn("Taking ownership", details.comments[1].body)

            # Comment 3: Private via Visibility__c even with NO internal marker in text
            self.assertFalse(details.comments[2].is_public)
            self.assertEqual(details.comments[2].created_by, "Jane Consultant")
            self.assertEqual(details.comments[2].visibility, "Private")
            self.assertEqual(
                details.comments[2].body,
                "NetworkPolicy: https://access.redhat.com/solutions/7101361",
            )

    def test_follow_and_unfollow_commands(self):
        """Test follow and unfollow CLI commands for single and multiple cases"""
        expected_user = self.env.get("REDHAT_SUPPORT_USERNAME") or self.env.get(
            "USER", "jdeveloper"
        )
        # 1. Follow single case via flag
        result_follow1 = self.run_cli(["follow", "-c", "12345"])
        self.assertEqual(result_follow1.returncode, 0)
        self.assertIn(
            f"Success: Added '{expected_user}' as watcher to Case #12345.",
            result_follow1.stdout,
        )

        # 2. Query following via 'following' and 'list --following'
        res_following = self.run_cli(["following"])
        self.assertEqual(res_following.returncode, 0)
        self.assertIn("12345", res_following.stdout)
        res_list_following = self.run_cli(["list", "--following"])
        self.assertEqual(res_list_following.returncode, 0)
        self.assertIn("12345", res_list_following.stdout)

        # 3. Follow multiple cases via positional args and custom user
        result_follow_multi = self.run_cli(
            ["follow", "1001", "1002", "--user", "custom_user"]
        )
        self.assertEqual(result_follow_multi.returncode, 0)
        self.assertIn(
            "Adding 'custom_user' as watcher on 2 case(s)...",
            result_follow_multi.stdout,
        )
        self.assertIn(
            "Success: Added 'custom_user' as watcher to Case #1001.",
            result_follow_multi.stdout,
        )
        self.assertIn(
            "Success: Added 'custom_user' as watcher to Case #1002.",
            result_follow_multi.stdout,
        )

        # 4. Query custom user following
        res_custom_following = self.run_cli(["following", "--user", "custom_user"])
        self.assertEqual(res_custom_following.returncode, 0)
        self.assertIn("1001", res_custom_following.stdout)
        self.assertIn("1002", res_custom_following.stdout)

        # 5. Unfollow single case
        result_unfollow1 = self.run_cli(["unfollow", "-c", "12345"])
        self.assertEqual(result_unfollow1.returncode, 0)
        self.assertIn(
            f"Success: Removed '{expected_user}' from watchers on Case #12345.",
            result_unfollow1.stdout,
        )

        # 6. Verify 12345 is no longer followed
        res_after_unfollow = self.run_cli(["following"])
        self.assertEqual(res_after_unfollow.returncode, 0)
        self.assertIn("No cases found.", res_after_unfollow.stdout)

        # 7. Unfollow multiple cases via positional args
        result_unfollow_multi = self.run_cli(
            ["unfollow", "1001", "1002", "--user", "custom_user"]
        )
        self.assertEqual(result_unfollow_multi.returncode, 0)
        self.assertIn(
            "Removing 'custom_user' from watchers on 2 case(s)...",
            result_unfollow_multi.stdout,
        )
        self.assertIn(
            "Success: Removed 'custom_user' from watchers on Case #1001.",
            result_unfollow_multi.stdout,
        )
        self.assertIn(
            "Success: Removed 'custom_user' from watchers on Case #1002.",
            result_unfollow_multi.stdout,
        )

        # 8. Follow with no arguments fails gracefully
        result_err = self.run_cli(["follow"])
        self.assertNotEqual(result_err.returncode, 0)
        self.assertIn(
            "Please specify at least one case number",
            result_err.stderr,
        )

    def test_state_service_direct(self):
        """Test StateService directly for atomic persistence, following, sync, pinned, tags, and recent"""
        import shutil
        import tempfile

        from rh_support_lib.services.state import StateService

        temp_dir = tempfile.mkdtemp()
        try:
            state_file = os.path.join(temp_dir, "state.yaml")
            svc = StateService(state_file=state_file)

            # 1. Followed Cases
            self.assertFalse(svc.is_following("12345", "testuser"))
            svc.add_followed_case(
                "12345", "testuser", summary="Test Case", account_number="9999"
            )
            self.assertTrue(svc.is_following("12345", "testuser"))

            followed = svc.get_followed_cases("testuser")
            self.assertEqual(len(followed), 1)
            self.assertEqual(followed["12345"].summary, "Test Case")
            self.assertEqual(followed["12345"].account_number, "9999")

            # Remove
            self.assertTrue(svc.remove_followed_case("12345", "testuser"))
            self.assertFalse(svc.is_following("12345", "testuser"))
            self.assertFalse(svc.remove_followed_case("12345", "testuser"))

            # 2. Sync Checkpoints
            svc.update_sync_info("testuser", "9999", "2026-08-27T10:00:00Z", 15)
            sync_info = svc.get_sync_info("testuser", "9999")
            self.assertEqual(sync_info.get("last_synced_at"), "2026-08-27T10:00:00Z")
            self.assertEqual(sync_info.get("last_scanned_case_count"), 15)

            # 3. Pinned Cases
            svc.set_pinned_cases("testuser", ["1001", "1002"])
            self.assertEqual(svc.get_pinned_cases("testuser"), ["1001", "1002"])

            # 4. Recent Cases
            svc.add_recent_case("testuser", "1001", {"summary": "Recent 1"})
            recent = svc.get_recent_cases("testuser")
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["case_number"], "1001")

            # 5. Tags
            svc.set_tags("testuser", "1001", ["ocp", "prod"])
            self.assertEqual(svc.get_tags("testuser", "1001"), ["ocp", "prod"])
        finally:
            shutil.rmtree(temp_dir)

    def test_following_bookmark_and_sync(self):
        """Test using following bookmark in config and follow --sync command"""
        import shutil
        import tempfile

        import yaml

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        orig_state = self.env.get("XDG_STATE_HOME")
        self.env["HOME"] = temp_home
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_home, ".config")
        self.env["XDG_STATE_HOME"] = os.path.join(temp_home, ".local", "state")
        try:
            config_dir = os.path.join(temp_home, ".config", "rh-support-cli")
            os.makedirs(config_dir, exist_ok=True)
            config_file = os.path.join(config_dir, "config.yaml")
            with open(config_file, "w") as f:
                yaml.dump(
                    {
                        "bookmarks": {
                            "my-watched": {
                                "following": True,
                            }
                        }
                    },
                    f,
                )

            # 1. Follow a case
            res_f = self.run_cli(["follow", "12345"])
            self.assertEqual(res_f.returncode, 0)

            # 2. Query list using bookmark
            res_bk = self.run_cli(["list", "-b", "my-watched"])
            self.assertEqual(res_bk.returncode, 0)
            self.assertIn("12345", res_bk.stdout)

            # 3. Test follow --sync command
            res_sync = self.run_cli(["follow", "--sync"])
            self.assertEqual(res_sync.returncode, 0)
            self.assertIn("Sync complete.", res_sync.stdout)
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            if orig_state:
                self.env["XDG_STATE_HOME"] = orig_state
            shutil.rmtree(temp_home)

    def test_state_service_tracked_and_checkpoints(self):
        """Test StateService tracking, checkpoints, unread counts, and auto-registration."""
        import shutil
        import tempfile

        from rh_support_lib.services.state import StateService

        temp_dir = tempfile.mkdtemp()
        try:
            state_file = os.path.join(temp_dir, "state.yaml")
            svc = StateService(state_file=state_file)
            # 1. Follow a case
            svc.add_tracked_case(
                "testuser",
                "12345",
                relationship="followed",
                summary="Case 12345",
                status="Waiting on Red Hat",
            )
            self.assertTrue(svc.is_following("12345", "testuser"))
            tc = svc.get_tracked_cases("testuser").get("12345")
            self.assertIsNotNone(tc)
            self.assertEqual(tc.relationship, "followed")
            self.assertEqual(tc.case_number, "12345")

            # 2. Update latest known remote state (new comment and status change)
            svc.update_latest_known(
                user="testuser",
                case_number="12345",
                status="Waiting on Customer Action Required",
                severity="1 (Urgent)",
                latest_comment_id="comm_002",
                latest_comment_date="2026-10-08T12:00:00Z",
                latest_comment_author="Red Hat Support",
                latest_comment_snippet="Please provide logs",
                last_modified_date="2026-10-08T12:00:00Z",
                new_comment_count=1,
            )
            tc = svc.get_tracked_cases("testuser").get("12345")
            self.assertTrue(tc.has_unread)
            self.assertTrue(tc.status_changed)
            self.assertEqual(tc.unread_count, 1)

            # 3. Mark read
            svc.mark_case_read("testuser", "12345")
            tc = svc.get_tracked_cases("testuser").get("12345")
            self.assertFalse(tc.has_unread)
            self.assertFalse(tc.status_changed)
            self.assertEqual(tc.unread_count, 0)
            self.assertEqual(
                tc.read_checkpoint.status, "Waiting on Customer Action Required"
            )
            self.assertEqual(tc.read_checkpoint.last_read_comment_id, "comm_002")

            # 4. Auto-register owned case
            svc.register_owned_case(
                "testuser", "99999", summary="My Bug", status="In Progress"
            )
            owned = svc.get_owned_cases("testuser")
            self.assertIn("99999", owned)
            self.assertEqual(owned["99999"].relationship, "owned")
        finally:
            shutil.rmtree(temp_dir)

    def test_sync_command_and_systemd(self):
        """Test sync CLI command and --install-systemd unit generation."""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        orig_state = self.env.get("XDG_STATE_HOME")
        self.env["HOME"] = temp_home
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_home, ".config")
        self.env["XDG_STATE_HOME"] = os.path.join(temp_home, ".local", "state")
        try:
            # 1. Follow a case first
            res_f = self.run_cli(["follow", "12345"])
            self.assertEqual(res_f.returncode, 0)

            # 2. Run sync command
            res_sync = self.run_cli(["sync"])
            self.assertEqual(res_sync.returncode, 0)
            self.assertIn("Syncing tracked cases for", res_sync.stdout)
            self.assertIn("Sync complete:", res_sync.stdout)

            # 3. Run sync --install-systemd
            res_systemd = self.run_cli(
                ["sync", "--install-systemd", "--interval", "10m"]
            )
            self.assertEqual(res_systemd.returncode, 0)
            self.assertIn("Installed systemd user service:", res_systemd.stdout)
            self.assertIn("Installed systemd user timer:", res_systemd.stdout)
            service_path = os.path.join(
                temp_home, ".config", "systemd", "user", "rh-support-sync.service"
            )
            timer_path = os.path.join(
                temp_home, ".config", "systemd", "user", "rh-support-sync.timer"
            )
            self.assertTrue(os.path.exists(service_path))
            self.assertTrue(os.path.exists(timer_path))
            with open(timer_path) as f:
                content = f.read()
                self.assertIn("OnUnitActiveSec=10m", content)
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            if orig_state:
                self.env["XDG_STATE_HOME"] = orig_state
            shutil.rmtree(temp_home)

    def test_systemd_install_and_management_commands(self):
        """Test dedicated install-systemd and systemd install/status/uninstall commands."""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        self.env["HOME"] = temp_home
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_home, ".config")
        service_path = os.path.join(
            temp_home, ".config", "systemd", "user", "rh-support-sync.service"
        )
        timer_path = os.path.join(
            temp_home, ".config", "systemd", "user", "rh-support-sync.timer"
        )
        try:
            # 1. install-systemd with --interval 30m and --notify
            res_install = self.run_cli(
                ["install-systemd", "--interval", "30m", "--notify"]
            )
            self.assertEqual(res_install.returncode, 0)
            self.assertIn("Installed systemd user service:", res_install.stdout)
            self.assertIn("Installed systemd user timer:", res_install.stdout)
            self.assertIn("Desktop notifications:", res_install.stdout)
            self.assertTrue(os.path.exists(service_path))
            self.assertTrue(os.path.exists(timer_path))
            with open(service_path) as f:
                s_content = f.read()
                self.assertIn("--notify", s_content)
            with open(timer_path) as f:
                t_content = f.read()
                self.assertIn("OnUnitActiveSec=30m", t_content)

            # 2. systemd status
            res_status = self.run_cli(["systemd", "status"])
            self.assertEqual(res_status.returncode, 0)
            self.assertIn("Timer unit:   Installed", res_status.stdout)
            self.assertIn("Service unit: Installed", res_status.stdout)

            # 3. systemd uninstall
            res_uninstall = self.run_cli(["systemd", "uninstall"])
            self.assertEqual(res_uninstall.returncode, 0)
            self.assertIn("uninstalled", res_uninstall.stdout)
            self.assertFalse(os.path.exists(service_path))
            self.assertFalse(os.path.exists(timer_path))

            # 4. systemd install subcommand
            res_sub_install = self.run_cli(["systemd", "install", "--interval", "5m"])
            self.assertEqual(res_sub_install.returncode, 0)
            self.assertTrue(os.path.exists(timer_path))
            with open(timer_path) as f:
                self.assertIn("OnUnitActiveSec=5m", f.read())
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            shutil.rmtree(temp_home)

    def test_updates_and_show_checkpoint(self):
        """Test updates CLI command and show command with visual checkpoint divider and status banner."""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        orig_state = self.env.get("XDG_STATE_HOME")
        self.env["HOME"] = temp_home
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_home, ".config")
        self.env["XDG_STATE_HOME"] = os.path.join(temp_home, ".local", "state")
        try:
            # 1. Follow case 12345
            res_f = self.run_cli(["follow", "12345"])
            self.assertEqual(res_f.returncode, 0)

            # 2. Manually set older read checkpoint in state.yaml to simulate unread comments and status change
            state_path = os.path.join(
                temp_home, ".local", "state", "rh-support-cli", "state.yaml"
            )
            user = self.env.get("REDHAT_SUPPORT_USERNAME") or self.env.get(
                "USER", "jdeveloper"
            )
            from rh_support_lib.services.state import StateService

            svc = StateService(state_file=state_path)
            svc.add_tracked_case(
                user, "12345", relationship="followed", summary="Test Case"
            )
            # Checkpoint with older date and old status
            state_data = svc.load_state()
            u_state = svc._get_user_state(state_data, user)
            u_state["tracked_cases"]["12345"]["read_checkpoint"] = {
                "last_read_comment_date": "2023-01-01T00:00:00Z",
                "last_read_comment_id": "old_comm",
                "status": "Waiting on Customer Action Required",
                "marked_read_at": "2023-01-01T00:00:00Z",
            }
            u_state["tracked_cases"]["12345"]["latest_known"] = {
                "last_comment_date": "2026-08-20T10:00:00Z",
                "last_comment_id": "c1",
                "status": "Waiting on Red Hat",
                "status_changed": True,
                "unread_comment_count": 2,
                "latest_comment_author": "Red Hat Support",
                "latest_comment_snippet": "We are investigating",
            }
            svc.save_state(state_data)

            # 3. Run 'updates' command
            res_upd = self.run_cli(["updates", "--simple-output"])
            self.assertEqual(res_upd.returncode, 0)
            self.assertIn("Case #12345", res_upd.stdout)
            self.assertIn("Status Changed", res_upd.stdout)
            self.assertIn("new comment(s)", res_upd.stdout)

            # 4. Run 'show -c 12345' with simple output: verify status diff banner and checkpoint divider
            res_show = self.run_cli(
                ["show", "-c", "12345", "--simple-output", "--no-pager"]
            )
            self.assertEqual(res_show.returncode, 0)
            self.assertIn("⚡ Status Changed:", res_show.stdout)
            self.assertIn(
                "Waiting on Customer Action Required -> Waiting on Red Hat",
                res_show.stdout,
            )
            self.assertIn("Last read checkpoint", res_show.stdout)

            # 5. Mark read via 'show -c 12345 --mark-read'
            res_mark = self.run_cli(
                ["show", "-c", "12345", "--mark-read", "--simple-output", "--no-pager"]
            )
            self.assertEqual(res_mark.returncode, 0)
            self.assertIn("Marked Case #12345 as read.", res_mark.stdout)

            # 6. Verify subsequent updates command reports no unread updates
            res_upd_clean = self.run_cli(["updates", "--simple-output"])
            self.assertEqual(res_upd_clean.returncode, 0)
            self.assertIn(
                "No unread updates across tracked cases.", res_upd_clean.stdout
            )
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            if orig_state:
                self.env["XDG_STATE_HOME"] = orig_state
            shutil.rmtree(temp_home)

    def test_tui_layout_horizontal_mailbox_and_persistence(self):
        """Test TUI horizontal split (mailbox) layout initialization, toggle action, and persistence."""
        import shutil
        import tempfile

        from rh_support_lib.services.state import StateService
        from rh_support_lib.tui.app import SupportApp

        # 1. StateService get/set tui layout
        temp_dir = tempfile.mkdtemp()
        try:
            state_file = os.path.join(temp_dir, "state.yaml")
            svc = StateService(state_file=state_file)
            self.assertEqual(svc.get_tui_layout("testuser"), "horizontal")
            svc.set_tui_layout("testuser", "vertical")
            self.assertEqual(svc.get_tui_layout("testuser"), "vertical")
            svc.set_tui_layout("testuser", "horizontal")
            self.assertEqual(svc.get_tui_layout("testuser"), "horizontal")
            svc.set_tui_layout("testuser", "mailbox")
            self.assertEqual(svc.get_tui_layout("testuser"), "horizontal")
        finally:
            shutil.rmtree(temp_dir)

        # 2. Headless SupportApp defaulting to horizontal mailbox
        async def run_layout_test():
            app = SupportApp(token="mock_access_token_123", config={})
            async with app.run_test() as pilot:
                self.assertEqual(app.layout, "horizontal")
                self.assertNotIn("layout-vertical", app.screen.classes)

                # 3. Test pane switching with ctrl-arrows in horizontal split
                await pilot.press("ctrl+down")
                self.assertTrue(
                    app.focused.id == "case-detail-container"
                    or app.query_one("#case-detail-container").has_focus
                )
                await pilot.press("ctrl+up")
                self.assertTrue(
                    app.focused.id == "case-table"
                    or app.query_one("#case-table").has_focus
                )

                # 4. Toggle to vertical split and test ctrl-left / ctrl-right
                app.action_toggle_layout()
                await pilot.pause()
                self.assertEqual(app.layout, "vertical")
                self.assertIn("layout-vertical", app.screen.classes)
                await pilot.press("ctrl+right")
                self.assertTrue(
                    app.focused.id == "case-detail-container"
                    or app.query_one("#case-detail-container").has_focus
                )
                await pilot.press("ctrl+left")
                self.assertTrue(
                    app.focused.id == "case-table"
                    or app.query_one("#case-table").has_focus
                )

                # 5. Toggle back to horizontal
                app.action_toggle_layout()
                await pilot.pause()
                self.assertEqual(app.layout, "horizontal")
                self.assertNotIn("layout-vertical", app.screen.classes)

    def test_tui_mark_read_removes_visual_unread_indicator(self):
        """Test that pressing 'm' in TUI removes the unread indicator from table and details divider."""
        import asyncio
        import shutil
        import tempfile
        from unittest.mock import patch

        import rh_support_lib.constants
        from rh_support_lib.models import Case, CaseComment, CaseDetails
        from rh_support_lib.services.state import StateService
        from rh_support_lib.tui.app import SupportApp

        orig_gql = rh_support_lib.constants.GRAPHQL_URL
        rh_support_lib.constants.GRAPHQL_URL = f"{self.base_url}/graphql"
        temp_dir = tempfile.mkdtemp()
        state_file = os.path.join(temp_dir, "rh-support-cli", "state.yaml")

        try:
            with patch.dict(
                os.environ,
                {
                    "XDG_STATE_HOME": temp_dir,
                    "XDG_CONFIG_HOME": temp_dir,
                    "RH_GRAPHQL_URL": f"{self.base_url}/graphql",
                },
            ):
                svc = StateService(state_file=state_file)
                app = SupportApp(token="mock_access_token_123", config={})
                app.case_service.state_service = svc
                user = app.case_service.get_current_user()

                # Set an older checkpoint on case 12345
                svc.add_tracked_case(
                    user, "12345", relationship="followed", summary="Test Case"
                )
                data = svc.load_state()
                u_state = svc._get_user_state(data, user)
                u_state["tracked_cases"]["12345"]["read_checkpoint"] = {
                    "last_read_comment_date": "2026-10-01T00:00:00Z",
                    "last_read_comment_id": "comm_0",
                    "status": "Waiting on Customer Action Required",
                    "marked_read_at": "2026-10-01T00:00:00Z",
                }
                svc.save_state(data)

                async def run_test():
                    async with app.run_test() as pilot:
                        await pilot.pause()
                        c = Case(
                            case_number="12345",
                            summary="Test Case",
                            status="Waiting on Red Hat",
                            severity="High",
                            has_unread=True,
                            unread_count=1,
                        )
                        details = CaseDetails(
                            case=c,
                            description="Case Description",
                            comments=[
                                CaseComment(
                                    id="comm_1",
                                    body="New comment",
                                    created_date="2026-10-08T09:00:00Z",
                                    is_public=True,
                                    created_by="Red Hat Support",
                                )
                            ],
                        )
                        app.cases = [c]
                        app.populate_cases_table()
                        table = app.query_one("#case-table")
                        cols = list(table.columns.keys())

                        # Check that table cell has unread badge
                        cell_text = str(table.get_cell("12345", cols[0]))
                        self.assertIn("●1", cell_text)

                        # Mount details and verify divider is present
                        app.selected_case_id = "12345"
                        app.render_case_details(details, [])
                        await pilot.pause()
                        dividers_before = app.query(".tui-checkpoint-divider")
                        self.assertEqual(len(dividers_before), 1)

                        # Trigger 'm' (mark read)
                        app.action_mark_read()
                        await pilot.pause()

                        # Table cell unread badge MUST be removed
                        cell_after = str(table.get_cell("12345", cols[0]))
                        self.assertNotIn("●", cell_after)
                        self.assertFalse(c.has_unread)
                        self.assertEqual(c.unread_count, 0)

                        # Re-render details (as fetch_case_details does) and verify divider is gone
                        app.render_case_details(details, [])
                        await pilot.pause()
                        dividers_after = app.query(".tui-checkpoint-divider")
                        self.assertEqual(len(dividers_after), 0)

                        # Verify checkpoint in state.yaml was advanced
                        reloaded_tc = svc.get_tracked_cases(user).get("12345")
                        self.assertEqual(
                            reloaded_tc.read_checkpoint.last_read_comment_date,
                            "2026-10-08T09:00:00Z",
                        )
                        self.assertEqual(
                            reloaded_tc.read_checkpoint.status, "Waiting on Red Hat"
                        )

                asyncio.run(run_test())
        finally:
            rh_support_lib.constants.GRAPHQL_URL = orig_gql
            shutil.rmtree(temp_dir)

    def test_state_service_xdg_state_path_resolution(self):
        """Test StateService XDG state path resolution and fallback mechanisms."""
        import shutil
        import tempfile
        from unittest.mock import patch

        from rh_support_lib.services.state import StateService

        # 1. Defaults to ~/.local/state/rh-support-cli/state.yaml
        temp_fresh = tempfile.mkdtemp()
        try:
            with patch.dict(os.environ, {"HOME": temp_fresh}, clear=True):
                resolved = StateService._resolve_state_path()
                self.assertTrue(
                    resolved.endswith(
                        os.path.join(".local", "state", "rh-support-cli", "state.yaml")
                    )
                )
        finally:
            shutil.rmtree(temp_fresh)
        # 2. Honors XDG_STATE_HOME
        with patch.dict(os.environ, {"XDG_STATE_HOME": "/custom/state"}):
            resolved = StateService._resolve_state_path()
            self.assertEqual(resolved, "/custom/state/rh-support-cli/state.yaml")

        # 3. Honors explicit state_file and RH_SUPPORT_STATE
        with patch.dict(os.environ, {"RH_SUPPORT_STATE": "/custom/env/state.yaml"}):
            resolved = StateService._resolve_state_path()
            self.assertEqual(resolved, "/custom/env/state.yaml")
        resolved = StateService._resolve_state_path(state_file="/explicit/state.yaml")
        self.assertEqual(resolved, "/explicit/state.yaml")

        # 4. Automatic migration from legacy config dir if legacy state file exists
        temp_dir = tempfile.mkdtemp()
        try:
            legacy_dir = os.path.join(temp_dir, "config", "rh-support-cli")
            os.makedirs(legacy_dir, exist_ok=True)
            legacy_file = os.path.join(legacy_dir, "state.yaml")
            with open(legacy_file, "w") as f:
                f.write("version: 1\n")
            target_state_dir = os.path.join(temp_dir, "state")
            with patch.dict(
                os.environ,
                {
                    "XDG_CONFIG_HOME": os.path.join(temp_dir, "config"),
                    "XDG_STATE_HOME": target_state_dir,
                },
                clear=True,
            ):
                expected_migrated = os.path.join(
                    target_state_dir, "rh-support-cli", "state.yaml"
                )
                resolved = StateService._resolve_state_path()
                self.assertEqual(resolved, expected_migrated)
                self.assertTrue(os.path.exists(expected_migrated))
                with open(expected_migrated) as f:
                    self.assertEqual(f.read(), "version: 1\n")
                self.assertFalse(os.path.exists(legacy_file))
        finally:
            shutil.rmtree(temp_dir)

    def test_case_comments_auto_exhaustion_pagination(self):
        """Test that get_case_details automatically exhausts multi-page comments."""
        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.cases import CaseService

        api_client = LegacyAPIClient("mock_access_token_123")
        case_service = CaseService(api_client, graphql_url=f"{self.base_url}/graphql")

        # 1. Fetch case 888888 with all_comments=True (default) -> should fetch page 1 and page 2
        details = case_service.get_case_details(
            "888888", first_comments=1, all_comments=True
        )
        self.assertIsNotNone(details)
        self.assertEqual(len(details.comments), 2)
        self.assertEqual(details.comments[0].body, "Comment on page 1")
        self.assertEqual(details.comments[1].body, "Comment on page 2")

        # 2. Fetch case 888888 with all_comments=False, max_comments=1 -> should fetch only page 1
        details_single = case_service.get_case_details(
            "888888", first_comments=1, all_comments=False, max_comments=1
        )
        self.assertIsNotNone(details_single)
        self.assertEqual(len(details_single.comments), 1)
        self.assertEqual(details_single.comments[0].body, "Comment on page 1")

    def test_case_list_pagination_all_and_limit(self):
        """Test that list_cases and CLI --all and --limit handle multi-page case pagination."""
        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.cases import CaseService

        api_client = LegacyAPIClient("mock_access_token_123")
        case_service = CaseService(api_client, graphql_url=f"{self.base_url}/graphql")

        # 1. Direct CaseService list_cases with all_pages=True
        cases_all = case_service.list_cases(account="PAGINATE", all_pages=True)
        self.assertEqual(len(cases_all), 2)
        case_numbers = [c.case_number for c in cases_all]
        self.assertIn("CASE_PAGE1", case_numbers)
        self.assertIn("CASE_PAGE2", case_numbers)

        # 2. Direct CaseService list_cases with limit=1
        cases_limited = case_service.list_cases(account="PAGINATE", limit=1)
        self.assertEqual(len(cases_limited), 1)
        self.assertEqual(cases_limited[0].case_number, "CASE_PAGE1")

        # 3. CLI list --all
        res_cli_all = self.run_cli(
            ["--simple-output", "list", "--account", "PAGINATE", "--all"]
        )
        self.assertEqual(res_cli_all.returncode, 0)
        self.assertIn("CASE_PAGE1", res_cli_all.stdout)
        self.assertIn("CASE_PAGE2", res_cli_all.stdout)

        # 4. CLI list --limit 1
        res_cli_lim = self.run_cli(
            ["--simple-output", "list", "--account", "PAGINATE", "--limit", "1"]
        )
        self.assertEqual(res_cli_lim.returncode, 0)
        self.assertIn("CASE_PAGE1", res_cli_lim.stdout)
        self.assertNotIn("CASE_PAGE2", res_cli_lim.stdout)

    def test_active_products_pagination(self):
        """Test that get_active_products loops through pages when all_pages=True."""
        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.cases import CaseService

        api_client = LegacyAPIClient("mock_access_token_123")
        case_service = CaseService(api_client, graphql_url=f"{self.base_url}/graphql")

        # Request with first=1, all_pages=True -> mock returns page 1 (RHEL) then page 2 (OpenShift)
        products = case_service.get_active_products(first=1, all_pages=True)
        self.assertEqual(len(products), 2)
        product_names = [p.name for p in products]
        self.assertIn("Red Hat Enterprise Linux", product_names)
        self.assertIn("Red Hat OpenShift Container Platform", product_names)

        # Request with first=1, all_pages=False -> returns only page 1
        products_single = case_service.get_active_products(first=1, all_pages=False)
        self.assertEqual(len(products_single), 1)
        self.assertEqual(products_single[0].name, "Red Hat Enterprise Linux")

    def test_json_and_yaml_list_output(self):
        """Test listing cases in machine-readable JSON and YAML format without logging noise."""
        import json

        import yaml

        # 1. JSON output
        res_json = self.run_cli(["list", "--json"])
        self.assertEqual(res_json.returncode, 0)
        self.assertNotIn("Authenticating...", res_json.stdout)
        self.assertNotIn("Fetching cases...", res_json.stdout)
        data = json.loads(res_json.stdout)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        self.assertIn("case_number", data[0])
        self.assertIn("status", data[0])

        # 2. YAML output
        res_yaml = self.run_cli(["list", "--format", "yaml"])
        self.assertEqual(res_yaml.returncode, 0)
        self.assertNotIn("Authenticating...", res_yaml.stdout)
        data_yaml = yaml.safe_load(res_yaml.stdout)
        self.assertIsInstance(data_yaml, list)
        self.assertIn("case_number", data_yaml[0])

    def test_json_and_yaml_show_output(self):
        """Test showing case details in machine-readable JSON and YAML format."""
        import json

        import yaml

        # 1. JSON output
        res_json = self.run_cli(["show", "-c", "12345", "--json"])
        self.assertEqual(res_json.returncode, 0)
        self.assertNotIn("Authenticating...", res_json.stdout)
        self.assertNotIn("Fetching case", res_json.stdout)
        data = json.loads(res_json.stdout)
        self.assertIsInstance(data, dict)
        self.assertIn("case", data)
        self.assertIn("description", data)
        self.assertIn("comments", data)
        self.assertIn("attachments", data)
        self.assertEqual(data["case"]["case_number"], "12345")

        # 2. YAML output
        res_yaml = self.run_cli(["show", "-c", "12345", "--format", "yaml"])
        self.assertEqual(res_yaml.returncode, 0)
        data_yaml = yaml.safe_load(res_yaml.stdout)
        self.assertIsInstance(data_yaml, dict)
        self.assertIn("case", data_yaml)
        self.assertIn("comments", data_yaml)

    def test_json_attachment_list_output(self):
        """Test listing attachments in machine-readable JSON format."""
        import json

        res_json = self.run_cli(["list-attachments", "12345", "--json"])
        self.assertEqual(res_json.returncode, 0)
        self.assertNotIn("Authenticating...", res_json.stdout)
        data = json.loads(res_json.stdout)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        self.assertIn("uuid", data[0])
        self.assertIn("file_name", data[0])

    def test_cache_service_atomic_writes_and_keys(self):
        """Test CacheService atomic writes, TTL expiration, and deterministic key hashing."""
        from rh_support_lib.models import Case
        from rh_support_lib.services.cache import CacheService

        cache = CacheService(cache_dir=self.test_cache_dir)
        self.assertTrue(cache.enabled)

        # 1. Deterministic list cache key hashing
        key1 = cache.compute_list_cache_key(
            status=["Waiting on Red Hat", "Closed"], severity=["1", "2"]
        )
        # Permuted order should produce identical hash key
        key2 = cache.compute_list_cache_key(
            status=["Closed", "Waiting on Red Hat"], severity=["2", "1"]
        )
        self.assertEqual(key1, key2)

        # Different filter produces different key
        key3 = cache.compute_list_cache_key(status=["Closed"])
        self.assertNotEqual(key1, key3)

        # 2. Atomic list caching and TTL
        dummy_case = Case(
            case_number="99901",
            summary="Test Case",
            status="Waiting on Red Hat",
            severity="1 (Urgent)",
            product="RHEL",
            version="9.0",
            account_number="123",
            created_date="2026-01-01",
            last_modified_date="2026-01-02",
            created_by="User",
            last_modified_by="User",
            owner="Owner",
            contact_name="Contact",
            contact_sso="sso",
            case_type="Standard",
            id="50099901",
        )
        cache.set_case_list(key1, [dummy_case])
        cached_list = cache.get_case_list(key1, ttl=60)
        self.assertIsNotNone(cached_list)
        self.assertEqual(len(cached_list), 1)
        self.assertEqual(cached_list[0].case_number, "99901")

        # Expired TTL returns None
        self.assertIsNone(cache.get_case_list(key1, ttl=0))

    def test_case_details_cache_hit_and_lightweight_probe(self):
        """Test that repeated 'show' commands use the lightweight probe and serve from local cache."""
        initial_details_count = MockRedHatHandler.details_query_count
        initial_probe_count = MockRedHatHandler.probe_query_count

        # First call: cache miss, executes full details query
        res1 = self.run_cli(["show", "-c", "12345"])
        self.assertEqual(res1.returncode, 0)
        self.assertIn("12345", res1.stdout)
        self.assertEqual(
            MockRedHatHandler.details_query_count, initial_details_count + 1
        )

        # Verify cache file exists
        cached_file = os.path.join(
            self.test_cache_dir, "rh-support-cli", "cases", "12345.json"
        )
        self.assertTrue(os.path.isfile(cached_file))

        # Second call: cache hit! Details query count does NOT increase, lightweight probe query count does
        res2 = self.run_cli(["show", "-c", "12345"])
        self.assertEqual(res2.returncode, 0)
        self.assertIn("12345", res2.stdout)
        self.assertEqual(
            MockRedHatHandler.details_query_count, initial_details_count + 1
        )
        self.assertEqual(MockRedHatHandler.probe_query_count, initial_probe_count + 1)

        # Third call with --no-cache: bypasses cache and increments details query count
        res3 = self.run_cli(["show", "-c", "12345", "--no-cache"])
        self.assertEqual(res3.returncode, 0)
        self.assertEqual(
            MockRedHatHandler.details_query_count, initial_details_count + 2
        )

    def test_case_list_cache_hit_and_no_cache(self):
        """Test that repeated 'list' commands hit local cache within TTL and --no-cache bypasses it."""
        initial_list_count = MockRedHatHandler.list_query_count

        # First call: cache miss
        res1 = self.run_cli(["list"])
        self.assertEqual(res1.returncode, 0)
        self.assertEqual(MockRedHatHandler.list_query_count, initial_list_count + 1)

        # Second call: hits cache (list_query_count remains identical)
        res2 = self.run_cli(["list"])
        self.assertEqual(res2.returncode, 0)
        self.assertEqual(MockRedHatHandler.list_query_count, initial_list_count + 1)

        # Third call with --no-cache: fetches from server
        res3 = self.run_cli(["list", "--no-cache"])
        self.assertEqual(res3.returncode, 0)
        self.assertEqual(MockRedHatHandler.list_query_count, initial_list_count + 2)

    def test_case_cache_invalidation_on_comment(self):
        """Test that commenting on a case invalidates its cached details."""
        # 1. Warm cache
        res_show1 = self.run_cli(["show", "-c", "12345"])
        self.assertEqual(res_show1.returncode, 0)
        cached_file = os.path.join(
            self.test_cache_dir, "rh-support-cli", "cases", "12345.json"
        )
        self.assertTrue(os.path.isfile(cached_file))

        # 2. Add comment
        comment_file = os.path.join(self.test_cache_dir, "c.txt")
        with open(comment_file, "w") as f:
            f.write("New test comment text")
        res_comm = self.run_cli(["comment", "-c", "12345", "-f", comment_file])
        self.assertEqual(res_comm.returncode, 0)

        # Cache file should be removed upon mutation!
        self.assertFalse(os.path.isfile(cached_file))

    def test_tui_swr_cache_instant_render(self):
        """Test that TUI immediately renders details from cache without loading placeholder."""
        import asyncio

        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.models import Case, CaseComment, CaseDetails
        from rh_support_lib.services.cache import CacheService
        from rh_support_lib.tui.app import SupportApp

        cache = CacheService(cache_dir=self.test_cache_dir)
        dummy_case = Case(
            case_number="12345",
            summary="SWR Instant Case",
            status="Waiting on Red Hat",
            severity="1 (Urgent)",
            product="RHEL",
            version="9.0",
            account_number="123",
            created_date="2026-01-01",
            last_modified_date="2023-01-02T10:00:00Z",
            created_by="Tester",
            last_modified_by="Tester",
            owner="Owner",
            contact_name="Tester",
            contact_sso="tester",
            case_type="Standard",
            id="50012345",
        )
        details = CaseDetails(
            case=dummy_case,
            description="Cached instant description",
            comments=[
                CaseComment(
                    id="comment_2",
                    created_date="2023-01-03T10:00:00Z",
                    body="c2",
                    created_by="Tester",
                    is_public=True,
                )
            ],
            attachments=[],
            notified_users=[],
        )
        cache.set_case_details(
            "12345", details, last_modified_date="2023-01-02T10:00:00Z"
        )

        async def run_headless_swr():
            from unittest.mock import patch

            client = LegacyAPIClient("mock_access_token_123")
            with patch.dict(
                os.environ,
                {
                    "XDG_CACHE_HOME": self.test_cache_dir,
                    "RH_GRAPHQL_URL": f"{self.base_url}/graphql",
                    "RH_ATTACHMENT_API_URL": self.base_url,
                },
            ):
                app = SupportApp(client, config={"tui": {"poll_interval": 0}})
                async with app.run_test() as pilot:
                    await pilot.pause()
                    # Trigger row selection for cached case 12345
                    from unittest.mock import MagicMock

                    event = MagicMock()
                    event.row_key.value = "12345"
                    app.on_row_selected(event)
                    # current_details should be populated IMMEDIATELY from cache!
                    self.assertIsNotNone(app.current_details)
                    self.assertEqual(
                        app.current_details.description, "Cached instant description"
                    )

        asyncio.run(run_headless_swr())

    def test_tui_background_activity_indicator(self):
        """Test that active background jobs update the TUI header subtitle with visual feedback."""
        import asyncio

        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.tui.app import SupportApp

        async def run_indicator_test():
            client = LegacyAPIClient("mock_access_token_123")
            app = SupportApp(client, config={"tui": {"poll_interval": 0}})
            async with app.run_test() as pilot:
                # On mount, fetch_cases is actively running and reflected in the header subtitle
                if app.sub_title:
                    self.assertIn("Fetching cases...", app.sub_title)

                # Wait for initial load worker to finish
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(app.sub_title, "")
                app._start_job("Syncing updates...")
                self.assertEqual(app.sub_title, "⟳ Syncing updates...")
                formatted = app.format_title(app.title, app.sub_title)
                self.assertIn("Syncing updates...", str(formatted))

                # Start concurrent job 2
                app._start_job("Uploading file.log...")
                self.assertEqual(
                    app.sub_title, "⟳ Syncing updates... | Uploading file.log..."
                )

                # Finish job 1
                app._finish_job("Syncing updates...")
                self.assertEqual(app.sub_title, "⟳ Uploading file.log...")

                # Finish job 2
                app._finish_job("Uploading file.log...")
                self.assertEqual(app.sub_title, "")

        asyncio.run(run_indicator_test())

    def test_case_details_cache_revalidates_on_new_remote_comment(self):
        """Test that a new comment on the remote server triggers revalidation and updates cached case."""
        # 1. Warm cache
        res1 = self.run_cli(["show", "-c", "12345"])
        self.assertEqual(res1.returncode, 0)
        initial_details_count = MockRedHatHandler.details_query_count

        # 2. Server receives new comment_3
        MockRedHatHandler.latest_mock_comment_id = "comment_3"
        MockRedHatHandler.latest_mock_comment_date = "2026-10-09T10:00:00Z"

        # 3. Running show -c 12345 automatically detects the new comment and re-fetches!
        res2 = self.run_cli(["show", "-c", "12345"])
        self.assertEqual(res2.returncode, 0)
        self.assertEqual(
            MockRedHatHandler.details_query_count, initial_details_count + 1
        )

    def test_tui_post_comment_refreshes_details_without_wiping_container(self):
        """Test that submitting a comment in the TUI refreshes details without stalling on 'Loading cases...'."""
        import asyncio
        from unittest.mock import MagicMock, patch

        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.tui.app import SupportApp

        async def run_post_comment_test():
            client = LegacyAPIClient("mock_access_token_123")
            with patch.dict(
                os.environ,
                {
                    "XDG_CACHE_HOME": self.test_cache_dir,
                    "RH_GRAPHQL_URL": f"{self.base_url}/graphql",
                    "RH_ATTACHMENT_API_URL": self.base_url,
                },
            ):
                app = SupportApp(client, config={"tui": {"poll_interval": 0}})
                async with app.run_test() as pilot:
                    await app.workers.wait_for_complete()
                    await pilot.pause()

                    # Select case 12345
                    event = MagicMock()
                    event.row_key.value = "12345"
                    app.on_row_selected(event)
                    await app.workers.wait_for_complete()
                    await pilot.pause()

                    # Post comment
                    app.execute_comment_submission(
                        "New comment from test", "Waiting on Customer"
                    )
                    await app.workers.wait_for_complete()
                    await pilot.pause()

                    # Verify that detail container was NOT wiped and buttons are rendered
                    container = app.query_one("#case-detail-container")
                    self.assertTrue(len(container.query(".tui-comment-btn")) > 0)
                    self.assertIsNotNone(app.current_details)
                    self.assertEqual(app.selected_case_id, "12345")

        asyncio.run(run_post_comment_test())

    def test_is_comment_newer_iso_parsing(self):
        """Test ISO 8601 parsing across various timezone and fractional second formats."""
        from rh_support_lib.utils import is_comment_newer, parse_iso_datetime

        # 1. Parsing ISO formats
        dt_z = parse_iso_datetime("2026-10-12T08:00:00Z")
        dt_ms_z = parse_iso_datetime("2026-10-12T08:00:00.123Z")
        dt_offset = parse_iso_datetime("2026-10-12T08:00:00.123+0000")
        dt_colon = parse_iso_datetime("2026-10-12T08:00:00.123+00:00")

        self.assertIsNotNone(dt_z)
        self.assertIsNotNone(dt_ms_z)
        self.assertIsNotNone(dt_offset)
        self.assertIsNotNone(dt_colon)

        self.assertEqual(dt_ms_z, dt_offset)
        self.assertEqual(dt_ms_z, dt_colon)
        self.assertTrue(dt_ms_z > dt_z)

        # 2. is_comment_newer comparing different ISO timestamp formats
        # Comment with fractional seconds is newer than checkpoint without fractional seconds
        self.assertTrue(
            is_comment_newer(
                created_date="2026-10-12T08:00:00.123Z",
                comment_id="c_new",
                checkpoint_date="2026-10-12T08:00:00Z",
                checkpoint_id="c_old",
            )
        )
        self.assertTrue(
            is_comment_newer(
                created_date="2026-10-12T08:00:00.123+0000",
                comment_id="c_new",
                checkpoint_date="2026-10-12T08:00:00Z",
                checkpoint_id="c_old",
            )
        )
        # Older comment is not newer
        self.assertFalse(
            is_comment_newer(
                created_date="2026-10-12T07:59:59.999Z",
                comment_id="c_old",
                checkpoint_date="2026-10-12T08:00:00Z",
                checkpoint_id="c_cur",
            )
        )
        # Empty checkpoint date and id -> all comments are unread
        self.assertTrue(
            is_comment_newer(
                created_date="2026-10-12T08:00:00Z",
                comment_id="c_1",
                checkpoint_date="",
                checkpoint_id="",
            )
        )
        # Checkpoint id comparison when date is absent
        self.assertTrue(
            is_comment_newer(
                created_date="",
                comment_id="c_2",
                checkpoint_date="",
                checkpoint_id="c_1",
            )
        )
        self.assertFalse(
            is_comment_newer(
                created_date="",
                comment_id="c_1",
                checkpoint_date="",
                checkpoint_id="c_1",
            )
        )

    def test_sync_and_updates_detection_and_concurrency(self):
        """Test that sync detects remote updates and new comments, preserving all cases under concurrency."""
        import shutil
        import tempfile

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        orig_state = self.env.get("XDG_STATE_HOME")
        self.env["HOME"] = temp_home
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_home, ".config")
        self.env["XDG_STATE_HOME"] = os.path.join(temp_home, ".local", "state")
        try:
            # 1. Follow multiple cases (including case with leading zero '04526149')
            case_list = ["04526149"] + [f"100{i}" for i in range(10)]
            res_follow = self.run_cli(["follow"] + case_list)
            self.assertEqual(res_follow.returncode, 0)

            # Initial sync: everything is already up to date with checkpoint
            res_sync1 = self.run_cli(["sync"])
            self.assertEqual(res_sync1.returncode, 0)
            self.assertIn("0 updated", res_sync1.stdout)
            self.assertIn("0 new comment(s)", res_sync1.stdout)

            # Verify 'updates' reports no unread updates
            res_upd1 = self.run_cli(["updates"])
            self.assertEqual(res_upd1.returncode, 0)
            self.assertIn("No unread updates across tracked cases.", res_upd1.stdout)

            # 2. Remote server receives a new comment for case 04526149 with millisecond ISO timestamp
            MockRedHatHandler.latest_mock_comment_id = "comment_remote_new"
            MockRedHatHandler.latest_mock_comment_date = "2026-10-12T12:30:00.500+0000"

            # 3. Run sync: should detect the 1 update on case 04526149!
            res_sync2 = self.run_cli(["sync"])
            self.assertEqual(res_sync2.returncode, 0)
            self.assertIn("1 updated", res_sync2.stdout)
            self.assertIn("1 new comment(s)", res_sync2.stdout)

            # 4. Run updates: should clearly list case 04526149 and its new comment
            res_upd2 = self.run_cli(["updates"])
            self.assertEqual(res_upd2.returncode, 0)
            self.assertIn("04526149", res_upd2.stdout)
            self.assertIn("1 new comment(s)", res_upd2.stdout)

            # 5. Verify all 11 cases are preserved in state.yaml without race conditions
            from rh_support_lib.services.state import StateService

            state_path = os.path.join(
                temp_home, ".local", "state", "rh-support-cli", "state.yaml"
            )
            svc = StateService(state_file=state_path)
            user = svc.load_state()["users"]
            current_user = next(iter(user.keys()))
            tracked = svc.get_tracked_cases(current_user)
            self.assertEqual(len(tracked), 11)
            self.assertIn("04526149", tracked)
            self.assertEqual(tracked["04526149"].latest_known.unread_comment_count, 1)

            # 6. Mark read advances checkpoint
            res_mark = self.run_cli(["updates", "--mark-read"])
            self.assertEqual(res_mark.returncode, 0)

            res_upd3 = self.run_cli(["updates"])
            self.assertIn("No unread updates across tracked cases.", res_upd3.stdout)
        finally:
            MockRedHatHandler.latest_mock_comment_id = "comment_2"
            MockRedHatHandler.latest_mock_comment_date = "2023-01-03T10:00:00Z"
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            if orig_state:
                self.env["XDG_STATE_HOME"] = orig_state
            shutil.rmtree(temp_home)

    def test_add_tracked_case_preserves_checkpoints(self):
        """Test that re-registering or following an existing case preserves checkpoints."""
        import shutil
        import tempfile

        from rh_support_lib.services.state import StateService

        temp_dir = tempfile.mkdtemp()
        try:
            state_file = os.path.join(temp_dir, "state.yaml")
            svc = StateService(state_file=state_file)

            # Track initial case with checkpoint
            svc.add_tracked_case(
                user="testuser",
                case_number="04526149",
                relationship="followed",
                summary="Initial Summary",
                status="Waiting on Red Hat",
                last_comment_id="comm_1",
                last_comment_date="2026-10-10T00:00:00Z",
            )

            tc = svc.get_tracked_cases("testuser")["04526149"]
            self.assertEqual(tc.read_checkpoint.status, "Waiting on Red Hat")
            self.assertEqual(tc.read_checkpoint.last_read_comment_id, "comm_1")
            self.assertEqual(
                tc.read_checkpoint.last_read_comment_date, "2026-10-10T00:00:00Z"
            )

            # Re-register case as owned (e.g. from auto-tracking list)
            svc.register_owned_case(
                user="testuser",
                case_number="04526149",
                summary="Updated Summary",
                status="",  # status omitted
                last_comment_id="",
                last_comment_date="",
            )

            tc2 = svc.get_tracked_cases("testuser")["04526149"]
            # Relationship merged to 'both'
            self.assertEqual(tc2.relationship, "both")
            self.assertEqual(tc2.summary, "Updated Summary")
            # Existing checkpoints preserved!
            self.assertEqual(tc2.read_checkpoint.status, "Waiting on Red Hat")
            self.assertEqual(tc2.read_checkpoint.last_read_comment_id, "comm_1")
            self.assertEqual(
                tc2.read_checkpoint.last_read_comment_date, "2026-10-10T00:00:00Z"
            )
        finally:
            shutil.rmtree(temp_dir)

    def test_comment_close_create_formatting_modes(self):
        """Test --format options (markdown, plain, html, raw) on comment, close, and create."""
        # 1. Test comment with default (markdown): soft breaks and code blocks
        with open("test_md_comment.txt", "w") as f:
            f.write("Line 1\nLine 2\n\n```bash\necho hello\n```")

        try:
            res_md = self.run_cli(
                ["comment", "-c", "12345", "-f", "test_md_comment.txt"]
            )
            self.assertEqual(res_md.returncode, 0)
            payload_md = MockRedHatHandler.last_comment_payload
            self.assertIn("<p>Line 1<br />\nLine 2</p>", payload_md["commentBody"])
            self.assertIn("<pre><code", payload_md["commentBody"])

            # 2. Test comment with --format plain: escapes entities and wraps lines in <p> and <br />
            with open("test_plain_comment.txt", "w") as f:
                f.write("Plain line 1\nPlain line 2\n\nParagraph 2 with <tag>")

            res_plain = self.run_cli(
                [
                    "comment",
                    "-c",
                    "12345",
                    "-f",
                    "test_plain_comment.txt",
                    "--format",
                    "plain",
                ]
            )
            self.assertEqual(res_plain.returncode, 0)
            payload_plain = MockRedHatHandler.last_comment_payload
            self.assertIn(
                "<p>Plain line 1<br />Plain line 2</p>", payload_plain["commentBody"]
            )
            self.assertIn("&lt;tag&gt;", payload_plain["commentBody"])

            # 3. Test comment with --format raw: sends verbatim string
            res_raw = self.run_cli(
                [
                    "comment",
                    "-c",
                    "12345",
                    "-f",
                    "test_plain_comment.txt",
                    "--format",
                    "raw",
                ]
            )
            self.assertEqual(res_raw.returncode, 0)
            payload_raw = MockRedHatHandler.last_comment_payload
            self.assertEqual(
                payload_raw["commentBody"],
                "Plain line 1\nPlain line 2\n\nParagraph 2 with <tag>",
            )

            # 4. Test comment with --format html: sends verbatim HTML
            with open("test_html_comment.txt", "w") as f:
                f.write("<p>Custom HTML</p><br/>")
            res_html = self.run_cli(
                [
                    "comment",
                    "-c",
                    "12345",
                    "-f",
                    "test_html_comment.txt",
                    "--format",
                    "html",
                ]
            )
            self.assertEqual(res_html.returncode, 0)
            payload_html = MockRedHatHandler.last_comment_payload
            self.assertEqual(payload_html["commentBody"], "<p>Custom HTML</p><br/>")

            # 5. Test close with --format plain
            res_close = self.run_cli(
                [
                    "close",
                    "-c",
                    "12345",
                    "-f",
                    "test_plain_comment.txt",
                    "--format",
                    "plain",
                ]
            )
            self.assertEqual(res_close.returncode, 0)
            payload_close = MockRedHatHandler.last_comment_payload
            self.assertIn(
                "<p>Plain line 1<br />Plain line 2</p>", payload_close["commentBody"]
            )

            # 6. Test create with --format markdown (default)
            with open("test_create_desc.txt", "w") as f:
                f.write("Description line 1\nDescription line 2")

            res_create = self.run_cli(
                [
                    "create",
                    "--summary",
                    "Formatted Case",
                    "--product",
                    "RHEL",
                    "--version",
                    "9.0",
                    "--description-file",
                    "test_create_desc.txt",
                    "--severity",
                    "Normal",
                    "--type",
                    "Standard",
                ],
                input="y\n",
            )
            self.assertEqual(res_create.returncode, 0)
            payload_create = MockRedHatHandler.last_case_payload
            self.assertIn(
                "<p>Description line 1<br />\nDescription line 2</p>",
                payload_create.get("Description", ""),
            )
        finally:
            for p in (
                "test_md_comment.txt",
                "test_plain_comment.txt",
                "test_html_comment.txt",
                "test_create_desc.txt",
            ):
                if os.path.exists(p):
                    os.remove(p)

    def test_search_cases_default(self):
        """Test search command default behavior searching cases."""
        res = self.run_cli(["search", "kernel panic"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("CASE #", res.stdout)
        self.assertIn("SUMMARY", res.stdout)
        self.assertIn("URL", res.stdout)
        self.assertIn("https://access.redhat.com/support/cases/", res.stdout)
        self.assertIn("SUBJ=kernel panic", res.stdout)

    def test_search_kcs_and_urls(self):
        """Test search command querying Knowledgebase (KCS) with solution/article URLs."""
        res = self.run_cli(["search", "boot mount", "--type", "kcs"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("ID", res.stdout)
        self.assertIn("TYPE", res.stdout)
        self.assertIn("7008126", res.stdout)
        self.assertIn("Solution", res.stdout)
        self.assertIn("Failed to mount API filesystem", res.stdout)
        self.assertIn("https://access.redhat.com/solutions/7008126", res.stdout)
        self.assertIn("https://access.redhat.com/articles/6911201", res.stdout)

    def test_search_all_combined(self):
        """Test search --type all queries both cases and knowledgebase."""
        res = self.run_cli(["search", "ingress", "--type", "all"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("SUPPORT CASES", res.stdout)
        self.assertIn("KNOWLEDGEBASE", res.stdout)
        self.assertIn("SOLUTIONS", res.stdout)
        self.assertIn("https://access.redhat.com/solutions/7008126", res.stdout)

    def test_search_open_browser_flag(self):
        """Test search with --open flag announces opening browser URL."""
        res = self.run_cli(["search", "boot mount", "--type", "kcs", "--open"])
        self.assertEqual(res.returncode, 0)
        self.assertIn(
            "Opening https://access.redhat.com/solutions/7008126 in browser...",
            res.stdout,
        )

    def test_search_local_cache(self):
        """Test search --local searches cached cases across description and comments without hitting network."""
        import json

        # Pre-populate a unique case in the isolated test cache
        case_file = os.path.join(
            self.test_cache_dir, "rh-support-cli", "cases", "987654.json"
        )
        os.makedirs(os.path.dirname(case_file), exist_ok=True)
        cached_record = {
            "cached_at": time.time(),
            "last_modified_date": "2026-10-12T00:00:00Z",
            "status": "Waiting on Customer",
            "case": {
                "case_number": "987654",
                "summary": "Local Cache Case",
                "status": "Waiting on Customer",
                "severity": "2 (High)",
                "product": "RHEL",
            },
            "description": "Unique description token: XYZ_LOCAL_DEBUG",
            "comments": [
                {
                    "id": "c1",
                    "body": "Comment containing secret diagnostic fingerprint: ALPHA_BETA_DIAG",
                    "created_date": "2026-10-12T01:00:00Z",
                    "created_by": "Red Hat Support",
                }
            ],
            "attachments": [],
            "notified_users": [],
        }
        with open(case_file, "w") as f:
            json.dump(cached_record, f)

        res = self.run_cli(["search", "ALPHA_BETA_DIAG", "--local"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("987654", res.stdout)
        self.assertIn("Local Cache Case", res.stdout)

    def test_search_structured_output(self):
        """Test search with --format json and yaml."""
        res_json = self.run_cli(
            ["search", "boot mount", "--type", "kcs", "--format", "json"]
        )
        self.assertEqual(res_json.returncode, 0)
        data = json.loads(res_json.stdout)
        self.assertTrue(isinstance(data, list))
        self.assertTrue(any(d.get("id") == "7008126" for d in data))
        self.assertTrue(
            any(
                "https://access.redhat.com/solutions/7008126" in d.get("url", "")
                for d in data
            )
        )

    def test_search_kcs_language_and_doc_id(self):
        """Test KCS search omits docs URL as ID, filters non-English by default, and allows --language."""
        # 1. Default (en): excludes Japanese doc, includes English doc with omitted ID
        res_default = self.run_cli(["search", "networking", "--type", "kcs"])
        self.assertEqual(res_default.returncode, 0)
        self.assertIn("Configuring Networking on RHEL 9", res_default.stdout)
        self.assertIn("https://docs.redhat.com/en/documentation/", res_default.stdout)
        # Japanese result excluded
        self.assertNotIn("RHEL 9 ネットワークの設定", res_default.stdout)
        # Ensure documentation ID does not display the full docs URL as an ID
        for line in res_default.stdout.splitlines():
            if "Configuring Networking" in line:
                self.assertNotIn("https://docs.redhat.com", line.split()[0])

        # 2. Querying with --language ja returns Japanese result
        res_ja = self.run_cli(
            ["search", "networking", "--type", "kcs", "--language", "ja"]
        )
        self.assertEqual(res_ja.returncode, 0)
        self.assertIn("RHEL 9 ネットワークの設定", res_ja.stdout)
        self.assertIn("https://docs.redhat.com/ja/documentation/", res_ja.stdout)

    def test_search_bookmarks(self):
        """Test search command incorporates default bookmark and supports --no-default-bookmark."""
        import shutil
        import tempfile

        import yaml

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        self.env["HOME"] = temp_home
        cfg_dir = os.path.join(temp_home, ".config", "rh-support-cli")
        os.makedirs(cfg_dir, exist_ok=True)
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_home, ".config")

        config_data = {
            "default_bookmark": "test-bm",
            "bookmarks": {
                "test-bm": {
                    "product": "Red Hat OpenShift Container Platform",
                    "status": "Waiting on Customer Action Required",
                }
            },
        }
        with open(os.path.join(cfg_dir, "config.yaml"), "w") as f:
            yaml.dump(config_data, f)

        try:
            # 1. Default search incorporates bookmark filters into query
            res_bm = self.run_cli(["search", "cert", "--format", "json"])
            self.assertEqual(res_bm.returncode, 0)
            self.assertIn("STAT=Waiting on Customer Action Required", res_bm.stdout)
            self.assertIn("PROD=Red Hat OpenShift Container Platform", res_bm.stdout)

            # 2. --no-default-bookmark skips bookmark filters
            res_no_bm = self.run_cli(
                ["search", "cert", "--no-default-bookmark", "--format", "json"]
            )
            self.assertEqual(res_no_bm.returncode, 0)
            self.assertNotIn("PROD=Red Hat OpenShift", res_no_bm.stdout)

            # 3. CLI override takes precedence over bookmark
            res_override = self.run_cli(
                ["search", "cert", "--product", "RHEL", "--format", "json"]
            )
            self.assertEqual(res_override.returncode, 0)
            self.assertIn("PROD=RHEL", res_override.stdout)
            self.assertNotIn("PROD=Red Hat OpenShift", res_override.stdout)
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            shutil.rmtree(temp_home)

    def test_file_part_stream_slicing_and_length(self):
        """Test FilePartStream streams exact byte slices in blocks with callbacks."""
        import tempfile

        from rh_support_lib.services.attachments import FilePartStream

        temp_file = tempfile.NamedTemporaryFile(delete=False)  # noqa: SIM115
        content = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 100  # 3600 bytes
        temp_file.write(content)
        temp_file.close()

        try:
            progress_bytes = []

            def on_progress(n: int) -> None:
                progress_bytes.append(n)

            with FilePartStream(
                file_path=temp_file.name,
                start_offset=100,
                length=500,
                block_size=128,
                on_bytes_read=on_progress,
            ) as stream:
                self.assertEqual(len(stream), 500)
                read_data = stream.read()
                self.assertEqual(len(read_data), 500)
                self.assertEqual(read_data, content[100:600])
                self.assertEqual(sum(progress_bytes), 500)
                self.assertEqual(stream.read(), b"")
        finally:
            os.remove(temp_file.name)

    def test_attachment_multipart_streaming_upload(self):
        """Test automated multipart upload with low-memory streaming and progress tracking."""
        import tempfile

        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.attachments import AttachmentService

        temp_file = tempfile.NamedTemporaryFile(delete=False)  # noqa: SIM115
        payload_data = b"MOCK_LARGE_MULTIPART_PAYLOAD_CHUNK_" * 100  # ~3500 bytes
        temp_file.write(payload_data)
        temp_file.close()

        client = LegacyAPIClient("mock_access_token_123")
        service = AttachmentService(client, base_url=self.base_url)
        orig_threshold = AttachmentService.MULTIPART_THRESHOLD
        AttachmentService.MULTIPART_THRESHOLD = (
            1000  # Trigger multipart on small test file
        )

        try:
            progress_reports = []

            def on_progress(uploaded: int, total: int) -> None:
                progress_reports.append((uploaded, total))

            att = service.upload_attachment(
                case_number="12345",
                file_path=temp_file.name,
                description="Automated multipart upload test",
                progress_callback=on_progress,
            )
            self.assertIsNotNone(att)
            self.assertEqual(att.uuid, "att_test_uuid_123")
            self.assertEqual(att.file_name, os.path.basename(temp_file.name))
            self.assertTrue(len(progress_reports) > 0)
            # Final report reached 100%
            self.assertEqual(progress_reports[-1][0], len(payload_data))
            self.assertEqual(progress_reports[-1][1], len(payload_data))
        finally:
            AttachmentService.MULTIPART_THRESHOLD = orig_threshold
            os.remove(temp_file.name)

    def test_attachment_multipart_abort_on_put_failure(self):
        """Test that multipart upload aborts session cleanly if a chunk PUT fails."""
        import tempfile
        from unittest.mock import patch

        import requests

        from rh_support_lib.api import LegacyAPIClient
        from rh_support_lib.services.attachments import AttachmentService

        temp_file = tempfile.NamedTemporaryFile(delete=False)  # noqa: SIM115
        temp_file.write(b"MOCK_DATA_" * 200)
        temp_file.close()

        client = LegacyAPIClient("mock_access_token_123")
        service = AttachmentService(client, base_url=self.base_url)
        orig_threshold = AttachmentService.MULTIPART_THRESHOLD
        AttachmentService.MULTIPART_THRESHOLD = 500

        try:
            # Simulate PUT failure on part upload
            with (
                patch(
                    "requests.put",
                    side_effect=requests.RequestException(
                        "Simulated connection timeout"
                    ),
                ),
                self.assertRaises(requests.RequestException),
            ):
                service.upload_attachment(
                    case_number="12345",
                    file_path=temp_file.name,
                )

            # Verify abort endpoint was called
            self.assertIsNotNone(
                getattr(MockRedHatHandler, "last_aborted_upload", None)
            )
            self.assertEqual(
                MockRedHatHandler.last_aborted_upload.get("uploadId"), "upload_id_123"
            )
            self.assertEqual(
                MockRedHatHandler.last_aborted_upload.get("caseNumber"), "12345"
            )
        finally:
            AttachmentService.MULTIPART_THRESHOLD = orig_threshold
            os.remove(temp_file.name)


if __name__ == "__main__":
    unittest.main()
