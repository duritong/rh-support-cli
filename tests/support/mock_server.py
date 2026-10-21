"""Shared Mock Red Hat API HTTP Server and base test fixture."""

from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from typing import ClassVar
from unittest import mock

# Configuration for the mock server
MOCK_HOST = "localhost"
MOCK_PORT = 0  # 0 means let the OS choose a free port


class MockRedHatHandler(http.server.BaseHTTPRequestHandler):
    lookup_product_version = "9.0"
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
    mock_case_statuses: ClassVar[dict[str, str]] = {}
    mock_account_cases: ClassVar[dict[str, list[dict]]] = {}

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
                                                "Name": {
                                                    "value": variables.get(
                                                        "productName"
                                                    )
                                                },
                                                "VersionName__c": {
                                                    "value": MockRedHatHandler.lookup_product_version
                                                },
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
                                                                "value": MockRedHatHandler.mock_case_statuses.get(
                                                                    case_num,
                                                                    "Waiting on Red Hat",
                                                                )
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
                    "Status": {
                        "value": MockRedHatHandler.mock_case_statuses.get(
                            case_num, "Waiting on Red Hat"
                        )
                    },
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
                account_eq = (
                    where_clause.get("RedHatSupportAccount", {})
                    .get("AccountNumber", {})
                    .get("eq")
                )
                if account_eq and account_eq in MockRedHatHandler.mock_account_cases:
                    raw_cases = MockRedHatHandler.mock_account_cases[account_eq]
                    stat_filter = where_clause.get("Status")
                    allowed_stats = None
                    if isinstance(stat_filter, dict):
                        if "in" in stat_filter:
                            allowed_stats = set(stat_filter["in"])
                        elif "eq" in stat_filter:
                            allowed_stats = {stat_filter["eq"]}
                    edges = []
                    for c_dict in raw_cases:
                        c_stat = c_dict.get("status", "Waiting on Red Hat")
                        if allowed_stats and c_stat not in allowed_stats:
                            continue
                        c_num_val = str(c_dict.get("case_number", "1001"))
                        edges.append(
                            {
                                "node": {
                                    "Id": f"500{c_num_val}",
                                    "CaseNumber__c": {"value": c_num_val},
                                    "Subject": {
                                        "value": c_dict.get(
                                            "summary", f"Case {c_num_val}"
                                        )
                                    },
                                    "Status": {"value": c_stat},
                                    "Priority": {
                                        "value": c_dict.get("severity", "2 (High)")
                                    },
                                    "CreatedDate": {"value": "2026-07-10T14:30:00Z"},
                                    "LastModifiedDate": {
                                        "value": "2026-07-11T09:15:00Z"
                                    },
                                    "RedHatSupportAccount": {
                                        "AccountNumber": {"value": str(account_eq)}
                                    },
                                },
                                "cursor": f"cursor_{c_num_val}",
                            }
                        )
                    page_info = {"hasNextPage": False, "endCursor": None}
                elif "ACC=PAGINATE" in summary_str:
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
            if getattr(MockRedHatHandler, "fail_attachments", False):
                self.send_response(401)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(
                    b'{"error": "Required scope api.graphql not found in JWT token", "status": "401"}'
                )
                return
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


class MockServerTestCase(unittest.TestCase):
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
        ready_event = threading.Event()

        def run_server():
            ready_event.set()
            cls.server.serve_forever()

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        ready_event.wait(timeout=5)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.shutdown()
            cls.server.server_close()

    def setUp(self):
        # Set up environment variables for the CLI to use the mock server
        MockRedHatHandler.lookup_product_version = "9.0"
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
        self.env["REDHAT_SUPPORT_USERNAME"] = "jdeveloper"
        self.env["FORCE_COLOR"] = "1"
        self.env["RH_KCS_SEARCH_URL"] = f"{self.base_url}/search/kcs"

        # Ensure subprocess finds requests even if HOME is changed
        self.env["PYTHONPATH"] = os.pathsep.join(sys.path)

        # Avoid truncation in list output
        self.env["COLUMNS"] = "200"

        # Isolate cache, config, and state for every test

        self.test_cache_dir = tempfile.mkdtemp()
        self.env["XDG_CACHE_HOME"] = self.test_cache_dir
        self.test_config_dir = tempfile.mkdtemp()
        self.env["XDG_CONFIG_HOME"] = self.test_config_dir
        self.test_state_dir = tempfile.mkdtemp()
        self.env["XDG_STATE_HOME"] = self.test_state_dir

        self.cli_path = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
            "rh-support-cli.py",
        )

    def tearDown(self):

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

    def case_cache(self):
        from rh_support_lib.api import RedHatAPIClient
        from rh_support_lib.services.cases import CaseService

        with (
            mock.patch.dict(os.environ, self.env),
            mock.patch("rh_support_lib.api.SSO_URL", self.env["RH_SSO_URL"]),
        ):
            return CaseService(RedHatAPIClient()).cache_service

    def run_completion(
        self, comp_line: str, extra_env: dict | None = None
    ) -> list[str]:
        """Helper to run CLI under argcomplete and return completions list."""

        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as tf:
            out_path = tf.name

        env = self.env.copy()
        if extra_env:
            env.update(extra_env)
        env["_ARGCOMPLETE"] = "1"
        env["_ARGCOMPLETE_IFS"] = "\n"
        env["_ARGCOMPLETE_STDOUT_FILENAME"] = out_path
        env["COMP_LINE"] = comp_line
        env["COMP_POINT"] = str(len(comp_line))

        try:
            subprocess.run(
                [sys.executable, self.cli_path],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            with open(out_path, "r") as f:
                content = f.read()
            return [line.strip() for line in content.splitlines() if line.strip()]
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)
