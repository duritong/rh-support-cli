import shutil
import sys
import requests
from rh_support_lib.constants import API_URL, STATUS_FILTER_MAP, SEVERITY_MAP
from rh_support_lib.utils import colorize, get_severity_color, get_status_color


def cmd_list(args, token, config=None):
    if config is None:
        config = {}

    # Construct filter payload
    payload = {"maxResults": 100, "offset": 0}

    # 1. Gather Filters (Bookmarks + CLI)
    filters = {}

    # Default Bookmark
    default_bk = config.get("default_bookmark")
    if default_bk and not args.no_default_bookmark:
        bks = default_bk if isinstance(default_bk, list) else [default_bk]
        for bk_name in bks:
            bk_data = config.get("bookmarks", {}).get(bk_name)
            if bk_data:
                filters.update(bk_data)
            else:
                print(f"Warning: Default bookmark '{bk_name}' not found.")

    # Explicit Bookmarks
    if args.bookmark:
        for bk_name in args.bookmark:
            bk_data = config.get("bookmarks", {}).get(bk_name)
            if bk_data:
                filters.update(bk_data)
            else:
                print(f"Warning: Bookmark '{bk_name}' not found.")

    # CLI Overrides (only if explicitly provided)
    if args.account:
        filters["account"] = args.account
    if args.status:
        filters["status"] = args.status
    if args.severity:
        filters["severity"] = args.severity
    if args.owner:
        filters["owner"] = args.owner

    # 2. Build Payload from Filters

    # Account
    acc = filters.get("account") or filters.get("accountNumber")
    if acc:
        payload["accountNumber"] = str(acc)

    # Status
    raw_status = filters.get("status")
    if raw_status:
        if isinstance(raw_status, str):
            raw_status = [raw_status]

        mapped_statuses = []
        for s in raw_status:
            val = STATUS_FILTER_MAP.get(str(s).lower(), s)
            for v in val.split(","):
                mapped_statuses.append(v.strip())

        if len(mapped_statuses) == 1:
            payload["status"] = mapped_statuses[0]
        else:
            payload["statuses"] = mapped_statuses

        if any("closed" in s.lower() for s in mapped_statuses):
            payload["includeClosed"] = True

    # Severity
    raw_sev = filters.get("severity")
    if raw_sev:
        if isinstance(raw_sev, str):
            raw_sev = [raw_sev]

        mapped_sevs = []
        for s in raw_sev:
            mapped_sevs.append(SEVERITY_MAP.get(str(s).lower(), s))

        if len(mapped_sevs) == 1:
            payload["severity"] = mapped_sevs[0]
        else:
            payload["severities"] = mapped_sevs

    # Owner
    own = filters.get("owner") or filters.get("ownerSSOName")
    if own:
        payload["ownerSSOName"] = str(own)

    print("Fetching cases...")
    endpoint = f"{API_URL}/cases/filter"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # Handle different response formats (List vs Dict wrapper)
        if isinstance(data, list):
            cases = data
        elif isinstance(data, dict):
            cases = data.get("cases") or data.get("items") or data.get("list") or []
        else:
            cases = []

    except requests.exceptions.RequestException as e:
        if e.response is not None:
            print(f"Server Response: {e.response.text}")
        sys.exit(f"Network Error: {e}")

    if not cases:
        print("No cases found.")
        return

    # Dynamic Table Width
    try:
        term_width = shutil.get_terminal_size((140, 20)).columns
    except Exception:
        term_width = 140

    w_num = 12
    w_owner = 19
    w_mod = 19
    w_sev = 10
    w_status = 20
    w_updated = 20
    # 6 spaces for separation
    fixed_used = w_num + w_owner + w_mod + w_sev + w_status + w_updated + 6

    w_title = max(30, term_width - fixed_used)

    # Table Headers
    header_fmt = f"{{:<{w_num}}} {{:<{w_title}}} {{:<{w_owner}}} {{:<{w_mod}}} {{:<{w_sev}}} {{:<{w_status}}} {{:<{w_updated}}}"
    print(
        "\n"
        + header_fmt.format(
            "NUMBER",
            "TITLE",
            "OWNER",
            "MODIFIED BY",
            "SEVERITY",
            "STATUS",
            "LAST UPDATED",
        )
    )
    print("-" * (fixed_used + w_title))

    for c in cases:
        num = str(c.get("caseNumber", "") or c.get("id", ""))

        title = c.get("summary", "")
        if len(title) > (w_title - 3):
            title = title[: (w_title - 3)] + "..."

        # Owner Extraction
        owner_obj = c.get("owner") or c.get("contact") or {}
        owner = ""
        if isinstance(owner_obj, dict):
            owner = owner_obj.get("name") or owner_obj.get("fullName")
            if not owner:
                first = owner_obj.get("firstName", "")
                last = owner_obj.get("lastName", "")
                if first or last:
                    owner = f"{first} {last}".strip()
            if not owner:
                owner = owner_obj.get("ssoUsername")

        if not owner:
            owner = (
                c.get("contactName")
                or c.get("ownerId")
                or c.get("contactSSOName")
                or "Unknown"
            )

        if len(owner) > 19:
            owner = owner[:19]

        # Modified By Extraction
        mod_obj = c.get("lastModifiedBy") or c.get("modifiedBy")
        mod_by = ""
        if isinstance(mod_obj, dict):
            mod_by = mod_obj.get("name") or mod_obj.get("fullName")
            if not mod_by:
                first = mod_obj.get("firstName", "")
                last = mod_obj.get("lastName", "")
                if first or last:
                    mod_by = f"{first} {last}".strip()
            if not mod_by:
                mod_by = mod_obj.get("ssoUsername", "")
        elif isinstance(mod_obj, str):
            mod_by = mod_obj

        if not mod_by:
            mod_by = c.get("lastModifiedById") or c.get("createdById") or ""

        if len(mod_by) > 19:
            mod_by = mod_by[:19]

        sev = c.get("severity", "")
        status = c.get("status", "")

        # Last Updated
        last_updated = c.get("lastModifiedDate", "")
        if len(last_updated) > 19:
            last_updated = last_updated[:19].replace("T", " ")

        # Colorize
        c_sev = get_severity_color(sev)
        c_stat = get_status_color(status)

        def p(t, w):
            return f"{t:<{w}}"

        def pc(t, w, col):
            ct = colorize(t, col, args.simple_output)
            pad = w - len(t)
            if pad < 0:
                pad = 0
            return ct + " " * pad

        line = (
            p(num, w_num)
            + " "
            + p(title, w_title)
            + " "
            + p(owner, w_owner)
            + " "
            + p(mod_by, w_mod)
            + " "
            + pc(sev, w_sev, c_sev)
            + " "
            + pc(status, w_status, c_stat)
            + " "
            + p(last_updated, w_updated)
        )
        print(line)
