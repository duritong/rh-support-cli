import os

# --- CONFIGURATION ---
DEFAULT_GRAPHQL_URL = "https://graphql.redhat.com"
DEFAULT_ATTACHMENT_URL = "https://api.access.redhat.com/support"

GRAPHQL_URL = os.environ.get("RH_GRAPHQL_URL", DEFAULT_GRAPHQL_URL)
API_URL = os.environ.get("RH_API_URL", GRAPHQL_URL)
ATTACHMENT_API_URL = os.environ.get("RH_ATTACHMENT_API_URL", DEFAULT_ATTACHMENT_URL)
SSO_URL = os.environ.get(
    "RH_SSO_URL",
    "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token",
)


SEVERITY_MAP = {
    "1": "1 (Urgent)",
    "urgent": "1 (Urgent)",
    "2": "2 (High)",
    "high": "2 (High)",
    "3": "3 (Normal)",
    "normal": "3 (Normal)",
    "medium": "3 (Normal)",
    "4": "4 (Low)",
    "low": "4 (Low)",
}


class COLORS:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    GREY = "\033[90m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


from rh_support_lib.statuses import (
    COMMENT_STATUS_CHOICES,
    STATUS_CLOSED,
    STATUS_FILTER_MAP,
    STATUS_IN_PROGRESS,
    STATUS_MAP,
    STATUS_WAITING_ON_CUSTOMER,
    STATUS_WAITING_ON_RED_HAT,
    determine_reply_status,
    get_status_choices_for_case,
    get_status_color,
)

__all__ = [
    "API_URL",
    "ATTACHMENT_API_URL",
    "COLORS",
    "COMMENT_STATUS_CHOICES",
    "DEFAULT_ATTACHMENT_URL",
    "DEFAULT_GRAPHQL_URL",
    "GRAPHQL_URL",
    "SEVERITY_MAP",
    "SSO_URL",
    "STATUS_CLOSED",
    "STATUS_FILTER_MAP",
    "STATUS_IN_PROGRESS",
    "STATUS_MAP",
    "STATUS_WAITING_ON_CUSTOMER",
    "STATUS_WAITING_ON_RED_HAT",
    "determine_reply_status",
    "get_status_choices_for_case",
    "get_status_color",
]
