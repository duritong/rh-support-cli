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
    is_followed: bool = False
    has_unread: bool = False
    unread_count: int = 0
    status_changed: bool = False
    old_status: str = ""


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
class CaseCheckpoint:
    """Represents a read checkpoint for a case."""

    status: str = ""
    severity: str = ""
    last_read_comment_id: str = ""
    last_read_comment_date: str = ""
    marked_read_at: str = ""


@dataclass
class CaseLatestKnown:
    """Represents the latest known remote state for a case."""

    status: str = ""
    severity: str = ""
    last_modified_date: str = ""
    latest_comment_id: str = ""
    latest_comment_date: str = ""
    latest_comment_author: str = ""
    latest_comment_snippet: str = ""
    unread_comment_count: int = 0
    status_changed: bool = False


@dataclass
class TrackedCase:
    """Represents a locally tracked (owned, followed, or both) support case."""

    case_number: str
    relationship: str = "followed"  # "owned" | "followed" | "both"
    followed_at: str = ""
    summary: str = ""
    account_number: str = ""
    source: str = "cli"
    read_checkpoint: CaseCheckpoint = field(default_factory=CaseCheckpoint)
    latest_known: CaseLatestKnown = field(default_factory=CaseLatestKnown)

    @property
    def added_at(self) -> str:
        return self.followed_at

    @property
    def has_unread(self) -> bool:
        return self.latest_known.status_changed or (
            self.latest_known.unread_comment_count > 0
        )

    @property
    def status_changed(self) -> bool:
        return self.latest_known.status_changed

    @property
    def unread_count(self) -> int:
        return self.latest_known.unread_comment_count


# Backwards-compatibility alias
FollowedCase = TrackedCase
