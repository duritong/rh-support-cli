import os
import sys

from rich.box import ROUNDED
from rich.console import Console
from rich.table import Table
from rich.text import Text

from rh_support_lib.constants import SEVERITY_MAP, STATUS_FILTER_MAP
from rh_support_lib.services.cases import CaseService


def cmd_list(args, api_client, config=None):
    if config is None:
        config = {}

    # 1. Gather Filters (Bookmarks + CLI)
    filters = {}

    # Default Bookmark
    default_bk = config.get("default_bookmark")
    if default_bk and not getattr(args, "no_default_bookmark", False):
        bks = default_bk if isinstance(default_bk, list) else [default_bk]
        for bk_name in bks:
            bk_data = config.get("bookmarks", {}).get(bk_name)
            if bk_data:
                filters.update(bk_data)
            else:
                print(f"Warning: Default bookmark '{bk_name}' not found.")

    # Explicit Bookmarks
    if getattr(args, "bookmark", None):
        for bk_name in args.bookmark:
            bk_data = config.get("bookmarks", {}).get(bk_name)
            if bk_data:
                filters.update(bk_data)
            else:
                print(f"Warning: Bookmark '{bk_name}' not found.")
    # CLI Overrides (only if explicitly provided)
    if getattr(args, "account", None):
        filters["account"] = args.account
    if getattr(args, "status", None):
        filters["status"] = args.status
    if getattr(args, "severity", None):
        filters["severity"] = args.severity
    if getattr(args, "owner", None):
        filters["owner"] = args.owner
    if getattr(args, "following", False):
        filters["following"] = True

    # 2. Build parameters for CaseService
    acc = filters.get("account") or filters.get("accountNumber")
    raw_status = filters.get("status")
    mapped_statuses = None
    if raw_status:
        if isinstance(raw_status, str):
            raw_status = [raw_status]
        mapped_statuses = []
        for s in raw_status:
            val = STATUS_FILTER_MAP.get(str(s).lower(), s)
            for v in val.split(","):
                mapped_statuses.append(v.strip())

    raw_sev = filters.get("severity")
    mapped_sevs = None
    if raw_sev:
        if isinstance(raw_sev, str):
            raw_sev = [raw_sev]
        mapped_sevs = []
        for s in raw_sev:
            mapped_sevs.append(SEVERITY_MAP.get(str(s).lower(), s))

    own = filters.get("owner") or filters.get("ownerSSOName")
    is_following = bool(filters.get("following"))
    sync_flag = bool(getattr(args, "sync", False))
    user_override = getattr(args, "user", None) or filters.get("user")
    backend = getattr(args, "backend", None)

    service = CaseService(api_client, backend=backend)
    try:
        if is_following:
            print("Fetching followed cases...")
            cases = service.list_followed_cases(
                username=user_override,
                sync=sync_flag,
                account=str(acc) if acc else None,
            )
        else:
            print("Fetching cases...")
            cases = service.list_cases(
                account=str(acc) if acc else None,
                status=mapped_statuses,
                severity=mapped_sevs,
                owner=str(own) if own else None,
            )
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Network Error: {e}")
    if not cases:
        print("No cases found.")
        return

    console_width = None
    if "COLUMNS" in os.environ:
        try:
            console_width = int(os.environ["COLUMNS"])
        except ValueError:
            pass
    if console_width is None and (args.simple_output or not sys.stdout.isatty()):
        console_width = 200
    color_sys = (
        None
        if args.simple_output
        else ("standard" if os.environ.get("FORCE_COLOR") == "1" else "auto")
    )
    console = Console(
        no_color=args.simple_output,
        highlight=not args.simple_output,
        width=console_width,
        height=int(os.environ.get("LINES", "100")),
        color_system=color_sys,
        force_terminal=True if os.environ.get("FORCE_COLOR") == "1" else None,
    )

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
        num = c.case_number or c.id
        title = c.summary
        owner = c.owner or c.contact_name or "Unknown"
        mod_by = c.last_modified_by or "Red Hat Support"
        sev = c.severity
        status = c.status
        last_updated = c.last_modified_date or ""
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
