import shutil
import sys
import pydoc
from rh_support_lib.constants import API_URL, COLORS
from rh_support_lib.api import get_json
from rh_support_lib.utils import colorize, get_severity_color, get_status_color


def cmd_show(args, token):
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

    # Dynamic width
    try:
        term_width = shutil.get_terminal_size((80, 20)).columns
    except Exception:
        term_width = 80

    sep_line = "-" * term_width
    double_sep_line = "=" * term_width

    # 4. Format Output
    c_stat = get_status_color(status)
    c_sev = get_severity_color(severity)

    lines = []
    lines.append(f"CASE: {num}")
    lines.append(f"TITLE: {colorize(title, COLORS.BOLD, args.simple_output)}")
    lines.append(f"URL: https://access.redhat.com/support/cases/{num}")
    lines.append(colorize(sep_line, COLORS.BOLD, args.simple_output))
    lines.append(f"Product:      {product} {version}")
    lines.append(f"Account:      {account_str}")
    lines.append(f"Created:      {created_date} by {created_by}")
    lines.append(f"Assignee:     {owner}")
    lines.append(f"Status:       {colorize(status, c_stat, args.simple_output)}")
    lines.append(f"Type:         {case_type}")
    lines.append(f"Severity:     {colorize(severity, c_sev, args.simple_output)}")
    lines.append(colorize(sep_line, COLORS.BOLD, args.simple_output))
    lines.append("DESCRIPTION:\n")
    lines.append(description)
    lines.append(
        "\n" + colorize(double_sep_line, COLORS.BOLD, args.simple_output) + "\n"
    )
    lines.append(f"COMMENTS ({len(comments)}):\n")

    for c in comments:
        c_date = c.get("createdDate", "")
        c_by = c.get("createdBy", "Unknown")

        # Determine visibility
        is_public = "Public"
        header_color = COLORS.BOLD
        if c.get("isPublic") is False:
            vis = c.get("visibility")
            is_public = f"Private ({vis})" if vis else "Private"
            header_color = COLORS.RED + COLORS.BOLD

        c_body = c.get("commentBody") or c.get("body") or c.get("text") or ""

        # Extended Header
        base_header = f"--- {c_date} by {c_by} ({is_public}) "
        # Pad with dashes
        remaining = term_width - len(base_header)
        if remaining > 0:
            header_str = base_header + ("-" * remaining)
        else:
            header_str = base_header

        lines.append(colorize(header_str, header_color, args.simple_output))
        lines.append(c_body)
        lines.append("")

    output = "\n".join(lines)

    if args.no_pager:
        print(output)
    else:
        pydoc.pager(output)
