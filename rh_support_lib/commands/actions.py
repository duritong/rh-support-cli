import os
import sys
from contextlib import ExitStack, redirect_stdout

from rh_support_lib.api import AuthenticationError
from rh_support_lib.constants import STATUS_MAP
from rh_support_lib.errors import CommentOutcomeUnknownError, PartialCommentError
from rh_support_lib.services.cases import CaseService
from rh_support_lib.statuses import STATUS_IN_PROGRESS, is_waiting_on_customer
from rh_support_lib.utils import (
    EditorError,
    draft_body,
    draft_identity,
    draft_lock,
    format_structured_output,
    open_editor,
    read_draft,
    remove_draft,
)


def cmd_attach(args, api_client):
    from rh_support_lib.commands.attachments import cmd_attachment_upload

    cmd_attachment_upload(args, api_client)


def cmd_download(args, api_client):
    from rh_support_lib.commands.attachments import cmd_attachment_download

    cmd_attachment_download(args, api_client)


def cmd_comment(args, api_client, config=None):
    output = sys.stdout
    with redirect_stdout(sys.stderr), ExitStack() as draft_stack:
        return _cmd_comment(args, api_client, config, output, draft_stack)


def _cmd_comment(args, api_client, config, output, draft_stack):

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

        templates_dir = resolve_templates_dir(config)
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

    case_service = CaseService(api_client, config=config)

    # Determine status label
    no_status_change = getattr(args, "no_status_change", False) or getattr(
        args, "keep_status", False
    )
    if no_status_change:
        final_status = None
    elif getattr(args, "status", None):
        status_key = args.status
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
    elif template_status:
        status_key = template_status
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
    else:
        # Default behavior: status only switches if the case status is waiting on customer
        current_status = case_service.get_case_status(args.case)
        if is_waiting_on_customer(current_status):
            final_status = STATUS_IN_PROGRESS
        else:
            final_status = None
    # Get Content
    comment_body = ""
    temp_file_path = None
    approval_info = {}
    resumed_draft = None
    file_content = ""

    if template_content:
        file_content = template_content
    elif args.file:
        if not os.path.isfile(args.file):
            sys.exit(f"Error: File '{args.file}' does not exist.")
        try:
            from rh_support_lib.config import resolve_drafts_dir

            if (
                os.path.realpath(os.path.dirname(os.path.abspath(args.file)))
                == os.path.abspath(resolve_drafts_dir(config))
                and os.path.basename(args.file).startswith("comment_draft_")
                and not draft_stack.enter_context(draft_lock(args.file))
            ):
                sys.exit("Error: Comment file is active in another process.")
            with open(args.file, "r") as f:
                file_content = draft_body(f.read()).strip()
        except (OSError, UnicodeError):
            sys.exit("Error: Unable to read comment file. No changes submitted.")
        if not file_content:
            sys.exit("Error: Empty comment file. No changes submitted.")

    if args.edit or (not args.file and not getattr(args, "template", None)):
        # Prepare context if needed
        header_content = None
        if args.include_previous_comments and args.include_previous_comments > 0:
            print("Fetching previous comments for context...")
            try:
                details = case_service.get_case_details(
                    args.case,
                )
                comments = details.comments if details else []

                if comments:
                    # The service returns comments in ascending server date order.
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

            except AuthenticationError:
                raise
            except Exception:  # noqa: BLE001
                print(
                    "Warning: Previous comments are unavailable; editor context omitted.",
                    file=sys.stderr,
                )

        # Check for existing draft to resume if initial_body is empty
        if not file_content:
            from rh_support_lib.commands.drafts import find_drafts
            from rh_support_lib.config import resolve_drafts_dir

            existing_drafts = find_drafts(
                case_number=args.case, drafts_dir=resolve_drafts_dir(config)
            )
            if existing_drafts:
                latest_draft = existing_drafts[0]
                try:
                    if not draft_stack.enter_context(draft_lock(latest_draft["path"])):
                        sys.exit("Error: Saved draft is active in another process.")
                    _, info = read_draft(latest_draft["path"])
                    if draft_identity(info) != tuple(latest_draft["identity"]):
                        sys.exit(
                            "Error: Saved draft changed; retry to resume it safely."
                        )
                except (OSError, UnicodeError):
                    sys.exit("Error: Saved draft is unavailable; no changes submitted.")
                draft_content = latest_draft.get("content", "").strip()
                if draft_content:
                    extracted = draft_body(draft_content).strip()
                    if extracted:
                        print(
                            f"Resuming saved draft for Case #{args.case} ({latest_draft['modified']})..."
                        )
                        file_content = extracted
                        resumed_draft = latest_draft

        # Open Editor (using file_content as initial body if provided)
        try:
            comment_body, temp_file_path = open_editor(
                args.case,
                final_status,
                header_content=header_content,
                initial_body=file_content,
                config=config,
                draft_stack=draft_stack,
                approval_info=approval_info,
            )
        except EditorError as error:
            sys.exit(f"Editor Error: {error}")
    else:
        # Non-interactive mode (File only, no edit)
        comment_body = file_content

    if not comment_body.strip():
        if temp_file_path:
            print(f"Draft comment preserved at: {temp_file_path}", file=sys.stderr)
        sys.exit("Error: Empty comment. No comment or status change submitted.")

    # 1. Post Comment and update status via CaseService
    submitted_identity = approval_info.get("identity")
    print(f"Posting comment to Case #{args.case}...")
    partial_error = None
    try:
        fmt = getattr(args, "format", "markdown") or "markdown"
        comment = case_service.add_comment(
            args.case,
            comment_body,
            is_public=True,
            status=final_status if final_status else None,
            body_format=fmt,
        )
    except PartialCommentError as error:
        comment = error.comment
        partial_error = error
    except CommentOutcomeUnknownError as error:
        if temp_file_path:
            print(f"Comment recovery copy preserved at: {temp_file_path}")
        sys.exit(str(error))
    except AuthenticationError:
        if temp_file_path:
            print(f"Draft comment preserved at: {temp_file_path}")
        raise
    except Exception:  # noqa: BLE001
        if temp_file_path:
            print(f"Comment recovery copy preserved at: {temp_file_path}")
        sys.exit(
            "Comment submission was not confirmed. Check the case in the portal "
            "before retrying; do not blindly repost the comment."
        )

    if (
        temp_file_path
        and os.path.exists(temp_file_path)
        and (
            submitted_identity is None
            or not remove_draft(temp_file_path, submitted_identity)
        )
    ):
        print(f"Submitted comment copy remains at: {temp_file_path}")
    if resumed_draft and not remove_draft(
        resumed_draft["path"], resumed_draft["identity"]
    ):
        print(f"Submitted source copy changed or remains at: {resumed_draft['path']}")
    status_confirmed = (
        partial_error.status_confirmed if partial_error else bool(final_status)
    )
    receipt = {
        "case_number": str(args.case),
        "comment_id": comment.id,
        "comment_posted": True,
        "requested_status": final_status,
        "status_confirmed": status_confirmed,
        "success": partial_error is None,
    }
    output_format = (
        "json" if getattr(args, "json", False) else getattr(args, "output_format", None)
    )
    if output_format in ("json", "yaml"):
        print(format_structured_output(receipt, fmt=output_format), file=output)
    else:
        print("Success: Comment added.", file=output)
        if final_status and status_confirmed:
            print(f"Success: Case status updated to '{final_status}'.", file=output)
    if partial_error:
        print(str(partial_error), file=sys.stderr)
        sys.exit(1)


def cmd_close(args, api_client, config=None):
    """Closes a support case with a comment, explicitly setting status to 'closed'."""
    args.status = "closed"
    return cmd_comment(args, api_client, config=config)


def cmd_link(args):
    url = f"https://access.redhat.com/support/cases/{args.case}"
    print(url)


def cmd_completion(args):
    print('eval "$(register-python-argcomplete rh-support-cli)"')
