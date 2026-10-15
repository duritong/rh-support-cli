import json
import os
import subprocess
import sys
import tempfile

import yaml

from rh_support_lib.services.attachments import AttachmentService
from rh_support_lib.services.cases import CaseService
from rh_support_lib.templates import TemplateEngine, resolve_templates_dir
from rh_support_lib.utils import prompt_text, select_from_list, strip_header_comments


def cmd_create(args, api_client, config):

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
                    except yaml.YAMLError:
                        template_vars[k] = v

        templates_dir = resolve_templates_dir(config)
        engine = TemplateEngine(templates_dir)
        defaults = engine.process(templates_to_process, template_vars)
        # Ignore fields starting with '_' across all actions
        defaults = {k: v for k, v in defaults.items() if not k.startswith("_")}

    # 1. Gather Data (Flags or Interactive)

    case_service = CaseService(api_client)

    # Product
    product = args.product or defaults.get("product")
    if not product:
        print("Fetching products...")
        try:
            products = case_service.get_active_products()
            prod_dicts = [{"name": p.name} for p in products]
            selected = select_from_list("Select Product", prod_dicts)
            product = selected.get("name") if selected else prompt_text("Product Name")
        except Exception:  # noqa: BLE001
            product = prompt_text("Product Name")

    version = args.version or defaults.get("version")
    if not version:
        version = prompt_text("Product Version", default="9.0")
    # Severity and Type
    severity = (
        args.severity
        or defaults.get("severity")
        or prompt_text("Severity", default="Normal")
    )
    case_type = (
        args.type
        or defaults.get("caseType")
        or prompt_text("Case Type", default="Standard")
    )
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
    case_service = CaseService(api_client)
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
        and not k.startswith("_")
    }
    try:
        fmt = getattr(args, "format", "markdown") or "markdown"
        case_number = case_service.create_case(
            summary=summary,
            description=description,
            product_name=product or "Red Hat Enterprise Linux",
            version_name=version or "9.0",
            severity=severity or "3 (Normal)",
            case_type=case_type or "Standard",
            account_number=defaults.get("accountNumber"),
            extra_fields=extra_fields,
            body_format=fmt,
        )
        print(f"Success: Case #{case_number} created.")
        if temp_desc_path and os.path.exists(temp_desc_path):
            os.remove(temp_desc_path)
    except Exception as e:  # noqa: BLE001
        if temp_desc_path:
            print(f"Draft description preserved at: {temp_desc_path}")
        label = (
            "Case validation failed" if isinstance(e, ValueError) else "Network Error"
        )
        sys.exit(f"{label}: {e}")

    # 4. Upload Attachments
    if attachments and case_number:
        print("Uploading attachments...")
        att_service = AttachmentService(api_client)
        for fpath in attachments:
            print(f"Attaching '{os.path.basename(fpath)}'...")
            try:
                att_service.upload_attachment(case_number, fpath)
                print(f"  - {os.path.basename(fpath)}: Uploaded")
            except Exception as e:  # noqa: BLE001
                print(f"  - {os.path.basename(fpath)}: Error ({e})")
    return case_number
