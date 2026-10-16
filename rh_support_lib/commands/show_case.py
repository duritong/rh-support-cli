from __future__ import annotations

import os
import pydoc
import sys

from rh_support_lib.api import AuthenticationError
from rh_support_lib.errors import ReadError
from rh_support_lib.services.cases import CaseService
from rh_support_lib.utils import is_comment_newer


def format_incident_export(
    case, description, comments, attachments, fmt="markdown"
) -> str:
    """Formats incident shift-handoff summary for Slack, Markdown, or JSON."""
    fmt = (fmt or "markdown").lower().strip()
    num = case.case_number or case.id
    title = case.summary or "Untitled Incident"
    status = case.status or "Unknown"
    severity = case.severity or "Unknown"
    product = f"{case.product or ''} {case.version or ''}".strip() or "Unspecified"
    account = str(case.account_number or "")
    portal_url = f"https://access.redhat.com/support/cases/{num}"

    # Find latest Red Hat update
    rh_comments = [
        c
        for c in comments
        if getattr(c, "is_associate", False) or not getattr(c, "is_customer", True)
    ]
    latest_rh = rh_comments[-1] if rh_comments else (comments[-1] if comments else None)

    latest_rh_date = latest_rh.created_date if latest_rh else ""
    latest_rh_by = (latest_rh.created_by or "Red Hat Support") if latest_rh else "None"
    latest_rh_text = ""
    if latest_rh and latest_rh.body:
        snippet = latest_rh.body.strip().splitlines()[0]
        latest_rh_text = snippet[:200] + ("..." if len(snippet) > 200 else "")

    # Pending action
    workflow_status = str(status).strip().casefold()
    if workflow_status == "closed":
        next_step = "Case is closed; review the resolution before reopening."
    elif workflow_status in (
        "waiting on customer",
        "waiting on customer action required",
    ):
        next_step = "Customer action required (check latest Red Hat request)."
    elif workflow_status in (
        "waiting on red hat",
        "waiting on engineering",
        "in progress",
    ):
        next_step = "Waiting on Red Hat support / engineering investigation."
    else:
        next_step = "Next action is unknown; review the case history."

    # Attachments summary
    att_summary = f"{len(attachments)} file(s)"
    if attachments:
        names = ", ".join(a.file_name for a in attachments[:3])
        if len(attachments) > 3:
            names += f" (+{len(attachments) - 3} more)"
        att_summary += f" ({names})"

    if fmt == "json":
        import json

        return json.dumps(
            {
                "case_number": str(num),
                "portal_url": portal_url,
                "title": title,
                "status": status,
                "severity": severity,
                "product": product,
                "account": account,
                "latest_update": {
                    "author": latest_rh_by,
                    "date": latest_rh_date,
                    "snippet": latest_rh_text,
                },
                "next_step": next_step,
                "attachments": [
                    {"uuid": a.uuid, "name": a.file_name, "size_kb": a.size_kb}
                    for a in attachments
                ],
            },
            indent=2,
        )

    if fmt == "slack":
        lines = [
            f"*<{portal_url}|Red Hat Case #{num}>* — *{title}*",
            f"• *Status*: {status} | *Severity*: {severity}",
            f"• *Product*: {product}" + (f" (Account: {account})" if account else ""),
        ]
        if latest_rh:
            lines.append(f"• *Latest Update* ({latest_rh_by} - {latest_rh_date}):")
            lines.append(f"> {latest_rh_text}")
        lines.append(f"• *Next Step*: {next_step}")
        lines.append(f"• *Attachments*: {att_summary}")
        return "\n".join(lines)

    # Default: markdown
    lines = [
        f"### Red Hat Case #{num}: {title}",
        f"**Portal Link**: {portal_url}",
        f"* **Status**: {status} | **Severity**: {severity}",
        f"* **Product**: {product}" + (f" (Account: {account})" if account else ""),
    ]
    if latest_rh:
        lines.append(f"* **Latest Update** ({latest_rh_by} on {latest_rh_date}):")
        lines.append(f"> {latest_rh_text}")
    lines.append(f"* **Next Step**: {next_step}")
    lines.append(f"* **Attachments**: {att_summary}")
    return "\n".join(lines)


def cmd_show(args, api_client, config: dict | None = None):
    fmt = getattr(args, "format", "text")
    export_fmt = getattr(args, "export", None)
    is_structured = (
        fmt in ("json", "yaml") or getattr(args, "json", False) or export_fmt == "json"
    )
    if not is_structured and not export_fmt:
        print(f"Fetching case {args.case}...")
    service = CaseService(api_client, config=config)
    try:
        details = service.get_case_details(args.case)
    except AuthenticationError:
        raise
    except ReadError as error:
        sys.exit(f"Error reading case {args.case}: {error}")
    except Exception:  # noqa: BLE001 - presentation boundary, hide raw transport details
        sys.exit(f"Error: Case {args.case} is unavailable; retrieval failed.")

    if not details or not details.case:
        sys.exit(f"Error: Case {args.case} not found.")

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
    except AuthenticationError:
        raise
    except Exception:  # noqa: BLE001 - never turn an unavailable list into empty success
        sys.exit(
            f"Error: Attachments for case {num} are unavailable; no complete case view returned."
        )
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

    export_fmt = getattr(args, "export", None)
    if export_fmt:
        output = format_incident_export(
            case, description, comments, attachments, fmt=export_fmt
        )
        print(output)
        if getattr(args, "mark_read", False):
            service.state_service.mark_case_read(user, str(num))
        return

    # Determine comment display order (defaults to 'newest' matching portal default)
    order = "newest"
    if getattr(args, "oldest_first", False):
        order = "oldest"
    elif getattr(args, "newest_first", False):
        order = "newest"
    elif getattr(args, "order", None):
        order = (
            "newest"
            if getattr(args, "order", "").lower() in ("newest", "desc")
            else "oldest"
        )
    elif config and (config.get("comments_order") or config.get("comment_order")):
        cfg_order = (
            str(config.get("comments_order") or config.get("comment_order"))
            .lower()
            .strip()
        )
        order = "newest" if cfg_order in ("newest", "desc") else "oldest"
    status_changed = False
    old_status = ""
    last_read_date = ""
    last_read_id = ""
    if tc and tc.read_checkpoint:
        old_status = tc.read_checkpoint.status
        if old_status and status and old_status != status:
            status_changed = True
        last_read_date = (
            tc.read_checkpoint.last_read_comment_date
            or tc.read_checkpoint.marked_read_at
            or ""
        )
        last_read_id = tc.read_checkpoint.last_read_comment_id or ""

    # Annotate unread status on comments
    unread_count = 0
    if tc and tc.read_checkpoint:
        for c in comments:
            c.is_unread = is_comment_newer(
                created_date=c.created_date,
                comment_id=c.id,
                checkpoint_date=last_read_date,
                checkpoint_id=last_read_id,
            )
            if c.is_unread:
                unread_count += 1
    else:
        for c in comments:
            c.is_unread = False

    display_comments = list(reversed(comments)) if order == "newest" else list(comments)

    if is_structured:
        from rh_support_lib.utils import format_structured_output

        data = {
            "case": case,
            "description": description,
            "comments": display_comments,
            "attachments": attachments,
            "order": order,
        }
        print(format_structured_output(data, fmt="yaml" if fmt == "yaml" else "json"))
        if getattr(args, "mark_read", False):
            service.state_service.mark_case_read(user, str(num))
        return
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
        order_label = "newest first" if order == "newest" else "oldest first"
        lines.append(f"COMMENTS ({len(display_comments)}) [{order_label}]:\n")
        checkpoint_shown_simple = False
        for c in display_comments:
            if order == "oldest" and c.is_unread and not checkpoint_shown_simple:
                checkpoint_shown_simple = True
                lines.append(
                    f"┈┈┈┈ 📍 Last read checkpoint │ ▼ {unread_count} unread below ┈┈┈┈\n"
                )
            elif (
                order == "newest"
                and not c.is_unread
                and not checkpoint_shown_simple
                and unread_count > 0
            ):
                checkpoint_shown_simple = True
                lines.append(
                    f"┈┈┈┈ 📍 Last read checkpoint │ ▲ {unread_count} unread above ┈┈┈┈\n"
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
            c_body = c.body or ""
            header_str = f"--- {c_date} by {c_by} ({is_public}) " + ("-" * 30)
            lines.append(header_str)
            lines.append(c_body)
            lines.append("")

        if order == "newest" and unread_count > 0 and not checkpoint_shown_simple:
            lines.append(
                f"┈┈┈┈ 📍 Last read checkpoint │ ▲ {unread_count} unread above ┈┈┈┈\n"
            )

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

            order_label = "newest first" if order == "newest" else "oldest first"
            con.print(
                f"\n[bold cyan]COMMENTS ({len(display_comments)})[/] [dim]({order_label})[/]:\n"
            )
            checkpoint_shown_rich = False
            ckpt_time = (
                (tc.read_checkpoint.marked_read_at or last_read_date or "previous")
                if tc and tc.read_checkpoint
                else "previous"
            )

            for c in display_comments:
                if order == "oldest" and c.is_unread and not checkpoint_shown_rich:
                    checkpoint_shown_rich = True
                    con.print(
                        f"[bold yellow]┈┈┈┈ 📍 Last read checkpoint ({ckpt_time}) │ ▼ {unread_count} unread below ┈┈┈┈[/]\n"
                    )
                elif (
                    order == "newest"
                    and not c.is_unread
                    and not checkpoint_shown_rich
                    and unread_count > 0
                ):
                    checkpoint_shown_rich = True
                    con.print(
                        f"[bold yellow]┈┈┈┈ 📍 Last read checkpoint ({ckpt_time}) │ ▲ {unread_count} unread above ┈┈┈┈[/]\n"
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
                    "yellow" if c.is_unread else ("green" if c.is_public else "red")
                )
                badge = " [bold yellow][NEW][/]" if c.is_unread else ""
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

            if order == "newest" and unread_count > 0 and not checkpoint_shown_rich:
                con.print(
                    f"[bold yellow]┈┈┈┈ 📍 Last read checkpoint ({ckpt_time}) │ ▲ {unread_count} unread above ┈┈┈┈[/]\n"
                )

        if args.no_pager:
            render_rich(console)
        else:
            with console.pager(styles=True):
                render_rich(console)

    if getattr(args, "mark_read", False):
        service.state_service.mark_case_read(user, str(num))
        print(f"✓ Marked Case #{num} as read.")
