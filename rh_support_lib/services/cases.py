from __future__ import annotations

import contextlib
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from rh_support_lib.api import extract_jwt_username
from rh_support_lib.constants import (
    API_URL,
    BACKEND_MODE,
    GRAPHQL_URL,
    SEVERITY_MAP,
    STATUS_MAP,
)
from rh_support_lib.models import (
    Attachment,
    Case,
    CaseComment,
    CaseDetails,
    ProductItem,
)
from rh_support_lib.services.state import StateService
from rh_support_lib.utils import clean_comment_body

# --- GRAPHQL QUERIES & MUTATIONS ---

GQL_GET_CASES_BY_FILTERS = """
query GetCasesByFilters($where: RedHatSupportCase_Filter, $first: Int, $after: String) {
  redhat_support_uiapi {
    query {
      RedHatSupportCase(
        where: $where,
        first: $first,
        after: $after,
        orderBy: { LastModifiedDate: { order: DESC } }
      ) {
        edges {
          node {
            Id
            CaseNumber__c { value }
            Subject { value }
            Status { value }
            Priority { value }
            CreatedDate { value }
            LastModifiedDate { value }
            Product {
              Name { value }
              VersionName__c { value }
            }
            RedHatSupportAccount {
              AccountNumber { value }
            }
            RedHatSupportContact {
              Name { value }
              SSOUserName__c { value }
            }
            Owner {
              ... on RedHatSupportGroup {
                Name { value }
              }
              ... on RedHatSupportUser {
                Name { value }
              }
            }
          }
          cursor
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}
"""

GQL_GET_CASE_AND_COMMENTS = """
query GetCaseAndComments($caseNumber: String, $first: Int, $after: String) {
  redhat_support_uiapi {
    query {
      RedHatSupportCase(where: { CaseNumber__c: { eq: $caseNumber } }) {
        edges {
          node {
            Id
            CaseNumber__c { value }
            Subject { value }
            Status { value }
            Priority { value }
            Type { value }
            Description { value }
            Product {
              Id
              Name { value }
              VersionName__c { value }
              ParentProduct__r {
                Id
                Name { value }
              }
            }
            RedHatSupportAccount {
              Id
              Name { value }
              AccountNumber { value }
            }
            RedHatSupportContact {
              Id
              Name { value }
              Email { value }
              Phone { value }
              SSOUserName__c { value }
            }
            Owner {
              ... on RedHatSupportGroup {
                Id
                Name { value }
              }
              ... on RedHatSupportUser {
                Id
                Name { value }
              }
            }
            CreatedDate { value }
            LastModifiedDate { value }
            LastModifiedBy {
              Name { value }
            }
            CaseComments__r(first: $first, after: $after, orderBy: { CreatedDate: { order: ASC } }) {
              edges {
                node {
                  Id
                  Body__c { value }
                  LastModifiedByName__c { value }
                  IsAssociate__c { value }
                  IsCustomer__c { value }
                  CreatedDate { value }
                }
                cursor
              }
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
      }
    }
  }
}
"""

GQL_GET_ACTIVE_PRODUCTS = """
query GetActiveProducts($first: Int, $after: String) {
  redhat_support_uiapi {
    query {
      RedHatSupportProduct2(
        where: { IsActive: { eq: true } }
        orderBy: { Name: { order: ASC } }
        first: $first
        after: $after
      ) {
        totalCount
        edges {
          node {
            Id
            Name { value }
            VersionName__c { value }
            Family { value }
            ParentProduct__c { value }
          }
          cursor
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}
"""

GQL_RESOLVE_CASE_LOOKUP_DATA = """
query ResolveCaseLookupData($ssoUser: String, $accountNumber: String, $productName: String, $versionName: String) {
  redhat_support_uiapi {
    Contact: query {
      RedHatSupportContact(where: { SSOUserName__c: { eq: $ssoUser } }, first: 1) {
        edges {
          node {
            Id
            AccountId { value }
          }
        }
      }
    }
    Account: query {
      RedHatSupportAccount(where: { AccountNumber: { eq: $accountNumber } }, first: 1) {
        edges {
          node {
            Id
          }
        }
      }
    }
    RecordType: query {
      RedHatSupportRecordType(
        where: { SobjectType: { eq: "Case" }, DeveloperName: { eq: "Technical_Support" } }
        first: 1
      ) {
        edges {
          node {
            Id
          }
        }
      }
    }
    Product: query {
      RedHatSupportProduct2(
        where: {
          and: [
            { Name: { eq: $productName } }
          ]
        }
        first: 1
      ) {
        edges {
          node {
            Id
            Name { value }
            VersionName__c { value }
          }
        }
      }
    }
  }
}
"""

GQL_CREATE_NEW_CASE = """
mutation CreateNewCase($input: RedHatSupportCaseCreateInput!) {
  redhat_support_uiapi {
    CaseCreate(input: $input) {
      RedHatSupportRecord {
        Id
        CaseNumber__c { value }
        Subject { value }
        Status { value }
        CreatedDate { value }
      }
    }
  }
}
"""

GQL_UPDATE_CASE = """
mutation UpdateCase($input: RedHatSupportCaseUpdateInput!) {
  redhat_support_uiapi {
    CaseUpdate(input: $input) {
      RedHatSupportRecord {
        Id
        CaseNumber__c { value }
        Status { value }
        Priority { value }
        LastModifiedDate { value }
      }
    }
  }
}
"""

GQL_CREATE_CASE_COMMENT = """
mutation CreateCaseComment($input: RedHatSupportCaseComment__cCreateInput!) {
  redhat_support_uiapi {
    CaseComment__cCreate(input: $input) {
      RedHatSupportRecord {
        Id
        Body__c { value }
        Case__c { value }
        CreatedDate { value }
      }
    }
  }
}
"""


class CaseService:
    """Service to handle all support case operations with dual v1 REST and GraphQL backend engines."""

    def __init__(
        self,
        api_client,
        backend: str | None = None,
        rest_url: str | None = None,
        graphql_url: str | None = None,
        state_service: StateService | None = None,
    ):
        self.api_client = api_client
        self.backend = (backend or os.environ.get("RH_BACKEND") or BACKEND_MODE).lower()
        self.rest_url = (rest_url or API_URL).rstrip("/")
        self.graphql_url = graphql_url or GRAPHQL_URL
        self._use_graphql = self.backend == "graphql"
        self.state_service = state_service or StateService()

    # --- NORMALIZATION HELPERS ---

    def _normalize_case_node(self, node: dict[str, Any]) -> Case:
        """Converts a GraphQL RedHatSupportCase node to a Case domain model."""
        case_id = node.get("Id", "")
        case_num = (
            node.get("CaseNumber__c", {}).get("value")
            if isinstance(node.get("CaseNumber__c"), dict)
            else node.get("caseNumber", case_id)
        )
        summary = (
            node.get("Subject", {}).get("value")
            if isinstance(node.get("Subject"), dict)
            else node.get("summary", "")
        )
        status = (
            node.get("Status", {}).get("value")
            if isinstance(node.get("Status"), dict)
            else node.get("status", "")
        )
        severity = (
            node.get("Priority", {}).get("value")
            if isinstance(node.get("Priority"), dict)
            else node.get("severity", "")
        )
        created_date = (
            node.get("CreatedDate", {}).get("value")
            if isinstance(node.get("CreatedDate"), dict)
            else node.get("createdDate", "")
        )
        last_modified_date = (
            node.get("LastModifiedDate", {}).get("value")
            if isinstance(node.get("LastModifiedDate"), dict)
            else node.get("lastModifiedDate", "")
        )

        product_obj = node.get("Product") or {}
        product_name = (
            product_obj.get("Name", {}).get("value", "")
            if isinstance(product_obj.get("Name"), dict)
            else str(product_obj.get("name", ""))
        )
        version_name = (
            product_obj.get("VersionName__c", {}).get("value", "")
            if isinstance(product_obj.get("VersionName__c"), dict)
            else str(product_obj.get("version", ""))
        )

        account_obj = node.get("RedHatSupportAccount") or {}
        account_number = (
            account_obj.get("AccountNumber", {}).get("value", "")
            if isinstance(account_obj.get("AccountNumber"), dict)
            else str(account_obj.get("accountNumber", ""))
        )

        contact_obj = node.get("RedHatSupportContact") or node.get("contact") or {}
        contact_name = (
            contact_obj.get("Name", {}).get("value", "")
            if isinstance(contact_obj.get("Name"), dict)
            else contact_obj.get("name", "")
        )
        contact_sso = (
            contact_obj.get("SSOUserName__c", {}).get("value", "")
            if isinstance(contact_obj.get("SSOUserName__c"), dict)
            else contact_obj.get("ssoUsername", "")
        )

        owner_obj = node.get("Owner") or node.get("owner") or {}
        owner_name = (
            owner_obj.get("Name", {}).get("value", "")
            if isinstance(owner_obj.get("Name"), dict)
            else owner_obj.get("name", "")
        )

        case_type = (
            node.get("Type", {}).get("value", "Standard")
            if isinstance(node.get("Type"), dict)
            else node.get("caseType", "Standard")
        )
        last_modified_by = (
            node.get("LastModifiedBy", {}).get("Name", {}).get("value", "")
            if isinstance(node.get("LastModifiedBy"), dict)
            else ""
        )

        return Case(
            case_number=str(case_num),
            summary=summary or "",
            status=status or "",
            severity=severity or "",
            product=product_name,
            version=version_name,
            account_number=account_number,
            created_date=created_date,
            last_modified_date=last_modified_date,
            created_by=contact_name,
            last_modified_by=last_modified_by,
            owner=owner_name,
            contact_name=contact_name,
            contact_sso=contact_sso,
            case_type=case_type,
            id=str(case_id),
        )

    def _normalize_case_v1(self, c: dict[str, Any]) -> Case:
        """Converts a REST v1 case dictionary to a Case domain model."""
        num = str(c.get("caseNumber") or c.get("id") or "")
        title = c.get("summary") or c.get("caseSummary") or ""
        status = c.get("status") or ""
        severity = c.get("severity") or ""
        product = c.get("product") or ""
        version = c.get("version") or ""
        account = str(c.get("accountNumberRef") or c.get("accountNumber") or "")
        created_date = c.get("createdDate") or ""
        last_mod_date = c.get("lastModifiedDate") or ""
        created_by = (
            c.get("createdById")
            or c.get("contactName")
            or c.get("createdBySSOName")
            or ""
        )
        last_mod_by = c.get("lastModifiedById") or c.get("modifiedBy") or ""

        owner_obj = c.get("owner") or c.get("contact") or {}
        owner = ""
        if isinstance(owner_obj, dict):
            owner = (
                owner_obj.get("name")
                or owner_obj.get("fullName")
                or owner_obj.get("ssoUsername")
                or ""
            )
        elif isinstance(owner_obj, str):
            owner = owner_obj
        if not owner:
            owner = c.get("ownerId") or "Unknown"

        case_type = c.get("caseType") or c.get("type") or "Standard"
        return Case(
            case_number=num,
            summary=title,
            status=status,
            severity=severity,
            product=product,
            version=version,
            account_number=account,
            created_date=created_date,
            last_modified_date=last_mod_date,
            created_by=str(created_by),
            last_modified_by=str(last_mod_by),
            owner=str(owner),
            contact_name=c.get("contactName", ""),
            contact_sso=c.get("contactSSOName", ""),
            case_type=case_type,
            id=str(c.get("id", "")),
        )

    # --- PUBLIC SERVICE METHODS ---

    def list_cases(
        self,
        account: str | None = None,
        status: list[str] | str | None = None,
        severity: list[str] | str | None = None,
        owner: str | None = None,
        first: int = 100,
        after: str | None = None,
    ) -> list[Case]:
        """Lists support cases applying dynamic filters using selected backend."""
        if self._use_graphql:
            return self._list_cases_graphql(
                account=account,
                status=status,
                severity=severity,
                owner=owner,
                first=first,
                after=after,
            )
        return self._list_cases_rest_v1(
            account=account,
            status=status,
            severity=severity,
            owner=owner,
            first=first,
        )

    def _list_cases_graphql(
        self,
        account: str | None = None,
        status: list[str] | str | None = None,
        severity: list[str] | str | None = None,
        owner: str | None = None,
        first: int = 100,
        after: str | None = None,
    ) -> list[Case]:
        where: dict[str, Any] = {}

        if account:
            where["RedHatSupportAccount"] = {"AccountNumber": {"eq": str(account)}}

        if status:
            if isinstance(status, list):
                if len(status) == 1:
                    where["Status"] = {"eq": status[0]}
                else:
                    where["Status"] = {"in": status}
            else:
                where["Status"] = {"eq": status}

        if severity:
            if isinstance(severity, list):
                mapped_sevs = [SEVERITY_MAP.get(s.lower(), s) for s in severity]
                if len(mapped_sevs) == 1:
                    where["Priority"] = {"eq": mapped_sevs[0]}
                else:
                    where["Priority"] = {"in": mapped_sevs}
            else:
                mapped_sev = SEVERITY_MAP.get(severity.lower(), severity)
                where["Priority"] = {"eq": mapped_sev}

        if owner:
            where["Owner"] = {"Name": {"eq": owner}}

        variables = {
            "where": where if where else None,
            "first": first,
            "after": after,
        }

        data = self.api_client.execute_graphql(
            GQL_GET_CASES_BY_FILTERS, variables, url=self.graphql_url
        )
        uiapi = data.get("redhat_support_uiapi") or {}
        query_data = uiapi.get("query") or {}
        case_container = query_data.get("RedHatSupportCase") or {}
        edges = case_container.get("edges") or []

        cases = []
        for edge in edges:
            node = edge.get("node")
            if node:
                cases.append(self._normalize_case_node(node))
        return cases

    def _list_cases_rest_v1(
        self,
        account: str | None = None,
        status: list[str] | str | None = None,
        severity: list[str] | str | None = None,
        owner: str | None = None,
        first: int = 100,
    ) -> list[Case]:
        payload: dict[str, Any] = {"maxResults": first, "offset": 0}
        if account:
            payload["accountNumber"] = str(account)
        if status:
            if isinstance(status, list):
                if len(status) == 1:
                    payload["status"] = status[0]
                else:
                    payload["statuses"] = status
            else:
                payload["status"] = status
        if severity:
            if isinstance(severity, list):
                mapped_sevs = [SEVERITY_MAP.get(s.lower(), s) for s in severity]
                if len(mapped_sevs) == 1:
                    payload["severity"] = mapped_sevs[0]
                else:
                    payload["severities"] = mapped_sevs
            else:
                payload["severity"] = SEVERITY_MAP.get(severity.lower(), severity)
        if owner:
            payload["ownerSSOName"] = str(owner)

        resp = self.api_client.post(f"{self.rest_url}/cases/filter", json=payload)
        resp.raise_for_status()
        data = resp.json()
        raw_cases = (
            data
            if isinstance(data, list)
            else (data.get("cases") or data.get("items") or data.get("list") or [])
        )
        return [self._normalize_case_v1(c) for c in raw_cases]

    def get_case_details(
        self, case_number: str, first_comments: int = 200
    ) -> CaseDetails | None:
        """Fetches full case details and its comments using the selected backend."""
        if self._use_graphql:
            return self._get_case_details_graphql(case_number, first_comments)
        return self._get_case_details_rest_v1(case_number)

    def _get_case_details_graphql(
        self, case_number: str, first_comments: int = 200
    ) -> CaseDetails | None:
        variables = {
            "caseNumber": str(case_number),
            "first": first_comments,
            "after": None,
        }

        data = self.api_client.execute_graphql(
            GQL_GET_CASE_AND_COMMENTS, variables, url=self.graphql_url
        )
        uiapi = data.get("redhat_support_uiapi") or {}
        query_data = uiapi.get("query") or {}
        case_container = query_data.get("RedHatSupportCase") or {}
        edges = case_container.get("edges") or []

        if not edges:
            return None

        node = edges[0].get("node") or {}
        case_obj = self._normalize_case_node(node)
        description = (
            node.get("Description", {}).get("value", "")
            if isinstance(node.get("Description"), dict)
            else node.get("description", "")
        )

        comments = []
        comments_container = node.get("CaseComments__r") or {}
        comment_edges = comments_container.get("edges") or []

        for c_edge in comment_edges:
            c_node = c_edge.get("node") or {}
            c_id = c_node.get("Id", "")
            c_raw_body = (
                c_node.get("Body__c", {}).get("value", "")
                if isinstance(c_node.get("Body__c"), dict)
                else c_node.get("body", "")
            )
            c_body = clean_comment_body(c_raw_body)
            c_author = (
                c_node.get("LastModifiedByName__c", {}).get("value", "")
                if isinstance(c_node.get("LastModifiedByName__c"), dict)
                else c_node.get("createdBy", "User")
            )
            c_is_assoc = (
                c_node.get("IsAssociate__c", {}).get("value", False)
                if isinstance(c_node.get("IsAssociate__c"), dict)
                else False
            )
            c_is_cust = (
                c_node.get("IsCustomer__c", {}).get("value", False)
                if isinstance(c_node.get("IsCustomer__c"), dict)
                else False
            )
            c_date = (
                c_node.get("CreatedDate", {}).get("value", "")
                if isinstance(c_node.get("CreatedDate"), dict)
                else c_node.get("createdDate", "")
            )
            c_vis = (
                c_node.get("Visibility__c", {}).get("value")
                if isinstance(c_node.get("Visibility__c"), dict)
                else c_node.get("visibility", "")
            )
            is_pub = c_node.get("isPublic")
            if is_pub is None:
                is_pub = not (c_vis and str(c_vis).lower() not in ("public", ""))

            # Detect private comments by content markers (e.g. legacy migrated internal notes)
            upper_body = c_body.lstrip().upper()
            if upper_body.startswith(("/* INTERNAL", "[INTERNAL", "INTERNAL NOTE")):
                is_pub = False
                if not c_vis or str(c_vis).lower() == "public":
                    c_vis = "Internal"

            comments.append(
                CaseComment(
                    id=str(c_id),
                    body=c_body or "",
                    created_by=c_author or "User",
                    is_associate=bool(c_is_assoc),
                    is_customer=bool(c_is_cust),
                    is_public=bool(is_pub),
                    visibility=str(c_vis)
                    if c_vis
                    else ("Public" if is_pub is not False else "Private"),
                    created_date=c_date or "",
                )
            )

        return CaseDetails(
            case=case_obj,
            description=description or "",
            comments=comments,
            attachments=[],
            notified_users=[],
        )

    def _get_case_details_rest_v1(self, case_number: str) -> CaseDetails | None:
        resp = self.api_client.get(f"{self.rest_url}/cases/{case_number}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        case_dict = resp.json()
        if not case_dict or not isinstance(case_dict, dict):
            return None

        case_obj = self._normalize_case_v1(case_dict)
        description = case_dict.get("description") or ""

        # Fetch comments
        comments = []
        raw_comments = case_dict.get("comments")
        if not raw_comments:
            try:
                c_resp = self.api_client.get(
                    f"{self.rest_url}/cases/{case_number}/comments"
                )
                if c_resp.status_code == 200:
                    raw_comments = c_resp.json()
            except Exception:  # noqa: BLE001
                raw_comments = []

        if isinstance(raw_comments, dict):
            raw_comments = (
                raw_comments.get("comments") or raw_comments.get("items") or []
            )

        if isinstance(raw_comments, list):
            for c in raw_comments:
                c_id = c.get("id") or "c1"
                c_raw_body = (
                    c.get("commentBody") or c.get("body") or c.get("text") or ""
                )
                c_body = clean_comment_body(c_raw_body)
                c_author = c.get("createdBy") or c.get("createdById") or "Unknown"
                c_date = c.get("createdDate") or ""
                c_is_pub = c.get("isPublic")
                c_vis = c.get("visibility") or (
                    "Public" if c_is_pub is not False else "Private"
                )
                upper_body = c_body.lstrip().upper()
                if upper_body.startswith(("/* INTERNAL", "[INTERNAL", "INTERNAL NOTE")):
                    c_is_pub = False
                    if not c_vis or str(c_vis).lower() == "public":
                        c_vis = "Internal"

                comments.append(
                    CaseComment(
                        id=str(c_id),
                        body=c_body or "",
                        created_by=str(c_author),
                        is_public=bool(c_is_pub) if c_is_pub is not None else True,
                        visibility=str(c_vis),
                        created_date=c_date or "",
                    )
                )

        # Attachments
        attachments = []
        raw_atts = case_dict.get("attachments") or []
        for a in raw_atts:
            uuid_val = a.get("uuid") or a.get("id") or ""
            size_val = a.get("size") or 0
            attachments.append(
                Attachment(
                    uuid=str(uuid_val),
                    file_name=a.get("fileName") or a.get("name") or "attachment.bin",
                    size=int(size_val),
                    size_kb=round(int(size_val) / 1024.0, 1),
                    created_date=a.get("createdDate", ""),
                    created_by=a.get("createdBy", ""),
                    description=a.get("description", ""),
                    download_link=a.get("link", ""),
                    upload_status="Completed",
                    case_number=str(case_number),
                )
            )

        # Notified Users / Watchers
        notified_users = []
        raw_users = (
            case_dict.get("notifiedUsers")
            or case_dict.get("notifiedusers")
            or case_dict.get("watchers")
            or case_dict.get("notified_users")
            or []
        )
        if isinstance(raw_users, dict):
            raw_users = raw_users.get("user") or raw_users.get("users") or []
        for u in raw_users:
            if isinstance(u, str):
                notified_users.append(u)
            elif isinstance(u, dict):
                sso = (
                    u.get("ssoUsername")
                    or u.get("userSSOName")
                    or u.get("sso")
                    or u.get("name")
                    or ""
                )
                if sso:
                    notified_users.append(sso)

        return CaseDetails(
            case=case_obj,
            description=description or "",
            comments=comments,
            attachments=attachments,
            notified_users=notified_users,
        )

    def get_case_internal_id(self, case_number: str) -> str:
        """Resolves Salesforce internal record Id from a customer case number."""
        details = self.get_case_details(case_number, first_comments=1)
        if details and details.case.id:
            return details.case.id
        return f"500{case_number}"

    def add_comment(
        self,
        case_number: str,
        body: str,
        is_public: bool = True,
        status: str | None = None,
        case_internal_id: str | None = None,
    ) -> CaseComment:
        """Adds a comment to an existing support case and optionally updates case status."""
        if self._use_graphql:
            return self._add_comment_graphql(
                case_number=case_number,
                body=body,
                is_public=is_public,
                status=status,
                case_internal_id=case_internal_id,
            )
        return self._add_comment_rest_v1(
            case_number=case_number,
            body=body,
            is_public=is_public,
            status=status,
        )

    def _add_comment_graphql(
        self,
        case_number: str,
        body: str,
        is_public: bool = True,
        status: str | None = None,
        case_internal_id: str | None = None,
    ) -> CaseComment:
        record_id = case_internal_id or self.get_case_internal_id(case_number)

        comment_input = {
            "Case__c": str(record_id),
            "Body__c": body,
            "Visibility__c": "Public" if is_public else "Private",
        }

        variables = {"input": {"RedHatSupportCaseComment__c": comment_input}}
        data = self.api_client.execute_graphql(
            GQL_CREATE_CASE_COMMENT, variables, url=self.graphql_url
        )

        record = (
            data.get("redhat_support_uiapi", {})
            .get("CaseComment__cCreate", {})
            .get("RedHatSupportRecord", {})
        )
        comment_id = record.get("Id", "")
        created_date = (
            record.get("CreatedDate", {}).get("value", "")
            if isinstance(record.get("CreatedDate"), dict)
            else ""
        )

        if status:
            mapped_status = STATUS_MAP.get(status.lower(), status)
            self.update_case(
                case_number, status=mapped_status, case_internal_id=record_id
            )

        return CaseComment(
            id=str(comment_id),
            body=body,
            created_date=created_date,
            created_by="Customer",
            is_public=is_public,
            is_customer=True,
        )

    def _add_comment_rest_v1(
        self,
        case_number: str,
        body: str,
        is_public: bool = True,
        status: str | None = None,
    ) -> CaseComment:
        payload = {"isPublic": is_public, "commentBody": body}
        resp = self.api_client.post(
            f"{self.rest_url}/cases/{case_number}/comments", json=payload
        )
        resp.raise_for_status()
        data = resp.json() if resp.text else {}

        if status:
            mapped_status = STATUS_MAP.get(status.lower(), status)
            self._update_case_rest_v1(case_number, status=mapped_status)

        return CaseComment(
            id=str(data.get("id", "c_new")),
            body=body,
            created_date="",
            created_by="Customer",
            is_public=is_public,
            visibility="Public" if is_public else "Private",
        )

    def update_case(
        self,
        case_number: str,
        status: str | None = None,
        severity: str | None = None,
        summary: str | None = None,
        description: str | None = None,
        case_internal_id: str | None = None,
    ) -> dict:
        """Modifies specific fields of an existing support case."""
        if self._use_graphql:
            return self._update_case_graphql(
                case_number=case_number,
                status=status,
                severity=severity,
                summary=summary,
                description=description,
                case_internal_id=case_internal_id,
            )
        return self._update_case_rest_v1(
            case_number=case_number,
            status=status,
            severity=severity,
            summary=summary,
            description=description,
        )

    def _update_case_graphql(
        self,
        case_number: str,
        status: str | None = None,
        severity: str | None = None,
        summary: str | None = None,
        description: str | None = None,
        case_internal_id: str | None = None,
    ) -> dict:
        record_id = case_internal_id or self.get_case_internal_id(case_number)
        patch_case: dict[str, Any] = {}

        if status:
            patch_case["Status"] = STATUS_MAP.get(status.lower(), status)
        if severity:
            patch_case["Priority"] = SEVERITY_MAP.get(severity.lower(), severity)
        if summary:
            patch_case["Subject"] = summary
        if description:
            patch_case["Description"] = description

        input_payload = {
            "Id": str(record_id),
            "RedHatSupportCase": patch_case,
        }

        variables = {"input": input_payload}
        data = self.api_client.execute_graphql(
            GQL_UPDATE_CASE, variables, url=self.graphql_url
        )
        return (
            data.get("redhat_support_uiapi", {})
            .get("CaseUpdate", {})
            .get("RedHatSupportRecord", {})
        )

    def _update_case_rest_v1(
        self,
        case_number: str,
        status: str | None = None,
        severity: str | None = None,
        summary: str | None = None,
        description: str | None = None,
    ) -> dict:
        updates: dict[str, Any] = {}
        if status:
            updates["status"] = STATUS_MAP.get(status.lower(), status)
        if severity:
            updates["severity"] = SEVERITY_MAP.get(severity.lower(), severity)
        if summary:
            updates["summary"] = summary
        if description:
            updates["description"] = description

        resp = self.api_client.put(f"{self.rest_url}/cases/{case_number}", json=updates)
        resp.raise_for_status()
        return updates

    def get_active_products(self, first: int = 200) -> list[ProductItem]:
        """Fetches active products from the product catalog."""
        if self._use_graphql:
            return self._get_active_products_graphql(first)
        return self._get_active_products_rest_v1()

    def _get_active_products_graphql(self, first: int = 200) -> list[ProductItem]:
        variables = {"first": first, "after": None}
        data = self.api_client.execute_graphql(
            GQL_GET_ACTIVE_PRODUCTS, variables, url=self.graphql_url
        )

        edges = (
            data.get("redhat_support_uiapi", {})
            .get("query", {})
            .get("RedHatSupportProduct2", {})
            .get("edges", [])
        )

        products = []
        for edge in edges:
            node = edge.get("node") or {}
            p_id = node.get("Id", "")
            p_name = (
                node.get("Name", {}).get("value", "")
                if isinstance(node.get("Name"), dict)
                else node.get("name", "")
            )
            p_version = (
                node.get("VersionName__c", {}).get("value", "")
                if isinstance(node.get("VersionName__c"), dict)
                else ""
            )
            p_family = (
                node.get("Family", {}).get("value", "")
                if isinstance(node.get("Family"), dict)
                else ""
            )
            p_parent = (
                node.get("ParentProduct__c", {}).get("value")
                if isinstance(node.get("ParentProduct__c"), dict)
                else None
            )

            products.append(
                ProductItem(
                    id=str(p_id),
                    name=p_name,
                    version_name=p_version or "",
                    family=p_family or "",
                    parent_id=p_parent,
                )
            )
        return products

    def _get_active_products_rest_v1(self) -> list[ProductItem]:
        prod_root = re.sub(r"/v\d+$", "", self.rest_url)
        prod_url = f"{prod_root}/v2"
        with contextlib.suppress(Exception):
            resp = self.api_client.get(f"{prod_url}/products")
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return [
                        ProductItem(
                            id=p.get("code") or p.get("name", "prod"),
                            name=p.get("name", ""),
                        )
                        for p in data
                    ]
        return [
            ProductItem(id="RHEL", name="Red Hat Enterprise Linux"),
            ProductItem(id="OPENSHIFT", name="Red Hat OpenShift Container Platform"),
        ]

    def resolve_case_lookups(
        self,
        sso_user: str | None = "jdeveloper",
        account_number: str | None = "123456",
        product_name: str | None = "Red Hat Enterprise Linux",
        version_name: str | None = "9.0",
    ) -> dict[str, str]:
        """Resolves required Salesforce Entity IDs for case creation."""
        variables = {
            "ssoUser": sso_user,
            "accountNumber": account_number,
            "productName": product_name,
            "versionName": version_name,
        }
        data = self.api_client.execute_graphql(
            GQL_RESOLVE_CASE_LOOKUP_DATA, variables, url=self.graphql_url
        )
        uiapi = data.get("redhat_support_uiapi") or {}

        contact_id = ""
        account_id = ""
        contact_edges = (
            uiapi.get("Contact", {}).get("RedHatSupportContact", {}).get("edges", [])
        )
        if contact_edges:
            c_node = contact_edges[0].get("node", {})
            contact_id = c_node.get("Id", "")
            account_id = (
                c_node.get("AccountId", {}).get("value", "")
                if isinstance(c_node.get("AccountId"), dict)
                else ""
            )

        if not account_id:
            account_edges = (
                uiapi.get("Account", {})
                .get("RedHatSupportAccount", {})
                .get("edges", [])
            )
            if account_edges:
                account_id = account_edges[0].get("node", {}).get("Id", "")

        record_type_id = ""
        rt_edges = (
            uiapi.get("RecordType", {})
            .get("RedHatSupportRecordType", {})
            .get("edges", [])
        )
        if rt_edges:
            record_type_id = rt_edges[0].get("node", {}).get("Id", "")

        product_id = ""
        prod_edges = (
            uiapi.get("Product", {}).get("RedHatSupportProduct2", {}).get("edges", [])
        )
        if prod_edges:
            product_id = prod_edges[0].get("node", {}).get("Id", "")

        return {
            "ContactId": contact_id or "003ContactMockId",
            "AccountId": account_id or "001AccountMockId",
            "RecordTypeId": record_type_id or "012RecordTypeMockId",
            "ProductId": product_id or "01tProductMockId",
        }

    def create_case(
        self,
        summary: str,
        description: str,
        product_name: str = "Red Hat Enterprise Linux",
        version_name: str = "9.0",
        severity: str = "3 (Normal)",
        case_type: str = "Standard",
        account_number: str | None = None,
        sso_user: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> str:
        """Creates a new Red Hat support case using the selected backend."""
        if self._use_graphql:
            return self._create_case_graphql(
                summary=summary,
                description=description,
                product_name=product_name,
                version_name=version_name,
                severity=severity,
                case_type=case_type,
                account_number=account_number,
                sso_user=sso_user,
                extra_fields=extra_fields,
            )
        return self._create_case_rest_v1(
            summary=summary,
            description=description,
            product_name=product_name,
            version_name=version_name,
            severity=severity,
            case_type=case_type,
            account_number=account_number,
            extra_fields=extra_fields,
        )

    def _create_case_graphql(
        self,
        summary: str,
        description: str,
        product_name: str = "Red Hat Enterprise Linux",
        version_name: str = "9.0",
        severity: str = "3 (Normal)",
        case_type: str = "Standard",
        account_number: str | None = None,
        sso_user: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> str:
        mapped_severity = SEVERITY_MAP.get(severity.lower(), severity)
        lookups = self.resolve_case_lookups(
            sso_user=sso_user,
            account_number=account_number,
            product_name=product_name,
            version_name=version_name,
        )

        case_input: dict[str, Any] = {
            "Subject": summary,
            "Description": description,
            "Priority": mapped_severity,
            "ProductId": lookups.get("ProductId"),
            "AccountId": lookups.get("AccountId"),
            "ContactId": lookups.get("ContactId"),
            "RecordTypeId": lookups.get("RecordTypeId"),
            "Status": "Waiting on Red Hat",
            "Origin": "Web",
            "Language": "en_US",
            "product": product_name,
            "version": version_name,
            "caseType": case_type,
        }
        if extra_fields:
            for k, v in extra_fields.items():
                if not k.startswith("_") and k not in case_input:
                    case_input[k] = v

        variables = {"input": {"RedHatSupportCase": case_input}}
        data = self.api_client.execute_graphql(
            GQL_CREATE_NEW_CASE, variables, url=self.graphql_url
        )
        record = (
            data.get("redhat_support_uiapi", {})
            .get("CaseCreate", {})
            .get("RedHatSupportRecord", {})
        )

        case_num = (
            record.get("CaseNumber__c", {}).get("value", "")
            if isinstance(record.get("CaseNumber__c"), dict)
            else record.get("caseNumber", "")
        )
        return str(case_num)

    def _create_case_rest_v1(
        self,
        summary: str,
        description: str,
        product_name: str = "Red Hat Enterprise Linux",
        version_name: str = "9.0",
        severity: str = "3 (Normal)",
        case_type: str = "Standard",
        account_number: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> str:
        mapped_severity = SEVERITY_MAP.get(severity.lower(), severity)
        payload: dict[str, Any] = {
            "product": product_name,
            "version": version_name,
            "summary": summary,
            "description": description,
            "severity": mapped_severity,
            "type": case_type,
            "caseType": case_type,
        }
        if account_number:
            payload["accountNumber"] = str(account_number)
        if extra_fields:
            for k, v in extra_fields.items():
                if not k.startswith("_") and k not in payload:
                    payload[k] = v

        resp = self.api_client.post(f"{self.rest_url}/cases", json=payload)
        resp.raise_for_status()
        case_data = resp.json() if resp.text else {}
        case_num = case_data.get("caseNumber") or case_data.get("id")

        if not case_num:
            loc = case_data.get("location") or case_data.get("locations")
            if isinstance(loc, list) and loc:
                case_num = str(loc[0]).rstrip("/").split("/")[-1]
            elif isinstance(loc, str) and loc:
                case_num = loc.rstrip("/").split("/")[-1]

        if not case_num and "Location" in resp.headers:
            case_num = resp.headers["Location"].rstrip("/").split("/")[-1]

        return str(case_num)

    def get_current_user(self) -> str:
        """Determines the current user's SSO username from config, env, JWT token, or system user."""
        for env_var in (
            "REDHAT_SUPPORT_USERNAME",
            "RH_SSO_USERNAME",
            "RH_USERNAME",
        ):
            val = os.environ.get(env_var)
            if val and val.strip():
                return val.strip()

        token = self.api_client.get_token()
        jwt_user = extract_jwt_username(token)
        if jwt_user:
            return jwt_user

        return os.environ.get("USER", "jdeveloper")

    def follow_case(
        self, case_numbers: list[str] | str, username: str | None = None
    ) -> list[str]:
        """Attaches the specified username (defaults to current user) as a watcher on one or more cases.
        Updates the local state and returns list of successfully followed case numbers."""
        user = username or self.get_current_user()
        if not isinstance(case_numbers, list):
            case_numbers = [case_numbers]

        followed = []
        for c_num in case_numbers:
            c_str = str(c_num).strip()
            if not c_str:
                continue
            payload = {"user": [{"ssoUsername": user}]}
            url = f"{self.rest_url}/cases/{c_str}/notifiedusers"
            resp = self.api_client.post(url, json=payload)
            if resp.status_code in (200, 201, 204):
                followed.append(c_str)
                self.state_service.add_followed_case(c_str, user, source="cli")
            else:
                resp.raise_for_status()
                followed.append(c_str)
                self.state_service.add_followed_case(c_str, user, source="cli")
        return followed

    def unfollow_case(
        self, case_numbers: list[str] | str, username: str | None = None
    ) -> list[str]:
        """Removes the specified username (defaults to current user) from watchers on one or more cases.
        Updates the local state and returns list of successfully unfollowed case numbers."""
        user = username or self.get_current_user()
        if not isinstance(case_numbers, list):
            case_numbers = [case_numbers]

        unfollowed = []
        for c_num in case_numbers:
            c_str = str(c_num).strip()
            if not c_str:
                continue
            url = f"{self.rest_url}/cases/{c_str}/notifiedusers/{user}"
            resp = self.api_client.delete(url)
            if resp.status_code in (200, 204, 404):
                unfollowed.append(c_str)
                self.state_service.remove_followed_case(c_str, user)
            else:
                resp.raise_for_status()
                unfollowed.append(c_str)
                self.state_service.remove_followed_case(c_str, user)
        return unfollowed

    @staticmethod
    def _match_user(target: str, user_list: list[str]) -> bool:
        """Matches username against a list of users, handling both exact matches and email prefixes."""
        t_clean = target.strip().lower()
        t_short = t_clean.split("@")[0]
        for u in user_list:
            u_clean = str(u).strip().lower()
            u_short = u_clean.split("@")[0]
            if t_clean == u_clean or t_short == u_short:
                return True
        return False

    def get_notified_users(self, case_number: str) -> list[str]:
        """Fetches the list of notified user SSO usernames for a given case."""
        with contextlib.suppress(Exception):
            resp = self.api_client.get(f"{self.rest_url}/cases/{case_number}")
            if resp.status_code == 200:
                data = resp.json() if resp.text else {}
                users = []
                raw_users = (
                    data.get("notifiedUsers")
                    or data.get("notifiedusers")
                    or data.get("watchers")
                    or data.get("notified_users")
                    or []
                )
                if isinstance(raw_users, dict):
                    raw_users = raw_users.get("user") or raw_users.get("users") or []
                for u in raw_users:
                    if isinstance(u, str):
                        users.append(u)
                    elif isinstance(u, dict):
                        sso = (
                            u.get("ssoUsername")
                            or u.get("userSSOName")
                            or u.get("sso")
                            or u.get("name")
                            or ""
                        )
                        if sso:
                            users.append(sso)
                return users
        return []

    def sync_followed_cases(
        self, username: str | None = None, account: str | None = None
    ) -> list[str]:
        """Scans active cases for the account in parallel to discover cases where the user is a watcher,
        reconciles them into local state, and returns the list of followed case numbers."""
        user = username or self.get_current_user()
        active_cases = self.list_cases(
            account=account, status=["Waiting on Red Hat", "Waiting on Customer"]
        )
        if not active_cases:
            return []

        def check_case(c: Case) -> str | None:
            with contextlib.suppress(Exception):
                users = self.get_notified_users(c.case_number)
                if self._match_user(user, users):
                    self.state_service.add_followed_case(
                        c.case_number,
                        user,
                        summary=c.summary,
                        account_number=c.account_number,
                        source="sync",
                    )
                    return c.case_number
            return None

        with ThreadPoolExecutor(
            max_workers=min(10, max(1, len(active_cases)))
        ) as executor:
            results = list(executor.map(check_case, active_cases))

        synced = [r for r in results if r]
        if account:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.state_service.update_sync_info(
                user, account, now_str, len(active_cases)
            )
        return synced

    def list_followed_cases(
        self,
        username: str | None = None,
        sync: bool = False,
        account: str | None = None,
        fetch_live: bool = True,
    ) -> list[Case]:
        """Returns the list of followed cases for the user. If sync is True, reconciles with the remote account first.
        Fetches live case details for all followed cases."""
        user = username or self.get_current_user()
        if sync:
            self.sync_followed_cases(username=user, account=account)

        followed_map = self.state_service.get_followed_cases(user)
        if not followed_map:
            return []

        case_numbers = list(followed_map.keys())

        if not fetch_live:
            return [
                Case(
                    case_number=c_num,
                    summary=f_info.summary or "Followed Case",
                    status="Followed",
                    severity="",
                    account_number=f_info.account_number,
                )
                for c_num, f_info in followed_map.items()
            ]

        # Fetch live case objects in parallel
        def fetch_one(c_num: str) -> Case | None:
            with contextlib.suppress(Exception):
                details = self.get_case_details(c_num)
                if details and details.case:
                    # Update cached summary
                    if details.case.summary:
                        self.state_service.add_followed_case(
                            c_num,
                            user,
                            summary=details.case.summary,
                            account_number=details.case.account_number,
                            source=followed_map[c_num].source,
                        )
                    return details.case
            # Fallback to local representation if individual fetch fails
            f_info = followed_map.get(c_num)
            return Case(
                case_number=c_num,
                summary=f_info.summary if f_info else "Followed Case",
                status="Unknown",
                severity="",
                account_number=f_info.account_number if f_info else "",
            )

        with ThreadPoolExecutor(max_workers=min(10, len(case_numbers))) as executor:
            cases = list(executor.map(fetch_one, case_numbers))

        return [c for c in cases if c is not None]
