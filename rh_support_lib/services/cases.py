from __future__ import annotations

import contextlib
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from rh_support_lib.api import extract_jwt_username
from rh_support_lib.constants import (
    GRAPHQL_URL,
    SEVERITY_MAP,
    STATUS_MAP,
)
from rh_support_lib.models import (
    Case,
    CaseComment,
    CaseDetails,
    ProductItem,
)
from rh_support_lib.services.cache import CacheService
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
                  CreatedBy {
                    Name { value }
                  }
                  LastModifiedByName__c { value }
                  IsAssociate__c { value }
                  IsCustomer__c { value }
                  Visibility__c {
                    value
                    displayValue
                  }
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

GQL_GET_CASE_COMMENTS = """
query GetCaseComments($filter: RedHatSupportCaseComment__c_Filter!, $orderBy: RedHatSupportCaseComment__c_OrderBy, $first: Int, $after: String) {
  redhat_support_uiapi {
    query {
      RedHatSupportCaseComment__c(
        where: $filter
        orderBy: $orderBy
        first: $first
        after: $after
      ) {
        edges {
          node {
            Id
            Body__c {
              value
            }
            CreatedBy {
              Name {
                value
              }
            }
            IsCustomer__c {
              value
            }
            IsAssociate__c {
              value
            }
            Visibility__c {
              value
              displayValue
            }
            LastModifiedByName__c {
              value
            }
            CreatedDate {
              value
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

GQL_CHECK_CASE_LAST_MODIFIED = """
query CheckCaseLastModified($caseNumber: String) {
  redhat_support_uiapi {
    query {
      RedHatSupportCase(where: { CaseNumber__c: { eq: $caseNumber } }) {
        edges {
          node {
            Id
            LastModifiedDate { value }
            Status { value }
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
    """Service to handle all support case operations with the GraphQL backend."""

    def __init__(
        self,
        api_client,
        rest_url: str | None = None,
        graphql_url: str | None = None,
        state_service: StateService | None = None,
        cache_service: CacheService | None = None,
        no_cache: bool = False,
    ):
        self.api_client = api_client
        self.graphql_url = (
            graphql_url or os.environ.get("RH_GRAPHQL_URL") or GRAPHQL_URL
        )
        self.state_service = state_service or StateService()
        self.cache_service = cache_service or CacheService(no_cache=no_cache)

    def check_case_last_modified(self, case_number: str) -> tuple[str, str, str] | None:
        """Lightweight probe query returning (internal_id, last_modified_date, status) or None."""
        variables = {"caseNumber": str(case_number).strip()}
        try:
            data = self.api_client.execute_graphql(
                GQL_CHECK_CASE_LAST_MODIFIED, variables, url=self.graphql_url
            )
            uiapi = data.get("redhat_support_uiapi") or {}
            query_data = uiapi.get("query") or {}
            case_container = query_data.get("RedHatSupportCase") or {}
            edges = case_container.get("edges") or []
            if not edges:
                return None
            node = edges[0].get("node") or {}
            c_id = node.get("Id", "")
            lmd = (
                node.get("LastModifiedDate", {}).get("value", "")
                if isinstance(node.get("LastModifiedDate"), dict)
                else ""
            )
            status = (
                node.get("Status", {}).get("value", "")
                if isinstance(node.get("Status"), dict)
                else ""
            )
            return c_id, lmd, status
        except Exception:  # noqa: BLE001
            return None

    # --- NORMALIZATION HELPERS ---

    def _normalize_comment_node(self, c_node: dict[str, Any]) -> CaseComment:
        """Converts a GraphQL RedHatSupportCaseComment__c node to a CaseComment domain model."""
        c_id = c_node.get("Id", "")
        c_raw_body = (
            c_node.get("Body__c", {}).get("value", "")
            if isinstance(c_node.get("Body__c"), dict)
            else c_node.get("body", "")
        )
        c_body = clean_comment_body(c_raw_body)

        created_by_obj = c_node.get("CreatedBy") or {}
        created_by_name = (
            created_by_obj.get("Name", {}).get("value")
            if isinstance(created_by_obj, dict)
            and isinstance(created_by_obj.get("Name"), dict)
            else ""
        )
        last_mod_name = (
            c_node.get("LastModifiedByName__c", {}).get("value")
            if isinstance(c_node.get("LastModifiedByName__c"), dict)
            else c_node.get("createdBy", "User")
        )
        c_author = created_by_name or last_mod_name or "User"

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

        vis_obj = c_node.get("Visibility__c")
        if isinstance(vis_obj, dict):
            c_vis = vis_obj.get("value") or vis_obj.get("displayValue") or ""
        else:
            c_vis = str(vis_obj) if vis_obj else ""

        is_pub = c_node.get("isPublic")
        if c_vis:
            is_pub = str(c_vis).lower() == "public"
        elif is_pub is not None:
            is_pub = bool(is_pub)
        else:
            is_pub = True

        return CaseComment(
            id=str(c_id),
            body=c_body or "",
            created_by=c_author,
            is_associate=bool(c_is_assoc),
            is_customer=bool(c_is_cust),
            is_public=bool(is_pub),
            visibility=str(c_vis) if c_vis else ("Public" if is_pub else "Private"),
            created_date=c_date or "",
        )

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

    # --- PUBLIC SERVICE METHODS ---

    def list_cases(
        self,
        account: str | None = None,
        status: list[str] | str | None = None,
        severity: list[str] | str | None = None,
        owner: str | None = None,
        first: int = 100,
        after: str | None = None,
        all_pages: bool = False,
        limit: int | None = None,
        use_cache: bool = True,
    ) -> list[Case]:
        cache_key = self.cache_service.compute_list_cache_key(
            account=account,
            status=status,
            severity=severity,
            owner=owner,
            limit=limit,
            all_pages=all_pages,
        )
        if use_cache and not after and self.cache_service.enabled:
            cached_list = self.cache_service.get_case_list(cache_key)
            if cached_list is not None:
                return cached_list

        cases = self._list_cases_graphql(
            account=account,
            status=status,
            severity=severity,
            owner=owner,
            first=first,
            after=after,
            all_pages=all_pages,
            limit=limit,
        )
        if use_cache and not after and self.cache_service.enabled and cases:
            self.cache_service.set_case_list(cache_key, cases)
        return cases

    def _list_cases_graphql(
        self,
        account: str | None = None,
        status: list[str] | str | None = None,
        severity: list[str] | str | None = None,
        owner: str | None = None,
        first: int = 100,
        after: str | None = None,
        all_pages: bool = False,
        limit: int | None = None,
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

        cases = []
        user = self.get_current_user()
        tracked_map = self.state_service.get_tracked_cases(user)
        current_after = after

        while True:
            batch_size = min(first, limit - len(cases)) if limit else first
            if batch_size <= 0:
                break

            variables = {
                "where": where if where else None,
                "first": batch_size,
                "after": current_after,
            }

            data = self.api_client.execute_graphql(
                GQL_GET_CASES_BY_FILTERS, variables, url=self.graphql_url
            )
            uiapi = data.get("redhat_support_uiapi") or {}
            query_data = uiapi.get("query") or {}
            case_container = query_data.get("RedHatSupportCase") or {}
            edges = case_container.get("edges") or []

            if not edges:
                break

            for edge in edges:
                node = edge.get("node")
                if node:
                    c = self._normalize_case_node(node)
                    # Auto-track owned cases (Option B)
                    if (c.contact_sso and c.contact_sso.lower() == user.lower()) or (
                        c.owner and c.owner.lower() == user.lower()
                    ):
                        self.state_service.register_owned_case(
                            user=user,
                            case_number=c.case_number,
                            summary=c.summary,
                            account_number=c.account_number,
                            status=c.status,
                            severity=c.severity,
                            last_modified_date=c.last_modified_date,
                        )

                    # Annotate tracking & unread badges
                    tc = tracked_map.get(c.case_number)
                    if tc:
                        c.is_followed = tc.relationship in ("followed", "both")
                        c.has_unread = (
                            tc.latest_known.unread_comment_count > 0
                            or tc.latest_known.status_changed
                        )
                        c.unread_count = tc.latest_known.unread_comment_count
                        c.status_changed = tc.latest_known.status_changed
                        if tc.read_checkpoint and tc.read_checkpoint.status:
                            c.old_status = tc.read_checkpoint.status
                    cases.append(c)

            page_info = case_container.get("pageInfo") or {}
            has_next = page_info.get("hasNextPage", False)
            end_cursor = page_info.get("endCursor")

            if not has_next or not end_cursor:
                break
            if not all_pages and (limit is None or len(cases) >= limit):
                break

            current_after = end_cursor

        return cases

    def get_case_details(
        self,
        case_number: str,
        first_comments: int = 200,
        all_comments: bool = True,
        max_comments: int | None = None,
        use_cache: bool = True,
        known_last_modified: str | None = None,
    ) -> CaseDetails | None:
        c_num = str(case_number).strip()
        if use_cache and self.cache_service.enabled:
            cached = self.cache_service.get_case_details(c_num)
            if cached:
                cached_details, _cached_atts, cached_lmd = cached
                if (
                    known_last_modified
                    and cached_lmd
                    and known_last_modified == cached_lmd
                ):
                    return cached_details
                probe = self.check_case_last_modified(c_num)
                if probe and probe[1] and cached_lmd and probe[1] == cached_lmd:
                    return cached_details

        details = self._get_case_details_graphql(
            case_number=c_num,
            first_comments=first_comments,
            all_comments=all_comments,
            max_comments=max_comments,
        )
        if details and use_cache and self.cache_service.enabled:
            lmd = details.case.last_modified_date if details.case else ""
            self.cache_service.set_case_details(c_num, details, last_modified_date=lmd)
        return details

    def _get_case_details_graphql(
        self,
        case_number: str,
        first_comments: int = 200,
        all_comments: bool = True,
        max_comments: int | None = None,
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
            comments.append(self._normalize_comment_node(c_node))

        # Paginate subsequent comments if hasNextPage is True
        page_info = comments_container.get("pageInfo") or {}
        has_next = page_info.get("hasNextPage", False)
        end_cursor = page_info.get("endCursor")

        while (
            has_next
            and end_cursor
            and (all_comments or (max_comments and len(comments) < max_comments))
        ):
            batch_size = (
                min(first_comments, max_comments - len(comments))
                if max_comments
                else first_comments
            )
            next_vars = {
                "caseNumber": str(case_number),
                "first": batch_size,
                "after": end_cursor,
            }
            next_data = self.api_client.execute_graphql(
                GQL_GET_CASE_AND_COMMENTS, next_vars, url=self.graphql_url
            )
            next_uiapi = next_data.get("redhat_support_uiapi") or {}
            next_query = next_uiapi.get("query") or {}
            next_cases = next_query.get("RedHatSupportCase") or {}
            next_edges = next_cases.get("edges") or []
            if not next_edges:
                break
            next_node = next_edges[0].get("node") or {}
            next_comments_container = next_node.get("CaseComments__r") or {}
            next_comment_edges = next_comments_container.get("edges") or []
            if not next_comment_edges:
                break
            for c_edge in next_comment_edges:
                c_node = c_edge.get("node") or {}
                comments.append(self._normalize_comment_node(c_node))

            next_page_info = next_comments_container.get("pageInfo") or {}
            has_next = next_page_info.get("hasNextPage", False)
            end_cursor = next_page_info.get("endCursor")

        return CaseDetails(
            case=case_obj,
            description=description or "",
            comments=comments,
            attachments=[],
            notified_users=[],
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
        return self._add_comment_graphql(
            case_number=case_number,
            body=body,
            is_public=is_public,
            status=status,
            case_internal_id=case_internal_id,
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

        self.cache_service.invalidate_case(case_number)
        return CaseComment(
            id=str(comment_id),
            body=body,
            created_date=created_date,
            created_by="Customer",
            is_public=is_public,
            is_customer=True,
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
        return self._update_case_graphql(
            case_number=case_number,
            status=status,
            severity=severity,
            summary=summary,
            description=description,
            case_internal_id=case_internal_id,
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
            mapped_status = STATUS_MAP.get(status.lower(), status)
            if mapped_status == "Waiting on Red Hat":
                mapped_status = "In Progress"
            elif mapped_status == "Waiting on Customer":
                mapped_status = "Waiting on Customer Action Required"
            patch_case["Status"] = mapped_status
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
        res = (
            data.get("redhat_support_uiapi", {})
            .get("CaseUpdate", {})
            .get("RedHatSupportRecord", {})
        )
        self.cache_service.invalidate_case(case_number)
        return res

    def get_active_products(
        self, first: int = 200, all_pages: bool = True, use_cache: bool = True
    ) -> list[ProductItem]:
        """Fetches active products from the product catalog."""
        if use_cache and all_pages and self.cache_service.enabled:
            cached_prods = self.cache_service.get_products()
            if cached_prods is not None:
                return cached_prods

        products = self._get_active_products_graphql(first=first, all_pages=all_pages)
        if use_cache and all_pages and self.cache_service.enabled and products:
            self.cache_service.set_products(products)
        return products

    def _get_active_products_graphql(
        self, first: int = 200, all_pages: bool = True
    ) -> list[ProductItem]:
        products = []
        current_after = None

        while True:
            variables = {"first": first, "after": current_after}
            data = self.api_client.execute_graphql(
                GQL_GET_ACTIVE_PRODUCTS, variables, url=self.graphql_url
            )

            container = (
                data.get("redhat_support_uiapi", {})
                .get("query", {})
                .get("RedHatSupportProduct2", {})
            )
            edges = container.get("edges", [])
            if not edges:
                break
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

            page_info = container.get("pageInfo") or {}
            has_next = page_info.get("hasNextPage", False)
            end_cursor = page_info.get("endCursor")

            if not all_pages or not has_next or not end_cursor:
                break

            current_after = end_cursor

        return products

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
        """Creates a new Red Hat support case using the GraphQL API."""
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
            "Status": "In Progress",
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
        self.cache_service.invalidate_case_lists()
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
        """Tracks one or more support cases in local state.yaml.
        Initializes the read checkpoint and returns list of followed case numbers."""
        user = username or self.get_current_user()
        if not isinstance(case_numbers, list):
            case_numbers = [case_numbers]

        followed = []
        for c_num in case_numbers:
            c_str = str(c_num).strip()
            if not c_str:
                continue
            summary = ""
            acc = ""
            status = ""
            severity = ""
            last_comm_id = ""
            last_comm_date = ""
            last_mod = ""
            with contextlib.suppress(Exception):
                details = self.get_case_details(c_str, first_comments=50)
                if details:
                    summary = details.case.summary
                    acc = details.case.account_number
                    status = details.case.status
                    severity = details.case.severity
                    last_mod = details.case.last_modified_date
                    if details.comments:
                        last_comm_id = details.comments[-1].id
                        last_comm_date = details.comments[-1].created_date

            self.state_service.add_tracked_case(
                user=user,
                case_number=c_str,
                relationship="followed",
                summary=summary,
                account_number=acc,
                status=status,
                severity=severity,
                last_comment_id=last_comm_id,
                last_comment_date=last_comm_date,
                last_modified_date=last_mod,
                source="cli",
            )
            followed.append(c_str)
        return followed

    def unfollow_case(
        self, case_numbers: list[str] | str, username: str | None = None
    ) -> list[str]:
        """Removes one or more support cases from followed cases in local state.yaml."""
        user = username or self.get_current_user()
        if not isinstance(case_numbers, list):
            case_numbers = [case_numbers]

        unfollowed = []
        for c_num in case_numbers:
            c_str = str(c_num).strip()
            if not c_str:
                continue
            if self.state_service.remove_followed_case(c_str, user):
                unfollowed.append(c_str)
        return unfollowed

    def sync_tracked_cases(
        self, username: str | None = None, account: str | None = None
    ) -> dict[str, Any]:
        """Syncs all tracked cases (owned + followed) against the remote GraphQL API,
        detecting status changes and new comments since last checkpoints."""
        user = username or self.get_current_user()
        tracked = self.state_service.get_tracked_cases(user)
        if not tracked:
            return {"synced_count": 0, "updated_cases": [], "new_comments": 0}

        def sync_one(c_num: str) -> dict[str, Any] | None:
            tc = tracked[c_num]
            try:
                details = self.get_case_details(c_num, first_comments=50)
                if not details:
                    return None

                status = details.case.status
                severity = details.case.severity
                last_mod = details.case.last_modified_date

                # Check unread comments since checkpoint
                last_read_date = tc.read_checkpoint.last_read_comment_date or ""
                last_read_id = tc.read_checkpoint.last_read_comment_id or ""

                new_comments = []
                for comm in details.comments:
                    if (
                        last_read_date
                        and comm.created_date > last_read_date
                        or not last_read_date
                        and last_read_id
                        and comm.id != last_read_id
                    ):
                        new_comments.append(comm)

                latest_comm_id = details.comments[-1].id if details.comments else ""
                latest_comm_date = (
                    details.comments[-1].created_date if details.comments else ""
                )
                latest_comm_author = (
                    details.comments[-1].created_by if details.comments else ""
                )
                latest_comm_snippet = (
                    details.comments[-1].body[:120].strip() if details.comments else ""
                )

                status_changed = bool(
                    tc.read_checkpoint.status
                    and status
                    and tc.read_checkpoint.status != status
                )

                self.state_service.update_latest_known(
                    user=user,
                    case_number=c_num,
                    status=status,
                    severity=severity,
                    last_modified_date=last_mod,
                    latest_comment_id=latest_comm_id,
                    latest_comment_date=latest_comm_date,
                    latest_comment_author=latest_comm_author,
                    latest_comment_snippet=latest_comm_snippet,
                    new_comment_count=len(new_comments),
                )

                if len(new_comments) > 0 or status_changed:
                    return {
                        "case_number": c_num,
                        "summary": details.case.summary,
                        "status": status,
                        "old_status": tc.read_checkpoint.status,
                        "status_changed": status_changed,
                        "new_comment_count": len(new_comments),
                        "new_comments": new_comments,
                        "latest_comment_author": latest_comm_author,
                        "latest_comment_snippet": latest_comm_snippet,
                    }
            except Exception:  # noqa: BLE001
                return None
            return None

        with ThreadPoolExecutor(max_workers=min(10, max(1, len(tracked)))) as executor:
            results = list(executor.map(sync_one, list(tracked.keys())))

        updated = [r for r in results if r]
        total_new_comments = sum(r["new_comment_count"] for r in updated)
        return {
            "synced_count": len(tracked),
            "updated_cases": updated,
            "new_comments": total_new_comments,
        }

    def sync_followed_cases(
        self, username: str | None = None, account: str | None = None
    ) -> list[str]:
        """Compatibility wrapper that runs sync_tracked_cases and returns updated case numbers."""
        res = self.sync_tracked_cases(username=username, account=account)
        return [u["case_number"] for u in res.get("updated_cases", [])]

    def list_followed_cases(
        self,
        username: str | None = None,
        sync: bool = False,
        account: str | None = None,
        fetch_live: bool = True,
    ) -> list[Case]:
        """Returns the list of followed cases for the user. If sync is True, updates checkpoints first."""
        user = username or self.get_current_user()
        if sync:
            self.sync_tracked_cases(username=user, account=account)

        followed_map = self.state_service.get_followed_cases(user)
        if not followed_map:
            return []

        case_numbers = list(followed_map.keys())
        if not fetch_live:
            return [
                Case(
                    case_number=c_num,
                    summary=tc.summary or "Followed Case",
                    status=tc.latest_known.status
                    or tc.read_checkpoint.status
                    or "Unknown",
                    severity=tc.latest_known.severity
                    or tc.read_checkpoint.severity
                    or "Normal",
                    account_number=tc.account_number,
                    is_followed=True,
                    has_unread=(
                        tc.latest_known.unread_comment_count > 0
                        or tc.latest_known.status_changed
                    ),
                    unread_count=tc.latest_known.unread_comment_count,
                    status_changed=tc.latest_known.status_changed,
                    old_status=tc.read_checkpoint.status,
                )
                for c_num, tc in followed_map.items()
            ]

        def fetch_one(c_num: str) -> Case | None:
            with contextlib.suppress(Exception):
                details = self.get_case_details(c_num, first_comments=1)
                if details and details.case:
                    c = details.case
                    tc = followed_map.get(c_num)
                    c.is_followed = True
                    if tc:
                        c.has_unread = (
                            tc.latest_known.unread_comment_count > 0
                            or tc.latest_known.status_changed
                        )
                        c.unread_count = tc.latest_known.unread_comment_count
                        c.status_changed = tc.latest_known.status_changed
                        c.old_status = tc.read_checkpoint.status
                    return c
            f_info = followed_map.get(c_num)
            return Case(
                case_number=c_num,
                summary=f_info.summary if f_info else "Followed Case",
                status="Unknown",
                severity="",
                account_number=f_info.account_number if f_info else "",
                is_followed=True,
            )

        with ThreadPoolExecutor(max_workers=min(10, len(case_numbers))) as executor:
            cases = list(executor.map(fetch_one, case_numbers))

        return [c for c in cases if c is not None]
