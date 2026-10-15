import os
import sys

from rh_support_lib.constants import STATUS_MAP
from rh_support_lib.services.cases import CaseService
from rh_support_lib.utils import open_editor, strip_header_comments


def cmd_attach(args, api_client):
    from rh_support_lib.commands.attachments import cmd_attachment_upload

    cmd_attachment_upload(args, api_client)


def cmd_download(args, api_client):
    from rh_support_lib.commands.attachments import cmd_attachment_download

    cmd_attachment_download(args, api_client)


def cmd_comment(args, api_client):

    # 1. Process templates if specified
    template_content = ""
    template_status = None
    if getattr(args, "template", None):
        import yaml

        from rh_support_lib.templates import TemplateEngine, resolve_templates_dir

        template_vars = {}
        if getattr(args, "template_var", None):
            for tv in args.template_var:
                if "=" in tv:
                    k, v = tv.split("=", 1)
                    try:
                        template_vars[k] = yaml.safe_load(v)
                    except yaml.YAMLError:
                        template_vars[k] = v

        templates_dir = resolve_templates_dir()
        engine = TemplateEngine(templates_dir)
        try:
            template_data = engine.process(args.template, template_vars)
        except ValueError as e:
            sys.exit(f"Template Error: {e}")

        # Look for comment text in standard fields
        template_content = (
            template_data.get("comment")
            or template_data.get("body")
            or template_data.get("commentBody")
            or ""
        )
        template_content = str(template_content).strip()
        template_status = template_data.get("status")
        if not template_content and not args.file and not args.edit:
            sys.exit(
                "Template Error: Rendered comment is empty. No comment or status change submitted."
            )

    # Determine status label
    no_status_change = getattr(args, "no_status_change", False) or getattr(
        args, "keep_status", False
    )
    if no_status_change:
        final_status = None
    else:
        status_key = (
            args.status
            if args.status
            else (template_status if template_status else "redhat")
        )
        if str(status_key).lower() in (
            "none",
            "keep",
            "unchanged",
            "no_change",
            "no-change",
        ):
            final_status = None
        else:
            final_status = STATUS_MAP.get(str(status_key).lower(), status_key)

    # Get Content
    comment_body = ""
    temp_file_path = None
    file_content = ""

    if template_content:
        file_content = template_content
    elif args.file:
        if not os.path.isfile(args.file):
            sys.exit(f"Error: File '{args.file}' does not exist.")
        with open(args.file, "r") as f:
            lines = f.readlines()
        file_content = strip_header_comments(lines).strip()

    case_service = CaseService(api_client)

    if args.edit or (not args.file and not getattr(args, "template", None)):
        # Prepare context if needed
        header_content = None
        if args.include_previous_comments and args.include_previous_comments > 0:
            print("Fetching previous comments for context...")
            try:
                details = case_service.get_case_details(
                    args.case,
                    first_comments=args.include_previous_comments,
                    all_comments=False,
                )
                comments = details.comments if details else []

                if comments:
                    # Sort by date (ascending) to get correct "last N"
                    comments.sort(key=lambda x: x.created_date)
                    n = args.include_previous_comments
                    subset = comments[-n:]

                    header_lines = ["--- PREVIOUS COMMENTS ---"]
                    for c in subset:
                        author = c.created_by or "Unknown"
                        date = c.created_date or "Unknown Date"
                        body = (c.body or "").strip()
                        header_lines.append(f"--- Comment by {author} on {date} ---")
                        header_lines.append(body)
                        header_lines.append("")

                    header_content = "\n".join(header_lines).strip()

            except Exception as e:  # noqa: BLE001
                print(f"Warning: Failed to fetch previous comments: {e}")

        # Open Editor (using file_content as initial body if provided)
        comment_body, temp_file_path = open_editor(
            args.case,
            final_status,
            header_content=header_content,
            initial_body=file_content,
        )
        if not comment_body:
            print("Aborted: Empty comment.")
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            sys.exit(0)
    else:
        # Non-interactive mode (File only, no edit)
        comment_body = file_content

    # 1. Post Comment and update status via CaseService
    print(f"Posting comment to Case #{args.case}...")
    try:
        fmt = getattr(args, "format", "markdown") or "markdown"
        case_service.add_comment(
            args.case,
            comment_body,
            is_public=True,
            status=final_status if final_status else None,
            body_format=fmt,
        )
        print("Success: Comment added.")
        if final_status:
            print(f"Success: Case status updated to '{final_status}'.")
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
    except Exception as e:  # noqa: BLE001
        print(f"Error: Comment failed ({e})")
        if temp_file_path:
            print(f"Draft comment preserved at: {temp_file_path}")
        sys.exit(f"Network Error: {e}")


def cmd_close(args, api_client):
    """Closes a support case with a comment, explicitly setting status to 'closed'."""
    args.status = "closed"
    return cmd_comment(args, api_client)


def cmd_link(args):
    url = f"https://access.redhat.com/support/cases/{args.case}"
    print(url)


def cmd_completion(args):
    print('eval "$(register-python-argcomplete rh-support-cli)"')
