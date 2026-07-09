import os
import sys
import yaml
import requests
from rh_support_lib.constants import API_URL, COLORS
from rh_support_lib.api import get_json
from rh_support_lib.utils import colorize
from rh_support_lib.templates import TemplateEngine

# Full set of valid fields in UpdateCaseRequest
VALID_PUT_FIELDS = {
    "summary",
    "description",
    "status",
    "product",
    "version",
    "caseType",
    "accountNumberRef",
    "openshiftClusterID",
    "openshiftClusterVersion",
    "notes",
    "customerEscalation",
    "contactSSOName",
    "name",
    "clearPhoneNumber",
    "phone",
    "phoneCountryCode",
    "phoneAreaCodePrefixLineNumber",
    "suppliedPhoneNumberVerified",
    "emailAddress",
    "severity",
    "hostname",
    "enhancedSLA",
    "groupNumber",
    "entitlementSla",
    "fts",
    "caseLanguage",
    "issue",
    "environment",
    "periodicityOfIssue",
    "timeFramesAndUrgency",
    "cep",
    "alternateId",
    "noClusterIdReason",
    "noClusterIdReasonExplanation",
    "contactInfo24x7",
    "screenSessionRequested",
    "reopenedReason",
}


def cmd_apply(args, token, config):
    # 1. Fetch Case Details
    print(f"Fetching case {args.case} details...")
    try:
        case = get_json(f"{API_URL}/cases/{args.case}", token)
    except Exception as e:
        sys.exit(f"Error fetching case details: {e}")

    if not case or not isinstance(case, dict):
        sys.exit(f"Error: Case {args.case} not found or invalid response.")

    # 2. Render templates
    templates_to_process = args.template
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
    template_data = engine.process(templates_to_process, template_vars)
    # Ignore fields starting with '_' across all actions
    template_data = {k: v for k, v in template_data.items() if not k.startswith("_")}

    # 3. Compute field differences
    updates = {}
    for k, v in template_data.items():
        # Map template field 'type' to Case API field 'caseType'
        target_key = "caseType" if k == "type" else k

        if target_key in VALID_PUT_FIELDS:
            current_val = case.get(target_key)
            if target_key == "caseType" and current_val is None:
                current_val = case.get("type")

            # Check if value differs
            if isinstance(v, bool):
                current_bool = (
                    current_val
                    if isinstance(current_val, bool)
                    else str(current_val).lower() in ("true", "1", "yes")
                )
                if current_bool != v:
                    updates[target_key] = v
            elif v is not None:
                if str(current_val).strip() != str(v).strip():
                    updates[target_key] = v

    # 4. Compute watchers/notified users differences
    watchers_keys = ["notified_users", "notifiedUsers", "watchers"]
    template_watchers = []
    for wk in watchers_keys:
        if wk in template_data:
            val = template_data[wk]
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        sso = item.get("ssoUsername") or item.get("username")
                        if sso:
                            template_watchers.append(str(sso))
                    elif isinstance(item, str):
                        template_watchers.append(item)
            elif isinstance(val, str):
                template_watchers.extend(
                    [s.strip() for s in val.split(",") if s.strip()]
                )

    # Dedup target watchers case-insensitively while preserving original case
    target_watchers = {}
    for w in template_watchers:
        target_watchers[w.lower()] = w

    existing_watchers = case.get("notifiedUsers", [])
    existing_ssos = set()
    if isinstance(existing_watchers, list):
        for u in existing_watchers:
            if isinstance(u, dict):
                sso = u.get("ssoUsername")
                if sso:
                    existing_ssos.add(sso.lower())
            elif isinstance(u, str):
                existing_ssos.add(u.lower())

    watchers_to_add = []
    for low_sso, orig_sso in target_watchers.items():
        if low_sso not in existing_ssos:
            watchers_to_add.append(orig_sso)

    # 5. Output comparison and update plan
    if not updates and not watchers_to_add:
        print(
            f"Case #{args.case} already adheres to the template(s). No changes needed."
        )
        sys.exit(0)

    print(f"\nPlan for Case #{args.case}:")
    if updates:
        print("  Field changes:")
        for k, v in updates.items():
            current_val = case.get(k)
            if k == "caseType" and current_val is None:
                current_val = case.get("type")
            # Apply terminal colors if enabled
            c_label = colorize(k, COLORS.BOLD, args.simple_output)
            print(f"    - {c_label}: {current_val} -> {v}")

    if watchers_to_add:
        print("  Watchers to add:")
        for w in sorted(watchers_to_add):
            c_watcher = colorize(w, COLORS.GREEN, args.simple_output)
            print(f"    - {c_watcher}")

    # 6. Apply updates or exit on dry-run
    if args.dry_run:
        print("\n[Dry-run] Would apply changes listed above.")
        sys.exit(0)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if updates:
        print("\nUpdating case fields...")
        try:
            resp = requests.put(
                f"{API_URL}/cases/{args.case}",
                headers=headers,
                json=updates,
                timeout=30,
            )
            if resp.status_code in [200, 201]:
                print("Success: Case fields updated.")
            else:
                print(f"Error: Field update failed (HTTP {resp.status_code})")
                print(resp.text)
                sys.exit(1)
        except requests.exceptions.RequestException as e:
            sys.exit(f"Network Error updating fields: {e}")

    if watchers_to_add:
        print("Adding watchers...")
        payload = {"user": [{"ssoUsername": sso} for sso in watchers_to_add]}
        try:
            resp = requests.post(
                f"{API_URL}/cases/{args.case}/notifiedusers",
                headers=headers,
                json=payload,
                timeout=30,
            )
            if resp.status_code in [200, 201]:
                print("Success: Watchers added.")
            else:
                print(f"Error: Watcher addition failed (HTTP {resp.status_code})")
                print(resp.text)
                sys.exit(1)
        except requests.exceptions.RequestException as e:
            sys.exit(f"Network Error adding watchers: {e}")


def cmd_list_templates(args, config):
    templates_dir = os.path.expanduser("~/.config/rh-support-cli/templates")
    if not os.path.isdir(templates_dir):
        print(f"Template directory does not exist: {templates_dir}")
        sys.exit(0)

    files = []
    try:
        for f in os.listdir(templates_dir):
            if f.endswith(".yaml") or f.endswith(".yml"):
                files.append(f)
    except Exception as e:
        sys.exit(f"Error listing template files: {e}")

    if not files:
        print(f"No templates found in {templates_dir}")
        sys.exit(0)

    files.sort()
    print(f"Found {len(files)} template(s) in {templates_dir}:\n")

    for f in files:
        path = os.path.join(templates_dir, f)
        name = os.path.splitext(f)[0]

        try:
            with open(path, "r") as fh:
                data = yaml.safe_load(fh) or {}
        except Exception as e:
            print(f"Template:    {colorize(name, COLORS.RED, args.simple_output)}")
            print(f"  File:      {f}")
            print(f"  Error:     Failed to load: {e}\n")
            continue

        description = (
            data.get("_template_description")
            or data.get("_template_desc")
            or data.get("_description")
            or data.get("_desc")
            or data.get("description")
            or data.get("desc")
            or "No description provided."
        )

        # Get includes
        includes = data.get("include_templates", [])
        if isinstance(includes, str):
            includes = [includes]

        # Get watchers
        watchers_keys = ["notified_users", "notifiedUsers", "watchers"]
        watchers = []
        for wk in watchers_keys:
            if wk in data:
                val = data[wk]
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            sso = item.get("ssoUsername") or item.get("username")
                            if sso:
                                watchers.append(str(sso))
                        elif isinstance(item, str):
                            watchers.append(item)
                elif isinstance(val, str):
                    watchers.extend([s.strip() for s in val.split(",") if s.strip()])

        # Fields (everything else except description, include_templates, watchers, and anything starting with '_')
        special_keys = {
            "description",
            "desc",
            "include_templates",
            "_template_description",
            "_template_desc",
            "_description",
            "_desc",
        } | set(watchers_keys)
        fields = {
            k: v
            for k, v in data.items()
            if k not in special_keys and not k.startswith("_")
        }

        # Colorize and print
        if args.simple_output:
            c_name = colorize(name, COLORS.BOLD, args.simple_output)
            print(f"Template:    {c_name}")
            print(f"  File:      {f}")
            print(f"  Desc:      {description}")

            if fields:
                fields_str = ", ".join(f"{k}: {v}" for k, v in fields.items())
                print(f"  Fields:    {fields_str}")
            if watchers:
                watchers_str = ", ".join(
                    colorize(w, COLORS.GREEN, args.simple_output) for w in watchers
                )
                print(f"  Watchers:  {watchers_str}")
            if includes:
                includes_str = ", ".join(includes)
                print(f"  Includes:  {includes_str}")
            print()
        else:
            from rich.console import Console
            from rich.panel import Panel
            from rich.text import Text

            console = Console()

            # Build body text
            body_text = Text()
            body_text.append("File:      ", style="bold yellow")
            body_text.append(f"{f}\n")
            body_text.append("Desc:      ", style="bold yellow")
            body_text.append(f"{description}\n")

            if fields:
                fields_str = ", ".join(f"{k}: {v}" for k, v in fields.items())
                body_text.append("Fields:    ", style="bold yellow")
                body_text.append(f"{fields_str}\n")
            if watchers:
                watchers_str = ", ".join(watchers)
                body_text.append("Watchers:  ", style="bold yellow")
                body_text.append(f"{watchers_str}\n", style="green")
            if includes:
                includes_str = ", ".join(includes)
                body_text.append("Includes:  ", style="bold yellow")
                body_text.append(f"{includes_str}\n")

            console.print(
                Panel(
                    body_text.strip(),
                    title=f"Template: [bold cyan]{name}[/]",
                    border_style="cyan",
                )
            )
            console.print()
