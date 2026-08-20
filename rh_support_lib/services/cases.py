from __future__ import annotations

from typing import Any

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
    """Service to handle all support case operations exclusively via the Red Hat GraphQL API."""

    def __init__(self, api_client, graphql_url: str | None = None):
        self.api_client = api_client
        self.graphql_url = graphql_url or GRAPHQL_URL

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

    def list_cases(
        self,
        account: str | None = None,
        status: list[str] | str | None = None,
        severity: list[str] | str | None = None,
        owner: str | None = None,
        first: int = 100,
        after: str | None = None,
    ) -> list[Case]:
        """Lists support cases applying dynamic GraphQL filters."""
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

    def get_case_details(
        self, case_number: str, first_comments: int = 200
    ) -> CaseDetails | None:
        """Fetches full case details and its comments in a single GraphQL round-trip."""
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
            c_body = (
                c_node.get("Body__c", {}).get("value", "")
                if isinstance(c_node.get("Body__c"), dict)
                else c_node.get("body", "")
            )
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
                is_pub = not (c_vis and str(c_vis).lower() != "public")

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
                    else ("Public" if is_pub else "Private"),
                    created_date=c_date or "",
                )
            )

        return CaseDetails(
            case=case_obj,
            description=description or "",
            comments=comments,
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

    def get_active_products(self, first: int = 200) -> list[ProductItem]:
        """Fetches active products from the product catalog."""
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
        """Creates a new Red Hat support case and returns the assigned case number."""
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
