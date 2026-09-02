from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Case:
    """Represents high-level metadata for a Red Hat support case."""

    case_number: str
    summary: str
    status: str
    severity: str
    product: str = ""
    version: str = ""
    account_number: str = ""
    created_date: str = ""
    last_modified_date: str = ""
    created_by: str = ""
    last_modified_by: str = ""
    owner: str = ""
    contact_name: str = ""
    contact_sso: str = ""
    case_type: str = "Standard"
    id: str = ""  # Internal Salesforce ID (e.g. 500...)


@dataclass
class CaseComment:
    """Represents a single comment on a support case."""

    id: str
    body: str
    created_date: str
    created_by: str
    is_public: bool = True
    visibility: str = "Public"
    is_associate: bool = False
    is_customer: bool = False


@dataclass
class Attachment:
    """Represents a file attachment attached to a support case."""

    uuid: str
    file_name: str
    size: int
    size_kb: float
    created_date: str
    created_by: str = ""
    description: str = ""
    download_link: str = ""
    upload_status: str = "Completed"
    case_number: str = ""


@dataclass
class CaseDetails:
    """Complete detail view of a support case including description and comments."""

    case: Case
    description: str
    comments: list[CaseComment] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    notified_users: list[str] = field(default_factory=list)


@dataclass
class ProductItem:
    """Represents a supported Red Hat product from the catalog."""

    id: str
    name: str
    version_name: str = ""
    family: str = ""
    parent_id: str | None = None


@dataclass
class FollowedCase:
    """Represents a followed/watched support case in local state."""

    case_number: str
    added_at: str = ""
    source: str = "cli"  # "cli" | "sync"
    summary: str = ""
    account_number: str = ""
