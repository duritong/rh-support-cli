import os
import sys
import json
import requests
import yaml
import tempfile
import subprocess
from rh_support_lib.constants import API_URL
from rh_support_lib.api import get_json
from rh_support_lib.utils import select_from_list, prompt_text, strip_header_comments
from rh_support_lib.templates import TemplateEngine


def cmd_create(args, token, config):
    # Process Templates
    defaults = {}

    templates_to_process = []
    if not getattr(args, "no_default_template", False) and config.get(
        "default_create_template"
    ):
        templates_to_process.append(config.get("default_create_template"))

    if args.template:
        templates_to_process.extend(args.template)

    if templates_to_process:
        template_vars = {}
        if args.template_var:
            for tv in args.template_var:
                if "=" in tv:
                    k, v = tv.split("=", 1)
                    try:
                        template_vars[k] = yaml.safe_load(v)
                    except Exception:
                        template_vars[k] = v

        templates_dir = os.path.expanduser("~/.config/rh-support-cli/templates")
        engine = TemplateEngine(templates_dir)
        defaults = engine.process(templates_to_process, template_vars)

    # 1. Gather Data (Flags or Interactive)

    # Product
    product = args.product or defaults.get("product")
    product_code = None
    if not product:
        print("Fetching products...")
        products = get_json(f"{API_URL.replace('/v1', '/v2')}/products", token)
        selected = select_from_list("Select Product", products)
        if selected:
            product = selected.get("name")
            product_code = product  # v2 uses the name for version lookup
        else:
            product = prompt_text("Product Name")

    # If we have a product code, we can fetch versions, otherwise just ask
    version = args.version or defaults.get("version")
    if not version:
        versions = []
        if product_code:
            import urllib.parse

            print("Fetching versions...")
            encoded_product = urllib.parse.quote(product_code)
            versions = get_json(
                f"{API_URL.replace('/v1', '/v2')}/products/{encoded_product}/versions",
                token,
            )

        if versions:
            selected = select_from_list("Select Version", versions)
            version = (
                selected.get("name") if selected else prompt_text("Product Version")
            )
        else:
            version = prompt_text("Product Version")

    # Severity and Type
    severity = args.severity or defaults.get("severity")
    if not severity:
        print("Fetching severities...")
        sevs = get_json(f"{API_URL}/values/severity", token)
        if sevs:
            selected = select_from_list("Select Severity", sevs)
            severity = selected.get("name") if selected else prompt_text("Severity")
        else:
            severity = prompt_text("Severity", default="Normal")

    case_type = args.type or defaults.get("caseType")
    if not case_type:
        print("Fetching case types...")
        types = get_json(f"{API_URL}/values/caseType", token)
        if types:
            selected = select_from_list("Select Case Type", types)
            case_type = selected.get("name") if selected else prompt_text("Case Type")
        else:
            case_type = prompt_text("Case Type", default="Standard")

    summary = args.summary or defaults.get("summary") or prompt_text("Case Summary")

    # Description
    description = ""
    temp_desc_path = None

    if args.description_file:
        if not os.path.isfile(args.description_file):
            sys.exit(f"Error: Description file '{args.description_file}' not found.")
        with open(args.description_file, "r") as f:
            lines = f.readlines()
        description = strip_header_comments(lines).strip()
    else:
        description = defaults.get("description", "")

    if not description:
        # If not provided, use editor
        editor = os.environ.get("EDITOR", "vi")
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".tmp", delete=False) as tf:
            tf.write("# Enter description for new case.\n")
            tf.write("# Leading lines starting with '#' will be ignored.\n\n")
            tf_path = tf.name
            temp_desc_path = tf_path

        print("Opening editor for description...")
        try:
            subprocess.call([editor, tf_path])
            with open(tf_path, "r") as f:
                lines = f.readlines()
            description = strip_header_comments(lines).strip()

            # Refresh token after the editor is closed, in case it expired while typing
            from rh_support_lib.api import get_access_token

            token = get_access_token(args.token_file)
        except Exception:
            if os.path.exists(tf_path):
                os.remove(tf_path)
            raise

    if not description:
        print("Aborted: Empty description.")
        if temp_desc_path and os.path.exists(temp_desc_path):
            os.remove(temp_desc_path)
        sys.exit(0)

    # Attachments
    attachments = args.attachment or []
    for fpath in attachments:
        if not os.path.isfile(fpath):
            sys.exit(f"Error: Attachment '{fpath}' not found.")

    # 2. Confirmation Loop
    while True:
        print("\n--- New Case Overview ---")
        print(f"Product:     {product}")
        print(f"Version:     {version}")
        print(f"Summary:     {summary}")
        print(f"Severity:    {severity}")
        print(f"Type:        {case_type}")
        print(f"Attachments: {', '.join(attachments) if attachments else 'None'}")

        # Extra fields from template
        extra_fields = {
            k: v
            for k, v in defaults.items()
            if k
            not in [
                "product",
                "version",
                "summary",
                "description",
                "severity",
                "caseType",
            ]
        }
        if extra_fields:
            print(f"Extra Fields: {json.dumps(extra_fields, indent=2)}")

        print("-------------------------")

        choice = prompt_text("Submit this case? (y/n/edit)", default="y").lower()
        if choice == "y":
            break
        elif choice == "n":
            print("Cancelled.")
            if temp_desc_path and os.path.exists(temp_desc_path):
                os.remove(temp_desc_path)
            sys.exit(0)
        elif choice == "edit":
            # Simple edit loop
            print("Fields: product, version, summary, severity, type")
            field = prompt_text("Field to edit").lower()
            if field == "product":
                product = prompt_text("New Product", product)
            elif field == "version":
                version = prompt_text("New Version", version)
            elif field == "summary":
                summary = prompt_text("New Summary", summary)
            elif field == "severity":
                severity = prompt_text("New Severity", severity)
            elif field == "type":
                case_type = prompt_text("New Type", case_type)
            else:
                print("Unknown field.")

    # 3. Create Case
    print("Creating case...")
    payload = {
        "product": product,
        "version": version,
        "summary": summary,
        "description": description,
        "severity": severity,
        "type": case_type,
    }

    # Merge extra fields
    for k, v in defaults.items():
        if k not in payload:
            payload[k] = v

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        resp = requests.post(
            f"{API_URL}/cases", headers=headers, json=payload, timeout=30
        )
        if resp.status_code in [200, 201]:
            case_data = resp.json()
            case_number = case_data.get("caseNumber") or case_data.get("id")
            if not case_number and "locations" in case_data and case_data["locations"]:
                loc = case_data["locations"][0]
                case_number = loc.rstrip("/").split("/")[-1]

            print(f"Success: Case #{case_number} created.")

            # Cleanup temp file on success
            if temp_desc_path and os.path.exists(temp_desc_path):
                os.remove(temp_desc_path)
        else:
            print(f"Error: Case creation failed (HTTP {resp.status_code})")
            print(resp.text)
            if temp_desc_path:
                print(f"Draft description preserved at: {temp_desc_path}")
            sys.exit(1)
    except requests.exceptions.RequestException as e:
        if temp_desc_path:
            print(f"Draft description preserved at: {temp_desc_path}")
        sys.exit(f"Network Error: {e}")

    # 4. Upload Attachments
    if attachments and case_number:
        print("Uploading attachments...")
        # Reuse cmd_attach logic conceptually, but we need to pass args object or call function
        # Simpler to just reimplement the upload loop here since cmd_attach takes 'args'
        for fpath in attachments:
            print(f"Attaching '{os.path.basename(fpath)}'...")
            try:
                from requests_toolbelt.multipart.encoder import MultipartEncoder

                with open(fpath, "rb") as f:
                    m = MultipartEncoder(fields={"file": (os.path.basename(fpath), f)})
                    ep = f"{API_URL}/cases/{case_number}/attachments"
                    req_headers = headers.copy()
                    req_headers["Content-Type"] = m.content_type
                    up_resp = requests.post(
                        ep, headers=req_headers, data=m, timeout=(30, 3600)
                    )
                    if up_resp.status_code in [200, 201]:
                        print(f"  - {os.path.basename(fpath)}: Uploaded")
                    else:
                        print(
                            f"  - {os.path.basename(fpath)}: Failed ({up_resp.status_code})"
                        )
            except Exception as e:
                print(f"  - {os.path.basename(fpath)}: Error ({e})")

    return case_number
