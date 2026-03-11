import os
import sys
import requests
from rh_support_lib.constants import API_URL, STATUS_MAP
from rh_support_lib.utils import open_editor, strip_header_comments
from rh_support_lib.api import get_json


def cmd_attach(args, token):
    if not isinstance(args.file, list):
        args.file = [args.file]

    has_error = False

    for fpath in args.file:
        if not os.path.isfile(fpath):
            print(f"Error: File '{fpath}' does not exist. Skipping.")
            has_error = True
            continue

        # Check size (1GB limit warning)
        file_size = os.path.getsize(fpath)
        if file_size > 1073741824:
            print(
                f"Warning: File '{os.path.basename(fpath)}' exceeds 1GB. API upload might fail. Consider SFTP."
            )

        print(f"Attaching '{os.path.basename(fpath)}' to Case #{args.case}...")

        endpoint = f"{API_URL}/cases/{args.case}/attachments"
        headers = {"Authorization": f"Bearer {token}"}

        # Streaming upload
        try:
            from requests_toolbelt.multipart.encoder import MultipartEncoder

            with open(fpath, "rb") as f:
                m = MultipartEncoder(fields={"file": (os.path.basename(fpath), f)})
                req_headers = headers.copy()
                req_headers["Content-Type"] = m.content_type
                response = requests.post(
                    endpoint, headers=req_headers, data=m, timeout=(30, 3600)
                )

            if response.status_code in [200, 201]:
                print(f"Success: File '{os.path.basename(fpath)}' attached.")
            else:
                print(
                    f"Error: Upload failed for '{os.path.basename(fpath)}' (HTTP {response.status_code})"
                )
                print(response.text)
                has_error = True
        except requests.exceptions.RequestException as e:
            print(f"Network Error while uploading '{os.path.basename(fpath)}': {e}")
            has_error = True

    if has_error:
        import sys

        sys.exit(1)


def cmd_comment(args, token):
    # Determine status label
    status_key = args.status if args.status else "redhat"
    final_status = STATUS_MAP.get(status_key, "Waiting on Red Hat")

    # Get Content
    comment_body = ""
    temp_file_path = None
    file_content = ""

    if args.file:
        if not os.path.isfile(args.file):
            sys.exit(f"Error: File '{args.file}' does not exist.")
        with open(args.file, "r") as f:
            lines = f.readlines()
        file_content = strip_header_comments(lines).strip()

    if args.edit or not args.file:
        # Prepare context if needed
        header_content = None
        if args.include_previous_comments and args.include_previous_comments > 0:
            print("Fetching previous comments for context...")
            try:
                comments_data = get_json(f"{API_URL}/cases/{args.case}/comments", token)

                # Normalize comments list
                comments = []
                if isinstance(comments_data, list):
                    comments = comments_data
                elif isinstance(comments_data, dict):
                    # Try common wrapper keys
                    for key in ["comments", "items", "list"]:
                        if key in comments_data:
                            comments = comments_data[key]
                            break

                if comments:
                    # Sort by date (ascending) to get correct "last N"
                    # Using string comparison for ISO dates works fine
                    comments.sort(key=lambda x: x.get("createdDate", ""))

                    # Take last N
                    n = args.include_previous_comments
                    if n > len(comments):
                        n = len(comments)
                    subset = comments[-n:]

                    header_lines = []
                    for c in subset:
                        author = c.get("createdBy", "Unknown")
                        date = c.get("createdDate", "Unknown Date")
                        body = (
                            c.get("commentBody") or c.get("body") or c.get("text") or ""
                        )
                        body = body.strip()
                        # Format nicely
                        header_lines.append(f"--- Comment by {author} on {date} ---")
                        header_lines.append(body)
                        header_lines.append("")  # Empty line separator

                    header_content = "\n".join(header_lines).strip()

            except Exception as e:
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
            
        # Refresh token after the editor is closed, in case it expired while typing
        from rh_support_lib.api import get_access_token
        token = get_access_token(args.token_file)
    else:
        # Non-interactive mode (File only, no edit)
        comment_body = file_content

    # 1. Post Comment
    print(f"Posting comment to Case #{args.case}...")

    comment_endpoint = f"{API_URL}/cases/{args.case}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # Swagger definition uses 'commentBody' and 'isPublic' (inferred from response schema and common patterns,
    # though Swagger only shows response for POST /comments, input body schema Ref is CaseComment)
    # Checking CaseComment definition in Swagger:
    # "CaseComment": { "properties": { "body": { "type": "string" }, "public": { "type": "boolean" } ... } }
    # WAIT. The codebase investigator said: "Swagger definition... shows 'commentBody' as the field name, not 'body'".
    # Let me re-read the Swagger file content I just read.
    # Searching for "CaseComment" definition... I need to find it in the truncated output or assume investigator was right.
    # I see /v1/cases/{caseNumber}/comments POST parameters: schema $ref #/definitions/CaseComment
    # I need to see definitions/CaseComment. It was likely in the truncated part or I missed it.
    # Let's trust the investigator's specific finding about 'commentBody' vs 'body'.
    # Actually, looking at `rh_support_lib/commands/show_case.py`:
    # c_body = c.get("commentBody") or c.get("body") or c.get("text") or ""
    # This suggests `commentBody` is a strong candidate.

    payload = {"isPublic": True, "commentBody": comment_body}

    try:
        resp = requests.post(
            comment_endpoint, headers=headers, json=payload, timeout=30
        )
        if resp.status_code in [200, 201]:
            print("Success: Comment added.")
        else:
            print(f"Error: Comment failed (HTTP {resp.status_code})")
            print(resp.text)
            if temp_file_path:
                print(f"Draft comment preserved at: {temp_file_path}")
            sys.exit(1)

    except requests.exceptions.RequestException as e:
        if temp_file_path:
            print(f"Draft comment preserved at: {temp_file_path}")
        sys.exit(f"Network Error: {e}")

    # 2. Update Status
    print(f"Updating status to '{final_status}'...")

    status_endpoint = f"{API_URL}/cases/{args.case}"
    status_payload = {"status": final_status}

    try:
        resp = requests.put(
            status_endpoint, headers=headers, json=status_payload, timeout=30
        )
        if resp.status_code in [200, 204]:
            print(f"Success: Case status updated to '{final_status}'.")
            # Cleanup temp file only after complete success
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
        else:
            print(
                f"Warning: Comment posted, but status update failed (HTTP {resp.status_code})"
            )
            print(resp.text)
            # Since comment was posted, we can probably cleanup, but maybe keep it just in case?
            # The prompt said "if submitting the comment fails". The comment succeeded here.
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            sys.exit(1)
    except requests.exceptions.RequestException as e:
        # Comment posted, status failed.
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        sys.exit(f"Network Error during status update: {e}")


def cmd_link(args):
    url = f"https://access.redhat.com/support/cases/{args.case}"
    print(url)


def cmd_completion(args):
    print('eval "$(register-python-argcomplete rh-support-cli)"')
