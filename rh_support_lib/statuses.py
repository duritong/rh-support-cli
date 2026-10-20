from __future__ import annotations

from rh_support_lib.constants import COLORS

# --- CANONICAL STATUS CONSTANTS ---
STATUS_WAITING_ON_RED_HAT = "Waiting on Red Hat"
STATUS_WAITING_ON_CUSTOMER = "Waiting on Customer Action Required"
STATUS_WAITING_ON_CUSTOMER_LEGACY = "Waiting on Customer"
STATUS_IN_PROGRESS = "In Progress"
STATUS_CLOSED = "Closed"
STATUS_WAITING_ON_ENGINEERING = "Waiting on Engineering"
STATUS_NEEDS_NEW_OWNER = "Needs New Owner"
STATUS_REOPENED = "Reopened"

# Canonical comment status choices presented in UI dropdowns
COMMENT_STATUS_CHOICES: list[tuple[str, str]] = [
    (STATUS_IN_PROGRESS, STATUS_IN_PROGRESS),
    (STATUS_WAITING_ON_CUSTOMER, STATUS_WAITING_ON_CUSTOMER),
    (STATUS_CLOSED, STATUS_CLOSED),
    (STATUS_WAITING_ON_ENGINEERING, STATUS_WAITING_ON_ENGINEERING),
    (STATUS_REOPENED, STATUS_REOPENED),
]

# Normalizes shorthand or legacy status inputs to canonical backend strings
STATUS_MAP: dict[str, str] = {
    "redhat": STATUS_IN_PROGRESS,
    "customer": STATUS_WAITING_ON_CUSTOMER,
    "action_required": STATUS_WAITING_ON_CUSTOMER,
    "closed": STATUS_CLOSED,
    "reopened": STATUS_REOPENED,
    "inprogress": STATUS_IN_PROGRESS,
    "in_progress": STATUS_IN_PROGRESS,
    "engineering": STATUS_WAITING_ON_ENGINEERING,
    "needs_owner": STATUS_NEEDS_NEW_OWNER,
    "waiting on red hat": STATUS_IN_PROGRESS,
    "waiting on customer": STATUS_WAITING_ON_CUSTOMER,
    "waiting on customer action required": STATUS_WAITING_ON_CUSTOMER,
}

# Expands query filters to all matching backend statuses
STATUS_FILTER_MAP: dict[str, str] = {
    "customer": f"{STATUS_WAITING_ON_CUSTOMER_LEGACY},{STATUS_WAITING_ON_CUSTOMER}",
    "redhat": f"{STATUS_WAITING_ON_RED_HAT},{STATUS_IN_PROGRESS},{STATUS_WAITING_ON_ENGINEERING},{STATUS_NEEDS_NEW_OWNER}",
    "closed": STATUS_CLOSED,
    "open": (
        f"{STATUS_WAITING_ON_RED_HAT},"
        f"{STATUS_WAITING_ON_CUSTOMER_LEGACY},"
        f"{STATUS_WAITING_ON_CUSTOMER},"
        f"{STATUS_IN_PROGRESS},"
        f"{STATUS_WAITING_ON_ENGINEERING},"
        f"{STATUS_NEEDS_NEW_OWNER}"
    ),
    "in_progress": STATUS_IN_PROGRESS,
    "inprogress": STATUS_IN_PROGRESS,
    "engineering": STATUS_WAITING_ON_ENGINEERING,
    "needs_owner": STATUS_NEEDS_NEW_OWNER,
    "needs_new_owner": STATUS_NEEDS_NEW_OWNER,
    "action_required": STATUS_WAITING_ON_CUSTOMER,
}


def expand_status_filter(
    status: list[str] | tuple[str, ...] | str | None,
) -> list[str] | None:
    """Expands shorthand status filter values (e.g. 'open', 'customer') to backend status strings.

    Returns None if status is None or 'all', meaning no status filtering is applied.
    """
    if not status:
        return None

    if isinstance(status, (list, tuple)):
        raw_items = list(status)
    else:
        raw_items = [status]

    # Check if 'all' is explicitly requested
    for item in raw_items:
        if str(item).strip().lower() == "all":
            return None

    mapped: list[str] = []
    for s in raw_items:
        val = STATUS_FILTER_MAP.get(str(s).strip().lower(), str(s).strip())
        for v in val.split(","):
            clean = v.strip()
            if clean and clean not in mapped:
                mapped.append(clean)

    return mapped if mapped else None


def get_status_choices_for_case(
    case_status: str | None = None,
) -> list[tuple[str, str]]:
    """Returns canonical comment status choices, ensuring the case's current status is present."""
    choices = list(COMMENT_STATUS_CHOICES)
    if case_status and not any(case_status == val for _, val in choices):
        choices.append((case_status, case_status))
    return choices


def is_waiting_on_customer(status: str | None) -> bool:
    """Returns True if the given case status indicates action is required by the customer."""
    if not status:
        return False
    return status.strip().lower() in (
        "waiting on customer",
        "waiting on customer action required",
    )


def determine_reply_status(current_status: str | None) -> str | None:
    """
    Central business logic for determining the target status when drafting or posting a reply:
    - Customer responding to Red Hat (Waiting on Customer) -> transitions to In Progress.
    - All other statuses (Waiting on Red Hat, Engineering, Closed, etc.) -> remain unchanged.
    """
    if is_waiting_on_customer(current_status):
        return STATUS_IN_PROGRESS

    return current_status


def get_status_color(stat_str: str | None) -> str | None:
    """Returns the ANSI/Rich color code corresponding to a support case status."""
    if not stat_str:
        return None
    s = str(stat_str).lower()
    if "red hat" in s or "in progress" in s or "needs new owner" in s:
        return COLORS.YELLOW
    if "customer" in s or "action required" in s:
        return COLORS.GREEN
    if "engineering" in s:
        return COLORS.MAGENTA
    if "closed" in s:
        return COLORS.GREY
    if "reopened" in s:
        return COLORS.BLUE
    return None
