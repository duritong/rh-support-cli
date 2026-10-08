import os
import pydoc
import sys

from rh_support_lib.services.cases import CaseService


def cmd_show(args, api_client):
    fmt = getattr(args, "format", "text")
    is_structured = fmt in ("json", "yaml") or getattr(args, "json", False)
    if not is_structured:
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

    from rh_support_lib.services.attachments import AttachmentService

    try:
        attachments = AttachmentService(api_client).list_attachments(str(num))
    except Exception:  # noqa: BLE001
        attachments = []
    user = service.get_current_user()
    tc = service.state_service.get_tracked_cases(user).get(str(num))
    if not tc and (
        (case.contact_sso and case.contact_sso.lower() == user.lower())
        or (case.owner and case.owner.lower() == user.lower())
    ):
        service.state_service.register_owned_case(
            user=user,
            case_number=str(num),
            summary=title,
            account_number=account_str,
            status=status,
            severity=severity,
            last_comment_id=comments[-1].id if comments else "",
            last_comment_date=comments[-1].created_date if comments else "",
            last_modified_date=case.last_modified_date,
        )
        tc = service.state_service.get_tracked_cases(user).get(str(num))

    if is_structured:
        from rh_support_lib.utils import format_structured_output

        data = {
            "case": case,
            "description": description,
            "comments": comments,
            "attachments": attachments,
        }
        print(format_structured_output(data, fmt="yaml" if fmt == "yaml" else "json"))
        if getattr(args, "mark_read", False):
            service.state_service.mark_case_read(user, str(num))
        return
    status_changed = False
    old_status = ""
    last_read_date = ""
    last_read_id = ""
    if tc and tc.read_checkpoint:
        old_status = tc.read_checkpoint.status
        if old_status and status and old_status != status:
            status_changed = True
        last_read_date = tc.read_checkpoint.last_read_comment_date or ""
        last_read_id = tc.read_checkpoint.last_read_comment_id or ""

    unread_indices = set()
    if last_read_date or last_read_id:
        for idx, c in enumerate(comments):
            if (
                last_read_date
                and c.created_date > last_read_date
                or not last_read_date
                and last_read_id
                and c.id != last_read_id
            ):
                unread_indices.add(idx)
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
        if status_changed:
            lines.append(f"⚡ Status Changed: {old_status} -> {status}")
            lines.append("-" * 80)
        if attachments:
            lines.append(f"ATTACHMENTS ({len(attachments)}):\n")
            lines.append(
                f"{'UUID':<36}  {'FILE NAME':<30}  {'SIZE':<10}  {'UPLOADED BY':<20}  {'STATUS'}"
            )
            lines.append("-" * 110)
            for a in attachments:
                size_str = (
                    f"{a.size_kb:.1f} KB"
                    if a.size_kb < 1024
                    else f"{a.size_kb / 1024.0:.1f} MB"
                )
                lines.append(
                    f"{a.uuid:<36}  {a.file_name:<30}  {size_str:<10}  {a.created_by or 'Unknown':<20}  {a.upload_status}"
                )
            lines.append("\n" + "-" * 80)
        lines.append("DESCRIPTION:\n")
        lines.append(description)
        lines.append("\n" + "=" * 80 + "\n")
        lines.append(f"COMMENTS ({len(comments)}):\n")
        checkpoint_shown_simple = False
        for idx, c in enumerate(comments):
            if idx in unread_indices and not checkpoint_shown_simple:
                checkpoint_shown_simple = True
                lines.append("┈┈┈┈ 📍 Last read checkpoint │ Unread below ┈┈┈┈\n")
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
        from rich.box import ROUNDED
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
            if status_changed:
                diff_banner = f"[bold yellow]⚡ Status Changed:[/] {old_status} ➔ [bold green]{status}[/]"
                con.print(Panel(diff_banner, border_style="yellow"))

            if attachments:
                con.print(f"\n[bold yellow]ATTACHMENTS ({len(attachments)}):[/]\n")
                att_table = Table(
                    box=ROUNDED, header_style="bold yellow", show_lines=False
                )
                att_table.add_column("UUID", style="dim", no_wrap=True)
                att_table.add_column("Filename", style="bold white")
                att_table.add_column("Size", justify="right", style="green")
                att_table.add_column("Uploaded By", style="yellow")
                att_table.add_column("Status", style="cyan")
                for a in attachments:
                    size_str = (
                        f"{a.size_kb:.1f} KB"
                        if a.size_kb < 1024
                        else f"{a.size_kb / 1024.0:.1f} MB"
                    )
                    att_table.add_row(
                        a.uuid,
                        a.file_name,
                        size_str,
                        a.created_by or "Unknown",
                        a.upload_status,
                    )
                con.print(att_table)

            con.print("\n[bold magenta]DESCRIPTION:[/]\n")
            con.print(Markdown(description))
            con.print()

            con.print(f"\n[bold cyan]COMMENTS ({len(comments)}):[/]\n")
            checkpoint_shown_rich = False
            for idx, c in enumerate(comments):
                is_unread = idx in unread_indices
                if is_unread and not checkpoint_shown_rich:
                    checkpoint_shown_rich = True
                    count_unread = len(unread_indices)
                    ckpt_time = (
                        tc.read_checkpoint.marked_read_at
                        or last_read_date
                        or "previous"
                    )
                    con.print(
                        f"[bold yellow]┈┈┈┈ 📍 Last read checkpoint ({ckpt_time}) │ ▼ {count_unread} unread below ┈┈┈┈[/]\n"
                    )
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
                border_style = (
                    "yellow" if is_unread else ("green" if c.is_public else "red")
                )
                badge = " [bold yellow][NEW][/]" if is_unread else ""
                c_body = c.body or ""
                comment_header = f"{c_by} ({is_public}){badge} - {c_date}"
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

    if getattr(args, "mark_read", False):
        service.state_service.mark_case_read(user, str(num))
        print(f"✓ Marked Case #{num} as read.")
