import os

# --- CONFIGURATION ---
API_URL = os.environ.get("RH_API_URL", "https://api.access.redhat.com/support/v1")
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
    "customer": "Waiting on Customer",
    "redhat": "Waiting on Red Hat",
    "closed": "Closed",
    "open": "Waiting on Red Hat,Waiting on Customer",
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
