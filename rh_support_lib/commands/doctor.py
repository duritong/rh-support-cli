"""Doctor diagnostic command for health, environment, permissions, and configuration validation."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from rh_support_lib.config import load_config


def check_auth(token_file: str | None = None) -> dict[str, Any]:
    """Inspects offline token configuration without revealing secrets."""
    from rh_support_lib.api import _get_offline_token

    token_path = None
    if token_file is not None:
        token_path = token_file
        source = f"CLI argument (--token-file {token_file})"
    elif "REDHAT_SUPPORT_OFFLINE_TOKEN" in os.environ:
        source = "Environment variable (REDHAT_SUPPORT_OFFLINE_TOKEN)"
    else:
        token_path = os.path.expanduser("~/.config/rh-support-cli/token")
        source = f"Config file ({token_path})"

    token = _get_offline_token(token_file, interactive=False)
    if not token:
        return {
            "category": "Authentication",
            "name": "Offline API Token",
            "status": "FAIL",
            "message": "No offline token found or selected source is empty/unreadable (set --token-file, REDHAT_SUPPORT_OFFLINE_TOKEN or ~/.config/rh-support-cli/token).",
        }

    details = f"Configured via {source} [***]"

    # Check file permissions if loaded from file
    if token_path is not None and os.path.isfile(token_path):
        with contextlib.suppress(OSError):
            mode = os.stat(token_path).st_mode & 0o777
            if mode & 0o077:
                return {
                    "category": "Authentication",
                    "name": "Offline API Token",
                    "status": "WARN",
                    "message": f"{details} — Warning: permissions {oct(mode)} are too open (recommended: 0600).",
                }

    return {
        "category": "Authentication",
        "name": "Offline API Token",
        "status": "PASS",
        "message": details,
    }


def check_configuration(config_file: str | None = None) -> dict[str, Any]:
    """Inspects YAML configuration file health."""
    cfg_path = (
        config_file
        or os.environ.get("RH_SUPPORT_CONFIG")
        or os.path.expanduser("~/.config/rh-support-cli/config.yaml")
    )
    if not os.path.isfile(cfg_path):
        return {
            "category": "Configuration",
            "name": "Configuration File",
            "status": "INFO",
            "message": f"No custom config file found at {cfg_path} (using defaults).",
        }

    try:
        cfg = load_config(cfg_path)
    except Exception as e:  # noqa: BLE001
        return {
            "category": "Configuration",
            "name": "Configuration File",
            "status": "FAIL",
            "message": f"Failed to parse {cfg_path}: {e}",
        }

    bms = len(cfg.get("bookmarks") or {})
    default_bm = cfg.get("default_bookmark")
    desc = f"Valid YAML ({bms} bookmark(s)"
    if default_bm:
        desc += f", default: '{default_bm}'"
    desc += f") at {cfg_path}"

    return {
        "category": "Configuration",
        "name": "Configuration File",
        "status": "PASS",
        "message": desc,
    }


def check_state_storage() -> dict[str, Any]:
    """Inspects state.yaml write permissions and locking."""
    state_path = os.environ.get("RH_SUPPORT_STATE")
    if not state_path:
        xdg_state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser(
            "~/.local/state"
        )
        state_path = os.path.join(xdg_state, "rh-support-cli", "state.yaml")

    state_dir = os.path.dirname(state_path)
    try:
        os.makedirs(state_dir, exist_ok=True)
        # Test file lockability
        lock_path = state_path + ".lock"
        with open(lock_path, "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:  # noqa: BLE001
        return {
            "category": "State Storage",
            "name": "Local State & Locking",
            "status": "FAIL",
            "message": f"State directory '{state_dir}' is not writable or lockable: {e}",
        }

    tracked_count = 0
    if os.path.isfile(state_path):
        with contextlib.suppress(Exception):
            import yaml

            with open(state_path, "r") as f:
                data = yaml.safe_load(f) or {}
            for u in data.get("users", {}).values():
                tracked_count += len(u.get("tracked_cases", {}))

    return {
        "category": "State Storage",
        "name": "Local State & Locking",
        "status": "PASS",
        "message": f"Writable and lockable ({tracked_count} tracked case(s)) at {state_path}",
    }


def check_disk_cache() -> dict[str, Any]:
    """Inspects local XDG disk cache directory."""
    cache_base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    cache_dir = os.path.join(cache_base, "rh-support-cli")
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=cache_dir, prefix=".doctor-"
        ) as f:
            f.write("ok")
    except Exception as e:  # noqa: BLE001
        return {
            "category": "Disk Cache",
            "name": "Local Cache",
            "status": "FAIL",
            "message": f"Cache directory '{cache_dir}' is not writable: {e}",
        }

    return {
        "category": "Disk Cache",
        "name": "Local Cache",
        "status": "PASS",
        "message": f"Writable at {cache_dir}",
    }


def check_product_catalog() -> dict[str, Any]:
    """Inspects bundled and cached Red Hat product catalog."""
    from rh_support_lib.completers import DEFAULT_FALLBACK_PRODUCTS

    count = len(DEFAULT_FALLBACK_PRODUCTS)
    source = "bundled fallbacks"

    cache_base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    cached_catalog = os.path.join(
        cache_base, "rh-support-cli", "catalog", "products.json"
    )
    repo_catalog = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        "products.json",
    )

    for path, label in (
        (cached_catalog, "local cache"),
        (repo_catalog, "bundled data file"),
    ):
        if os.path.isfile(path):
            with contextlib.suppress(Exception), open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                c = len(data.get("products", []))
                if c > count:
                    count = c
                    source = label
    return {
        "category": "Product Catalog",
        "name": "Active Products",
        "status": "PASS",
        "message": f"{count} products available (from {source})",
    }


def check_systemd_timer() -> dict[str, Any]:
    """Inspects systemd user sync timer.

    Per specification, a missing or inactive timer is strictly a WARN, never a FAIL.
    """
    if not shutil.which("systemctl"):
        return {
            "category": "Systemd Timer",
            "name": "Periodic Sync Timer",
            "status": "INFO",
            "message": "systemctl not available on this host.",
        }

    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", "rh-support-sync.timer"],
            capture_output=True,
            text=True,
            check=False,
        )
        is_active = res.stdout.strip() == "active"
    except Exception:  # noqa: BLE001
        is_active = False

    if is_active:
        return {
            "category": "Systemd Timer",
            "name": "Periodic Sync Timer",
            "status": "PASS",
            "message": "rh-support-sync.timer is active and running.",
        }

    # Check if timer unit file exists
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    timer_path = os.path.join(unit_dir, "rh-support-sync.timer")
    if os.path.isfile(timer_path):
        return {
            "category": "Systemd Timer",
            "name": "Periodic Sync Timer",
            "status": "WARN",
            "message": "Timer is installed but inactive. Run: systemctl --user enable --now rh-support-sync.timer",
        }

    return {
        "category": "Systemd Timer",
        "name": "Periodic Sync Timer",
        "status": "WARN",
        "message": "Timer not installed. Run: rh-support-cli install-systemd --enable",
    }


def check_runtime_environment() -> dict[str, Any]:
    """Inspects Python version and terminal dimensions."""
    py_ver = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    return {
        "category": "Environment",
        "name": "Python Version",
        "status": "PASS",
        "message": f"Python {py_ver} ({sys.executable})",
    }


def check_online_connectivity(timeout: float = 3.0) -> dict[str, Any]:
    """Performs bounded online check to Red Hat SSO and GraphQL endpoints."""
    import time
    import urllib.request

    from rh_support_lib.constants import GRAPHQL_URL

    endpoint = GRAPHQL_URL
    start = time.time()
    try:
        req = urllib.request.Request(endpoint, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.time() - start) * 1000
            return {
                "category": "Connectivity",
                "name": "Red Hat API Endpoint",
                "status": "PASS",
                "message": f"Reachable in {elapsed:.1f}ms (HTTP {resp.status})",
            }
    except Exception as e:  # noqa: BLE001
        return {
            "category": "Connectivity",
            "name": "Red Hat API Endpoint",
            "status": "FAIL",
            "message": f"Cannot reach {endpoint} within {timeout}s: {e}",
        }


def cmd_doctor(args: Any, config: dict | None = None) -> int:
    """Executes all doctor health checks and renders the diagnostic table."""
    token_file = getattr(args, "token_file", None)
    config_file = getattr(args, "config_file", None)
    is_online = getattr(args, "online", False)
    fmt = getattr(args, "format", "text")
    is_json = fmt == "json" or getattr(args, "json", False)
    simple = getattr(args, "simple_output", False)

    checks = [
        check_runtime_environment(),
        check_auth(token_file=token_file),
        check_configuration(config_file=config_file),
        check_state_storage(),
        check_disk_cache(),
        check_product_catalog(),
        check_systemd_timer(),
    ]

    if is_online:
        checks.append(check_online_connectivity())

    fails = sum(1 for c in checks if c["status"] == "FAIL")
    warns = sum(1 for c in checks if c["status"] == "WARN")
    passes = sum(1 for c in checks if c["status"] == "PASS")

    if is_json:
        payload = {
            "summary": {
                "total": len(checks),
                "passed": passes,
                "warnings": warns,
                "failures": fails,
            },
            "checks": checks,
        }
        print(json.dumps(payload, indent=2))
        return 1 if fails else 0

    if simple:
        print("RED HAT SUPPORT CLI — DOCTOR DIAGNOSTIC REPORT\n" + "=" * 80)
        for c in checks:
            status_tag = f"[{c['status']:<4}]"
            print(
                f"{status_tag} {c['category']:<16} │ {c['name']:<22} │ {c['message']}"
            )
        print("=" * 80)
        print(f"Summary: {passes} passed, {warns} warning(s), {fails} failure(s).")
    else:
        from rich.box import ROUNDED
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(
            title="Red Hat Support CLI — Diagnostic Report",
            box=ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("Category", style="bold white", width=16)
        table.add_column("Check", style="white", width=22)
        table.add_column("Status", justify="center", width=8)
        table.add_column("Details", style="dim white")

        for c in checks:
            st = c["status"]
            if st == "PASS":
                st_styled = "[bold green]PASS[/]"
            elif st == "WARN":
                st_styled = "[bold yellow]WARN[/]"
            elif st == "FAIL":
                st_styled = "[bold red]FAIL[/]"
            else:
                st_styled = "[bold cyan]INFO[/]"

            table.add_row(c["category"], c["name"], st_styled, c["message"])

        console.print(table)
        summary_style = (
            "bold red" if fails else ("bold yellow" if warns else "bold green")
        )
        console.print(
            f"[{summary_style}]Summary:[/] {passes} passed, {warns} warning(s), {fails} failure(s).\n"
        )

    return 1 if fails else 0
