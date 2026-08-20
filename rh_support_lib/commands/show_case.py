import os
import pydoc
import sys

from rh_support_lib.services.cases import CaseService


def cmd_show(args, api_client):
    print(f"Fetching case {args.case}...")
    service = CaseService(api_client)
    try:
        details = service.get_case_details(args.case)
    except Exception:  # noqa: BLE001
        details = None

    if not details or not details.case:
        sys.exit(f"Error: Case {args.case} not found or invalid response.")

    case = details.case
    num = case.case_number or case.id
    title = case.summary
    product = case.product
    version = case.version
    account_str = str(case.account_number)
    created_by = case.created_by or "Unknown"
    created_date = case.created_date or ""
    owner = case.owner or "Unknown"
    status = case.status
    case_type = case.case_type
    severity = case.severity
    description = details.description
    comments = details.comments

    if args.simple_output:
        lines = []
        lines.append(f"CASE: {num}")
        lines.append(f"TITLE: {title}")
        lines.append(f"URL: https://access.redhat.com/support/cases/{num}")
        lines.append("-" * 80)
        lines.append(f"Product:      {product} {version}".strip())
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
            c_date = c.created_date or ""
            c_by = c.created_by or "Unknown"
            is_public = (
                "Public"
                if c.is_public
                else (
                    f"Private ({c.visibility})"
                    if c.visibility and c.visibility != "Private"
                    else "Private"
                )
            )
            c_body = c.body or ""
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
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        console = Console(
            color_system="standard" if os.environ.get("FORCE_COLOR") == "1" else "auto",
            force_terminal=True if os.environ.get("FORCE_COLOR") == "1" else None,
        )

        def render_rich(con):
            header_text = Text()
            header_text.append(f"CASE: {num}\n", style="bold cyan")
            header_text.append(f"TITLE: {title}\n", style="bold white")
            header_text.append(
                f"URL: https://access.redhat.com/support/cases/{num}",
                style="dim underline",
            )
            con.print(Panel(header_text, border_style="cyan"))

            meta_table = Table.grid(padding=(0, 2))
            meta_table.add_column(style="bold yellow", width=14)
            meta_table.add_column(style="white")
            meta_table.add_column(style="bold yellow", width=14)
            meta_table.add_column(style="white")

            meta_table.add_row(
                "Product:", f"{product} {version}".strip(), "Status:", status
            )
            meta_table.add_row("Severity:", severity, "Type:", case_type)
            meta_table.add_row(
                "Account:",
                str(account_str),
                "Created By:",
                f"{created_by} on {created_date}" if created_date else str(created_by),
            )
            meta_table.add_row("Assignee:", owner, "", "")
            con.print(Panel(meta_table, title="Details", border_style="yellow"))

            con.print("\n[bold magenta]DESCRIPTION:[/]\n")
            con.print(Markdown(description))
            con.print()

            con.print(f"\n[bold cyan]COMMENTS ({len(comments)}):[/]\n")

            for c in comments:
                c_date = c.created_date or ""
                c_by = c.created_by or "Unknown"
                is_public = (
                    "Public"
                    if c.is_public
                    else (
                        f"Private ({c.visibility})"
                        if c.visibility and c.visibility != "Private"
                        else "Private"
                    )
                )
                border_style = "green" if c.is_public else "red"
                c_body = c.body or ""
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
