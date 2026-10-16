import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

import yaml

from rh_support_lib.operations import (
    CaseCreationOutcomeUnknownError,
    PartialCaseCreationError,
    create_case_with_attachments,
)
from rh_support_lib.services.attachments import AttachmentService
from rh_support_lib.services.cases import CaseService
from rh_support_lib.templates import TemplateEngine, resolve_templates_dir
from rh_support_lib.utils import (
    EditorError,
    edit_file,
    format_structured_output,
    prompt_text,
    select_from_list,
    strip_header_comments,
)


def cmd_create(args, api_client, config):
    # Prompts and service diagnostics must not contaminate serialized receipts.
    output = sys.stdout
    with redirect_stdout(sys.stderr):
        return _cmd_create(args, api_client, config, output)


def _cmd_create(args, api_client, config, output):

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
        try:
            defaults = engine.process(templates_to_process, template_vars)
        except ValueError as e:
            sys.exit(f"Template Error: {e}")
        # Ignore fields starting with '_' across all actions
        defaults = {k: v for k, v in defaults.items() if not k.startswith("_")}

    # YAML scalars should behave like their equivalent command-line strings.
    if type(defaults.get("severity")) is int and defaults["severity"] in range(1, 5):
        defaults["severity"] = str(defaults["severity"])
    if type(defaults.get("version")) in (int, float):
        defaults["version"] = str(defaults["version"])

    # 1. Gather Data (Flags or Interactive)

    case_service = CaseService(api_client, config=config)

    skip_confirmation = getattr(args, "yes", False)
    if skip_confirmation:
        required = {
            "product": args.product or defaults.get("product"),
            "version": args.version or defaults.get("version"),
            "severity": args.severity or defaults.get("severity"),
            "type": args.type or defaults.get("caseType"),
            "summary": args.summary or defaults.get("summary"),
        }
        missing = [
            name
            for name, value in required.items()
            if not isinstance(value, str) or not value.strip()
        ]
        if missing:
            sys.exit("Error: --yes requires supplied values for: " + ", ".join(missing))

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
        try:
            with open(args.description_file, "r") as f:
                description = f.read().strip()
        except (OSError, UnicodeError):
            sys.exit("Error: Unable to read description file. No case created.")
        if not description:
            sys.exit("Error: Empty description file. No case created.")
    else:
        description = defaults.get("description", "")

    if not description:
        if skip_confirmation:
            sys.exit(
                "Error: --yes requires a description from --description-file or a template. "
                "No case created."
            )
        # If not provided, use editor
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".tmp", delete=False) as tf:
            tf.write("# Enter description for new case.\n")
            tf.write("# Leading lines starting with '#' will be ignored.\n\n")
            tf_path = tf.name
            temp_desc_path = tf_path

        print("Opening editor for description...")
        try:
            edit_file(tf_path)
            with open(tf_path, "r") as f:
                lines = f.readlines()
            description = strip_header_comments(lines).strip()

        except EditorError as error:
            sys.exit(f"Editor Error: {error}")
        except (OSError, UnicodeError):
            sys.exit(
                f"Editor Error: Unable to read edited description. Draft path: {tf_path}"
            )

    if not description:
        if temp_desc_path:
            print(f"Draft description preserved at: {temp_desc_path}", file=sys.stderr)
        sys.exit("Error: Empty description. No case created.")

    # Attachments
    attachments = args.attachment or []
    for fpath in attachments:
        if not os.path.isfile(fpath):
            if temp_desc_path:
                print(f"Draft description preserved at: {temp_desc_path}")
            sys.exit(f"Error: Attachment '{fpath}' not found.")

    # 2. Confirmation Loop
    while not skip_confirmation:
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
    partial_error = None
    try:
        fmt = getattr(args, "format", "markdown") or "markdown"
        receipt = create_case_with_attachments(
            case_service,
            AttachmentService(api_client),
            {
                "summary": summary,
                "description": description,
                "product_name": product or "Red Hat Enterprise Linux",
                "version_name": version or "9.0",
                "severity": severity or "3 (Normal)",
                "case_type": case_type or "Standard",
                "account_number": defaults.get("accountNumber"),
                "extra_fields": extra_fields,
                "body_format": fmt,
            },
            files=attachments,
        )
    except PartialCaseCreationError as error:
        receipt = error.receipt
        partial_error = error
    except CaseCreationOutcomeUnknownError as error:
        if temp_desc_path:
            print(f"Description recovery copy preserved at: {temp_desc_path}")
        sys.exit(str(error))
    except Exception as e:  # noqa: BLE001
        if temp_desc_path:
            print(f"Draft description preserved at: {temp_desc_path}")
        diagnostic = (
            "Case validation failed: Check product/version, summary/description, "
            "attachment readability, and contact/account/record-type lookups. "
            "For SSO identity, set REDHAT_SUPPORT_USERNAME if needed. "
            if isinstance(e, ValueError)
            else "Network Error: "
        )
        sys.exit(
            f"{diagnostic}Creation did not return a confirmed case. "
            "Check the Red Hat Customer Portal before retrying."
        )

    # The description is submitted even when attachments fail. Never call it unsent.
    if temp_desc_path and os.path.exists(temp_desc_path):
        try:
            os.remove(temp_desc_path)
        except OSError:
            print(f"Submitted description copy remains at: {temp_desc_path}")

    output_format = (
        "json" if getattr(args, "json", False) else getattr(args, "output_format", None)
    )
    if output_format in ("json", "yaml"):
        print(format_structured_output(receipt, fmt=output_format), file=output)
    else:
        print(f"Success: Case #{receipt['case_number']} created.", file=output)
        for attachment in receipt["attachments"]:
            status = "Uploaded" if attachment["uploaded"] else attachment["error"]
            print(
                f"  - {attachment['file']}: {status}",
                file=output if attachment["uploaded"] else sys.stderr,
            )
    if partial_error:
        print(str(partial_error), file=sys.stderr)
        sys.exit(1)
    return receipt["case_number"]
