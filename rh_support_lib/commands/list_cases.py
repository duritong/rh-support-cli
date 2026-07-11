import sys
import requests
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.box import ROUNDED
from rh_support_lib.constants import API_URL, STATUS_FILTER_MAP, SEVERITY_MAP


def cmd_list(args, api_client, config=None):
    from rh_support_lib.api import RedHatAPIClient, LegacyAPIClient

    if not isinstance(api_client, (RedHatAPIClient, LegacyAPIClient)):
        api_client = LegacyAPIClient(api_client)
    token = api_client.get_token()

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

    console = Console(no_color=args.simple_output, highlight=not args.simple_output)

    # Re-calculate table styling
    box_style = None if args.simple_output else ROUNDED
    table = Table(
        show_header=True,
        header_style="bold magenta" if not args.simple_output else "",
        box=box_style,
        pad_edge=False,
    )

    table.add_column("NUMBER", width=12)
    table.add_column("TITLE", ratio=1, min_width=30)
    table.add_column("OWNER", width=19)
    table.add_column("MODIFIED BY", width=19)
    table.add_column("SEVERITY", width=10)
    table.add_column("STATUS", width=20)
    table.add_column("LAST UPDATED", width=20)

    for c in cases:
        num = str(c.get("caseNumber", "") or c.get("id", ""))
        title = c.get("summary", "")

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

        sev = c.get("severity", "")
        status = c.get("status", "")

        # Last Updated
        last_updated = c.get("lastModifiedDate", "")
        if len(last_updated) > 19:
            last_updated = last_updated[:19].replace("T", " ")

        # Styles
        sev_style = ""
        if not args.simple_output:
            if "Urgent" in sev or "1" in sev:
                sev_style = "bold red"
            elif "High" in sev or "2" in sev:
                sev_style = "red"
            elif "Normal" in sev or "medium" in sev or "3" in sev:
                sev_style = "yellow"
            elif "Low" in sev or "4" in sev:
                sev_style = "green"

        status_style = ""
        if not args.simple_output:
            if "Red Hat" in status or "red hat" in status.lower():
                status_style = "bold orange3"
            elif "customer" in status.lower():
                status_style = "bold green"
            elif "closed" in status.lower():
                status_style = "dim"

        table.add_row(
            num,
            title,
            owner,
            mod_by,
            Text(sev, style=sev_style),
            Text(status, style=status_style),
            last_updated,
        )

    console.print()
    console.print(table)
