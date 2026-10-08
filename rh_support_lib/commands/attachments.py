from __future__ import annotations

import os
import sys

from rich.box import ROUNDED
from rich.console import Console
from rich.table import Table

from rh_support_lib.services.attachments import AttachmentService


def cmd_attachment(args, api_client):
    """Dispatcher for `attachment` subcommands: list, upload, download, delete, status."""
    subaction = getattr(args, "attachment_action", None)
    if subaction == "list":
        cmd_attachment_list(args, api_client)
    elif subaction in ("upload", "add"):
        cmd_attachment_upload(args, api_client)
    elif subaction in ("download", "get"):
        cmd_attachment_download(args, api_client)
    elif subaction in ("delete", "rm"):
        cmd_attachment_delete(args, api_client)
    elif subaction == "status":
        cmd_attachment_status(args, api_client)
    else:
        case_val = getattr(args, "case", None) or getattr(args, "case_pos", None)
        if case_val:
            args.case = str(case_val)
            cmd_attachment_list(args, api_client)
        else:
            sys.exit(
                "Error: Please specify an attachment action: list, upload, download, delete, status."
            )


def cmd_attachment_list(args, api_client):
    """Lists attachments for a support case."""
    service = AttachmentService(api_client)
    case_num = str(getattr(args, "case", None) or getattr(args, "case_pos", None) or "")
    if not case_num:
        sys.exit("Error: Case number is required (e.g. -c 12345678 or positional).")
    include_deleted = getattr(args, "include_deleted", False)
    simple_output = getattr(args, "simple_output", False)

    try:
        attachments = service.list_attachments(
            case_num, include_deleted=include_deleted
        )
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Error fetching attachments: {e}")

    if not attachments:
        print(f"No attachments found for Case #{case_num}.")
        return

    if simple_output:
        print(f"Attachments for Case #{case_num}:")
        print(
            f"{'UUID':<36}  {'FILE NAME':<30}  {'SIZE (KB)':<10}  {'UPLOADED BY':<20}  {'STATUS'}"
        )
        print("-" * 110)
        for att in attachments:
            print(
                f"{att.uuid:<36}  {att.file_name:<30}  {att.size_kb:<10.1f}  {att.created_by:<20}  {att.upload_status}"
            )
    else:
        console = Console()
        table = Table(
            title=f"Attachments for Case #{case_num}",
            box=ROUNDED,
            header_style="bold cyan",
            show_lines=False,
        )
        table.add_column("UUID", style="dim", no_wrap=True)
        table.add_column("Filename", style="bold white")
        table.add_column("Size", justify="right", style="green")
        table.add_column("Uploaded By", style="yellow")
        table.add_column("Date", style="magenta")
        table.add_column("Status", style="cyan")

        for att in attachments:
            size_str = (
                f"{att.size_kb:.1f} KB"
                if att.size_kb < 1024
                else f"{att.size_kb / 1024.0:.1f} MB"
            )
            table.add_row(
                att.uuid,
                att.file_name,
                size_str,
                att.created_by or "Unknown",
                att.created_date or "-",
                att.upload_status,
            )
        console.print(table)


def cmd_attachment_upload(args, api_client):
    """Uploads one or more files to a support case."""
    service = AttachmentService(api_client)
    case_num = args.case
    files = args.file
    if not isinstance(files, list):
        files = [files]

    description = getattr(args, "description", "") or ""
    has_error = False

    for fpath in files:
        if not os.path.isfile(fpath):
            print(f"Error: File '{fpath}' does not exist. Skipping.")
            has_error = True
            continue

        fname = os.path.basename(fpath)
        print(f"Attaching '{fname}' to Case #{case_num}...")
        try:
            service.upload_attachment(case_num, fpath, description=description)
            print(f"Success: File '{fname}' attached.")
        except Exception as e:  # noqa: BLE001
            print(f"Network Error while uploading '{fname}': {e}")
            has_error = True

    if has_error:
        sys.exit(1)


def cmd_attachment_download(args, api_client):
    """Downloads an attachment from a support case."""
    service = AttachmentService(api_client)
    case_num = args.case
    att_id = args.attachment or getattr(args, "attachment_id", None)
    if not att_id:
        sys.exit("Error: Attachment ID (--attachment / -a) is required.")

    target_path = getattr(args, "output", None)
    print(f"Downloading attachment '{att_id}' from Case #{case_num}...")
    try:
        saved_path = service.download_attachment(
            case_num, att_id, target_path=target_path
        )
        print(f"Successfully downloaded attachment to: {saved_path}")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Error downloading attachment: {e}")


def cmd_attachment_delete(args, api_client):
    """Deletes an attachment from a support case."""
    service = AttachmentService(api_client)
    case_num = args.case
    att_id = args.attachment or getattr(args, "attachment_id", None)
    if not att_id:
        sys.exit("Error: Attachment ID (--attachment / -a) is required.")

    auto_yes = getattr(args, "yes", False)
    if not auto_yes:
        confirm = input(
            f"Are you sure you want to delete attachment '{att_id}' from Case #{case_num}? [y/N]: "
        )
        if confirm.lower() not in ("y", "yes"):
            print("Operation cancelled.")
            return

    print(f"Deleting attachment '{att_id}' from Case #{case_num}...")
    try:
        service.delete_attachment(case_num, att_id)
        print(f"Successfully deleted attachment '{att_id}'.")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Error deleting attachment: {e}")


def cmd_attachment_status(args, api_client):
    """Fetches upload status for attachments on a case."""
    service = AttachmentService(api_client)
    case_num = args.case
    att_id = getattr(args, "attachment", None) or getattr(args, "attachment_id", None)

    try:
        statuses = service.get_attachment_status(case_num, att_id)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Error fetching attachment status: {e}")

    for st in statuses:
        fname = st.get("fileName", "Unknown")
        status_val = st.get("status", "Unknown")
        aid = st.get("attachmentId", "")
        print(f"- {fname} (ID: {aid}): {status_val}")
