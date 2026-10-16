"""Drafts discovery, preview, and cleanup commands for preserved comment drafts."""

from __future__ import annotations

import os
import re
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from typing import Any

from rh_support_lib.config import resolve_drafts_dir
from rh_support_lib.utils import (
    draft_identity,
    draft_lock,
    format_structured_output,
    read_draft,
    remove_draft,
)


def find_drafts(
    case_number: str | None = None,
    drafts_dir: str | None = None,
) -> list[dict[str, Any]]:
    """Scans drafts directory and returns metadata for saved drafts."""
    target_dir = os.path.realpath(
        os.path.expanduser(drafts_dir or resolve_drafts_dir())
    )
    if not os.path.isdir(target_dir):
        return []

    results: list[dict[str, Any]] = []
    try:
        filenames = os.listdir(target_dir)
    except OSError:
        return []

    for fname in sorted(filenames):
        if not fname.endswith((".txt", ".tmp")) or not fname.startswith(
            "comment_draft_"
        ):
            continue

        fpath = os.path.join(target_dir, fname)

        # Extract case number from filename: comment_draft_{case}_{suffix}.txt
        match = re.match(r"^comment_draft_([^_]+)_", fname)
        c_num = match.group(1) if match else "Unknown"

        if case_number and str(case_number).strip() not in (c_num, c_num.lstrip("0")):
            continue

        try:
            content, stat = read_draft(fpath)
        except (OSError, UnicodeError):
            continue

        snippet = ""
        for line in content.splitlines():
            line_str = line.strip()
            if line_str and not line_str.startswith("#"):
                snippet = line_str[:80] + ("..." if len(line_str) > 80 else "")
                break

        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        results.append(
            {
                "file": fname,
                "path": fpath,
                "case_number": c_num,
                "size_bytes": stat.st_size,
                "modified": mtime,
                "mtime_ns": stat.st_mtime_ns,
                "identity": draft_identity(stat),
                "snippet": snippet or "(empty draft)",
                "content": content,
            }
        )

    # Sort newest first
    results.sort(key=lambda x: x["mtime_ns"], reverse=True)
    return results


def cmd_drafts(args: Any, config: dict | None = None) -> int:
    """Dispatcher for `drafts` commands: list, show, clear."""
    drafts_dir = resolve_drafts_dir(config)
    subcmd = getattr(args, "draft_action", "list") or "list"
    target_case = getattr(args, "case", None)
    fmt = "json" if getattr(args, "json", False) else getattr(args, "format", "text")
    structured = fmt in ("json", "yaml")

    if subcmd == "list":
        drafts = find_drafts(case_number=target_case, drafts_dir=drafts_dir)
        if structured:
            print(format_structured_output(drafts, fmt=fmt))
            return 0

        if not drafts:
            case_msg = f" for Case #{target_case}" if target_case else ""
            print(f"No saved comment drafts found{case_msg} in {drafts_dir}")
            return 0

        if getattr(args, "simple_output", False):
            print(f"SAVED COMMENT DRAFTS ({len(drafts)})\n" + "=" * 80)
            for d in drafts:
                print(f"Case #{d['case_number']:<10} │ {d['modified']} │ {d['file']}")
                print(f"  Preview: {d['snippet']}\n")
        else:
            from rich.box import ROUNDED
            from rich.console import Console
            from rich.table import Table

            console = Console()
            table = Table(
                title=f"Saved Comment Drafts ({len(drafts)})",
                box=ROUNDED,
                header_style="bold cyan",
            )
            table.add_column("Case #", style="bold yellow", width=12)
            table.add_column("Modified", style="dim", width=20)
            table.add_column("Size", justify="right", width=10)
            table.add_column("Preview", style="white")

            for d in drafts:
                table.add_row(
                    d["case_number"],
                    d["modified"],
                    f"{d['size_bytes']} B",
                    d["snippet"],
                )
            console.print(table)
        return 0

    if subcmd == "show":
        identifier = getattr(args, "identifier", None)
        if not identifier:
            sys.exit(
                "Error: Draft identifier or filename is required (run 'rh-support-cli drafts list')."
            )

        # Never let a partial filename select a different case over an exact ID.
        drafts = find_drafts(drafts_dir=drafts_dir)
        matches = [d for d in drafts if d["file"] == identifier]
        if not matches:
            matches = [
                d
                for d in drafts
                if (d["case_number"].lstrip("0") or "0")
                == (identifier.lstrip("0") or "0")
            ]
        if not matches:
            matches = [d for d in drafts if identifier in d["file"]]
        if len(matches) > 1:
            sys.exit(
                f"Error: Ambiguous draft identifier '{identifier}'; use an exact filename."
            )
        if not matches:
            sys.exit(f"Error: No draft matching '{identifier}' found in {drafts_dir}")
        match = matches[0]

        if structured:
            print(format_structured_output(match, fmt=fmt))
            return 0

        print(
            f"Draft: {match['file']} (Case #{match['case_number']} | {match['modified']})\n"
            + "-" * 70
        )
        print(match["content"].strip())
        return 0

    if subcmd in ("clear", "clean", "rm"):
        drafts = find_drafts(case_number=target_case, drafts_dir=drafts_dir)
        if not drafts:
            if structured:
                print(
                    format_structured_output(
                        {"deleted": 0, "skipped": 0, "cancelled": False}, fmt=fmt
                    )
                )
            else:
                print("No drafts to delete.")
            return 0

        if not getattr(args, "yes", False):
            case_info = f" for Case #{target_case}" if target_case else ""
            with redirect_stdout(sys.stderr if structured else sys.stdout):
                confirm = input(
                    f"Delete {len(drafts)} saved comment draft(s){case_info}? [y/N]: "
                )
            if confirm.lower() not in ("y", "yes"):
                if structured:
                    print(
                        format_structured_output(
                            {"deleted": 0, "skipped": 0, "cancelled": True}, fmt=fmt
                        )
                    )
                else:
                    print("Operation cancelled.")
                return 0

        deleted = 0
        skipped = 0
        for d in drafts:
            try:
                with draft_lock(os.path.join(drafts_dir, ".creation")) as gate:
                    if not gate:
                        skipped += 1
                        continue
                    with draft_lock(d["path"]) as acquired:
                        if acquired and remove_draft(d["path"], d["identity"]):
                            deleted += 1
                        else:
                            skipped += 1
            except OSError:
                skipped += 1

        if structured:
            print(
                format_structured_output(
                    {"deleted": deleted, "skipped": skipped, "cancelled": False},
                    fmt=fmt,
                )
            )
        else:
            print(f"✓ Deleted {deleted} saved comment draft(s).")
            if skipped:
                print(f"Skipped {skipped} active, changed, or unavailable draft(s).")
        return 0

    return 0
