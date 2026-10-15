#!/usr/bin/env python3
"""Script to fetch the latest active Red Hat product catalog and update rh_support_lib/data/products.json.

Requirements:
    Requires a valid Red Hat Offline Token via REDHAT_SUPPORT_OFFLINE_TOKEN environment variable,
    --token-file argument, or ~/.config/rh-support-cli/token.

Usage:
    ./scripts/update_catalog.py
    ./scripts/update_catalog.py --token-file /path/to/token.txt
    ./scripts/update_catalog.py --output custom_products.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# Ensure rh_support_lib can be imported from parent directory
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from rh_support_lib.api import RedHatAPIClient
from rh_support_lib.services.cases import CaseService


def update_catalog(
    token_file: str | None = None,
    output_path: str | None = None,
    update_user_cache: bool = True,
    quiet: bool = False,
) -> int:
    """Queries GraphQL product catalog across all pages and writes products.json."""
    default_output = os.path.join(repo_root, "rh_support_lib", "data", "products.json")
    target_output = os.path.abspath(output_path or default_output)
    if not quiet:
        print("Authenticating with Red Hat API...")

    try:
        api_client = RedHatAPIClient(token_file_arg=token_file)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"Authentication Error: {e}\n")
        return 1

    case_service = CaseService(api_client)

    if not quiet:
        print(
            "Querying active products across all GraphQL pages (this may take a few seconds)..."
        )

    try:
        products = case_service.get_active_products(all_pages=True, use_cache=False)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"API Error fetching products: {e}\n")
        return 1

    if not products:
        sys.stderr.write("Error: No active products returned from API.\n")
        return 1

    # Prepare payload
    serialized_products = [
        {
            "id": p.id,
            "name": p.name,
            "version_name": p.version_name,
            "family": p.family,
            "parent_id": p.parent_id,
        }
        for p in products
    ]

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "updated_at": now_iso,
        "count": len(serialized_products),
        "products": serialized_products,
    }

    # Write to target output file
    os.makedirs(os.path.dirname(target_output), exist_ok=True)
    with open(target_output, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")

    if not quiet:
        print(f"✓ Saved {len(serialized_products)} products to: {target_output}")

    # Also update user's local XDG disk cache if requested
    if update_user_cache:
        cache_base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        user_catalog_path = os.path.join(
            cache_base, "rh-support-cli", "catalog", "products.json"
        )
        try:
            os.makedirs(os.path.dirname(user_catalog_path), exist_ok=True)
            with open(user_catalog_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "cached_at": datetime.now(timezone.utc).timestamp(),
                        "products": serialized_products,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
                f.write("\n")
            if not quiet:
                print(f"✓ Updated local cache at: {user_catalog_path}")
        except Exception as e:  # noqa: BLE001
            if not quiet:
                print(f"Note: Could not update user cache ({e})")

    if not quiet:
        print("\nNext step: Review and commit the updated product catalog:")
        print("  git add rh_support_lib/data/products.json")
        print(f"  git commit -m 'Update Red Hat product catalog ({now_iso[:10]})'")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch latest Red Hat product catalog and update repository products.json"
    )
    parser.add_argument("--token-file", help="Path to file containing offline token")
    parser.add_argument(
        "-o",
        "--output",
        help="Path to output products.json (defaults to rh_support_lib/data/products.json)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not update user's local ~/.cache/rh-support-cli/catalog/products.json",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress status output"
    )
    args = parser.parse_args()

    rc = update_catalog(
        token_file=args.token_file,
        output_path=args.output,
        update_user_cache=not args.no_cache,
        quiet=args.quiet,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
