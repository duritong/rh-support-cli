import os

# --- CONFIGURATION ---
DEFAULT_REST_URL = "https://api.access.redhat.com/support/v1"
DEFAULT_GRAPHQL_URL = "https://graphql.redhat.com"
DEFAULT_ATTACHMENT_URL = "https://api.access.redhat.com/support"

API_URL = os.environ.get("RH_API_URL", DEFAULT_REST_URL)
GRAPHQL_URL = os.environ.get("RH_GRAPHQL_URL", DEFAULT_GRAPHQL_URL)
ATTACHMENT_API_URL = os.environ.get("RH_ATTACHMENT_API_URL", DEFAULT_ATTACHMENT_URL)
BACKEND_MODE = os.environ.get("RH_BACKEND", "graphql")
SSO_URL = os.environ.get(
    "RH_SSO_URL",
    "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token",
)

STATUS_MAP = {
    "redhat": "Waiting on Red Hat",
    "customer": "Waiting on Customer",
    "closed": "Closed",
}

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

STATUS_FILTER_MAP = {
    "customer": "Waiting on Customer,Waiting on Customer Action Required",
    "redhat": "Waiting on Red Hat,In Progress,Waiting on Engineering,Needs New Owner",
    "closed": "Closed",
    "open": "Waiting on Red Hat,Waiting on Customer,Waiting on Customer Action Required,In Progress,Waiting on Engineering,Needs New Owner",
    "in_progress": "In Progress",
    "inprogress": "In Progress",
    "engineering": "Waiting on Engineering",
    "needs_owner": "Needs New Owner",
    "needs_new_owner": "Needs New Owner",
    "action_required": "Waiting on Customer Action Required",
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
