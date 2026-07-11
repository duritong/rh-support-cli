import sys
import pydoc
from rh_support_lib.constants import API_URL
from rh_support_lib.api import get_json


def cmd_show(args, api_client):
    from rh_support_lib.api import RedHatAPIClient, LegacyAPIClient

    if not isinstance(api_client, (RedHatAPIClient, LegacyAPIClient)):
        api_client = LegacyAPIClient(api_client)
    token = api_client.get_token()

    print(f"Fetching case {args.case}...")
    # 1. Fetch Case
    try:
        case = get_json(f"{API_URL}/cases/{args.case}", token)
    except Exception:
        case = None

    if not case or not isinstance(case, dict):
        sys.exit(f"Error: Case {args.case} not found or invalid response.")

    # 2. Extract Fields
    num = case.get("caseNumber") or case.get("id")
    title = case.get("summary")
    product = case.get("product")
    version = case.get("version")

    # Account
    account_num = case.get("accountNumber")
    account_str = f"{account_num}"

    # Created By
    created_by = case.get("createdById", "Unknown")
    created_date = case.get("createdDate", "")

    # Owner (reuse logic from list)
    owner_obj = case.get("owner") or case.get("contact") or {}
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
            case.get("contactName")
            or case.get("ownerId")
            or case.get("contactSSOName")
            or "Unknown"
        )

    status = case.get("status")
    case_type = case.get("caseType") or case.get("type")
    severity = case.get("severity")

    description = case.get("description", "")

    # 3. Fetch Comments
    comments = case.get("comments")
    if not comments:
        # Fetch separately
        comments = get_json(f"{API_URL}/cases/{args.case}/comments", token)

    if not isinstance(comments, list):
        comments = []

    # Sort comments
    comments.sort(key=lambda x: x.get("createdDate", ""), reverse=False)

    if args.simple_output:
        # Standard plain text representation for simple-output mode (guarantees backward compatibility and test passing)
        lines = []
        lines.append(f"CASE: {num}")
        lines.append(f"TITLE: {title}")
        lines.append(f"URL: https://access.redhat.com/support/cases/{num}")
        lines.append("-" * 80)
        lines.append(f"Product:      {product} {version}")
        lines.append(f"Account:      {account_str}")
        lines.append(f"Created:      {created_date} by {created_by}")
        lines.append(f"Assignee:     {owner}")
        lines.append(f"Status:       {status}")
        lines.append(f"Type:         {case_type}")
        lines.append(f"Severity:     {severity}")
        lines.append("-" * 80)
        lines.append("DESCRIPTION:\n")
        lines.append(description)
        lines.append("\n" + "=" * 80 + "\n")
        lines.append(f"COMMENTS ({len(comments)}):\n")

        for c in comments:
            c_date = c.get("createdDate", "")
            c_by = c.get("createdBy", "Unknown")
            is_public = "Public"
            if c.get("isPublic") is False:
                vis = c.get("visibility")
                is_public = f"Private ({vis})" if vis else "Private"

            c_body = c.get("commentBody") or c.get("body") or c.get("text") or ""
            header_str = f"--- {c_date} by {c_by} ({is_public}) " + ("-" * 30)
            lines.append(header_str)
            lines.append(c_body)
            lines.append("")

        output = "\n".join(lines)
        if args.no_pager:
            print(output)
        else:
            pydoc.pager(output)

    else:
        # Rich modern layout using Panels, Markdown syntax highlighting, and Grid tables
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.markdown import Markdown
        from rich.text import Text

        console = Console()

        def render_rich(con):
            # Header panel
            header_text = Text()
            header_text.append(f"CASE: {num}\n", style="bold cyan")
            header_text.append(f"TITLE: {title}\n", style="bold white")
            header_text.append(
                f"URL: https://access.redhat.com/support/cases/{num}",
                style="dim underline",
            )
            con.print(Panel(header_text, border_style="cyan"))

            # Metadata layout
            meta_table = Table.grid(padding=(0, 2))
            meta_table.add_column(style="bold yellow", width=14)
            meta_table.add_column(style="white")
            meta_table.add_column(style="bold yellow", width=14)
            meta_table.add_column(style="white")

            meta_table.add_row("Product:", f"{product} {version}", "Status:", status)
            meta_table.add_row("Severity:", severity, "Type:", case_type)
            meta_table.add_row(
                "Account:",
                str(account_str),
                "Created By:",
                f"{created_by} on {created_date}",
            )
            meta_table.add_row("Assignee:", owner, "", "")
            con.print(Panel(meta_table, title="Details", border_style="yellow"))

            # Description rendered with Markdown parser
            con.print("\n[bold magenta]DESCRIPTION:[/]\n")
            con.print(Markdown(description))
            con.print()

            # Comments with dynamic colored panels & visibility
            con.print(f"\n[bold cyan]COMMENTS ({len(comments)}):[/]\n")

            for c in comments:
                c_date = c.get("createdDate", "")
                c_by = c.get("createdBy", "Unknown")

                is_public = "Public"
                border_style = "green"
                if c.get("isPublic") is False:
                    vis = c.get("visibility")
                    is_public = f"Private ({vis})" if vis else "Private"
                    border_style = "red"

                c_body = c.get("commentBody") or c.get("body") or c.get("text") or ""
                comment_header = f"{c_by} ({is_public}) - {c_date}"
                con.print(
                    Panel(
                        Markdown(c_body),
                        title=comment_header,
                        border_style=border_style,
                    )
                )
                con.print()

        if args.no_pager:
            render_rich(console)
        else:
            with console.pager(styles=True):
                render_rich(console)
