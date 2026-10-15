from __future__ import annotations

import contextlib
import json
import os
from typing import Any

from rh_support_lib.config import load_config
from rh_support_lib.templates import resolve_templates_dir

# Curated catalog of standard Red Hat Enterprise products for offline fallback
DEFAULT_FALLBACK_PRODUCTS = [
    "Red Hat 3scale API Management",
    "Red Hat Advanced Cluster Management for Kubernetes",
    "Red Hat Advanced Cluster Security for Kubernetes",
    "Red Hat Ansible Automation Platform",
    "Red Hat Ceph Storage",
    "Red Hat Certificate System",
    "Red Hat Data Grid",
    "Red Hat Decision Manager",
    "Red Hat Developer Hub",
    "Red Hat Device Edge",
    "Red Hat Directory Server",
    "Red Hat Enterprise Linux",
    "Red Hat Enterprise Linux AI",
    "Red Hat Enterprise Linux for Real Time",
    "Red Hat Enterprise Linux for SAP Solutions",
    "Red Hat Fuse",
    "Red Hat Gluster Storage",
    "Red Hat JBoss Enterprise Application Platform",
    "Red Hat JBoss Web Server",
    "Red Hat OpenShift Container Platform",
    "Red Hat OpenShift Dedicated",
    "Red Hat OpenShift Data Foundation",
    "Red Hat OpenStack Platform",
    "Red Hat Process Automation Manager",
    "Red Hat Quay",
    "Red Hat Satellite",
    "Red Hat Single Sign-On",
    "Red Hat build of Keycloak",
    "Red Hat build of MicroShift",
]

# Query status choices for list and search
FILTER_STATUS_CHOICES = [
    "open",
    "closed",
    "redhat",
    "customer",
    "in_progress",
    "engineering",
    "needs_owner",
    "action_required",
]

# Action status choices for comment and update
COMMENT_STATUS_CHOICES = [
    "redhat",
    "customer",
    "in_progress",
    "closed",
    "none",
    "keep",
    "reopened",
    "engineering",
]

SEVERITY_CHOICES = [
    "Low",
    "Normal",
    "High",
    "Urgent",
    "1",
    "2",
    "3",
    "4",
]

CASE_TYPE_CHOICES = [
    "Standard",
    "Bug",
]


def product_completer(prefix: str = "", **kwargs: Any) -> list[str]:
    """Completes product names from local catalog cache, falling back to bundled products."""
    products: list[str] = []
    seen: set[str] = set()

    def add_product(name: str | None) -> None:
        if name and name.strip() and name.strip() not in seen:
            clean = name.strip()
            seen.add(clean)
            products.append(clean)

    # 1. Consult local disk cache if available
    cache_base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    catalog_path = os.path.join(
        cache_base, "rh-support-cli", "catalog", "products.json"
    )
    if os.path.isfile(catalog_path):
        with contextlib.suppress(Exception), open(catalog_path, "r") as f:
            data = json.load(f)
            for item in data.get("products", []):
                add_product(item.get("name"))

    # 2. Consult bundled package data catalog if present
    bundled_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "products.json"
    )
    if os.path.isfile(bundled_path):
        with contextlib.suppress(Exception), open(bundled_path, "r") as f:
            data = json.load(f)
            for item in data.get("products", []):
                add_product(item.get("name"))

    # 3. Merge curated fallback products
    for p in DEFAULT_FALLBACK_PRODUCTS:
        add_product(p)
    prefix_low = prefix.lower()
    # Return prefix matches first, then word/substring matches
    starts = [p for p in products if p.lower().startswith(prefix_low)]
    contains = [p for p in products if prefix_low in p.lower() and p not in starts]
    return (starts + contains) if (starts or contains) else products


def status_completer(
    prefix: str = "", parsed_args: Any = None, **kwargs: Any
) -> list[str]:
    """Completes status values based on the subcommand context."""
    subcmd = getattr(parsed_args, "command", "") if parsed_args else ""
    choices = (
        COMMENT_STATUS_CHOICES
        if subcmd in ("comment", "close")
        else FILTER_STATUS_CHOICES
    )
    prefix_low = prefix.lower()
    return [c for c in choices if c.lower().startswith(prefix_low)]


def severity_completer(prefix: str = "", **kwargs: Any) -> list[str]:
    """Completes case severity levels."""
    prefix_low = prefix.lower()
    return [s for s in SEVERITY_CHOICES if s.lower().startswith(prefix_low)]


def bookmark_completer(prefix: str = "", **kwargs: Any) -> list[str]:
    """Completes bookmark names defined in config.yaml."""
    with contextlib.suppress(Exception):
        cfg = load_config()
        bms = list((cfg.get("bookmarks") or {}).keys())
        prefix_low = prefix.lower()
        return [b for b in bms if b.lower().startswith(prefix_low)]
    return []


def template_completer(prefix: str = "", **kwargs: Any) -> list[str]:
    """Completes template names found in the templates directory."""
    with contextlib.suppress(Exception):
        tpl_dir = resolve_templates_dir()
        if os.path.isdir(tpl_dir):
            names = []
            for f in os.listdir(tpl_dir):
                if f.endswith((".yaml", ".yml")):
                    names.append(os.path.splitext(f)[0])
            prefix_low = prefix.lower()
            return [t for t in sorted(names) if t.lower().startswith(prefix_low)]
    return []


def case_completer(prefix: str = "", **kwargs: Any) -> list[str]:
    """Completes case numbers from tracked and followed cases in state.yaml."""
    case_numbers: list[str] = []
    seen: set[str] = set()

    state_path = os.environ.get("RH_SUPPORT_STATE")
    if not state_path:
        xdg_state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser(
            "~/.local/state"
        )
        state_path = os.path.join(xdg_state, "rh-support-cli", "state.yaml")

    if os.path.isfile(state_path):
        with contextlib.suppress(Exception):
            import yaml

            with open(state_path, "r") as f:
                data = yaml.safe_load(f) or {}
            users = data.get("users", {})
            for u_data in users.values():
                for c_num in u_data.get("tracked_cases", {}):
                    c_str = str(c_num).strip()
                    if c_str and c_str not in seen:
                        seen.add(c_str)
                        case_numbers.append(c_str)
                for c_num in u_data.get("following", {}):
                    c_str = str(c_num).strip()
                    if c_str and c_str not in seen:
                        seen.add(c_str)
                        case_numbers.append(c_str)

    prefix_low = prefix.lower()
    return [c for c in case_numbers if c.lower().startswith(prefix_low)]


def case_type_completer(prefix: str = "", **kwargs: Any) -> list[str]:
    """Completes case types for ticket creation."""
    prefix_low = prefix.lower()
    return [t for t in CASE_TYPE_CHOICES if t.lower().startswith(prefix_low)]


def setup_completers(parser: Any) -> None:
    """Configures dynamic completers on argument parser actions."""
    try:
        from argcomplete.completers import FilesCompleter
    except ImportError:
        return

    files_completer = FilesCompleter()

    # Collect all parsers (root parser + all subparsers recursively)
    parsers_to_process = [parser]
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            for sub_p in action.choices.values():
                if sub_p not in parsers_to_process:
                    parsers_to_process.append(sub_p)

    # Process all collected parsers
    for p in parsers_to_process:
        for act in p._actions:
            # If the action already has explicit choices defined (e.g. search --type, --format, --order),
            # argcomplete handles it natively from act.choices. Do NOT overwrite.
            if getattr(act, "choices", None):
                continue

            dest = getattr(act, "dest", "")
            opts = getattr(act, "option_strings", [])

            # 1. Product
            if dest in ("product", "prod") or "--product" in opts:
                act.completer = product_completer

            # 2. Status
            elif dest in ("status", "stat") or "--status" in opts:
                act.completer = status_completer

            # 3. Severity
            elif dest == "severity" or "--severity" in opts:
                act.completer = severity_completer

            # 4. Bookmark
            elif dest in ("bookmark", "bookmarks") or "--bookmark" in opts:
                act.completer = bookmark_completer

            # 5. Template
            elif dest in ("template", "templates") or "--template" in opts:
                act.completer = template_completer

            # 6. Case number
            elif dest in ("case", "cases", "case_pos") or "--case" in opts:
                act.completer = case_completer

            # 7. Case Type (e.g. ticket create which has no choices defined)
            elif dest == "type" or "--type" in opts:
                act.completer = case_type_completer
            # 8. Local file paths
            elif dest in (
                "file",
                "files",
                "output",
                "token_file",
                "config_file",
                "description_file",
                "attachment",
            ) and (
                any(
                    o
                    in (
                        "-f",
                        "--file",
                        "-o",
                        "--output",
                        "--token-file",
                        "--config-file",
                        "--description-file",
                        "--attachment",
                    )
                    for o in opts
                )
            ):
                act.completer = files_completer
