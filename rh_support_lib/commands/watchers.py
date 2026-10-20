from __future__ import annotations

import sys

from rh_support_lib.services.cases import CaseService
from rh_support_lib.utils import format_structured_output


def _gather_cases(args) -> list[str]:
    """Extracts case numbers from positional and flag arguments."""
    cases = []
    if getattr(args, "case", None):
        if isinstance(args.case, list):
            cases.extend(args.case)
        else:
            cases.append(args.case)
    if getattr(args, "cases", None):
        if isinstance(args.cases, list):
            cases.extend(args.cases)
        else:
            cases.append(args.cases)

    # Flatten and dedup
    result = []
    seen = set()
    for c in cases:
        c_str = str(c).strip()
        if c_str and c_str not in seen:
            seen.add(c_str)
            result.append(c_str)
    return result


def _resolve_account(args, config) -> str | None:
    """Resolves account number from positional arg, --account, or bookmarks."""
    from rh_support_lib.config import resolve_bookmarks

    acc = getattr(args, "account", None) or getattr(args, "account_pos", None)
    if acc:
        return str(acc).strip()

    filters = resolve_bookmarks(
        config,
        getattr(args, "bookmark", None),
        getattr(args, "no_default_bookmark", False),
    )
    res = filters.get("account") or filters.get("accountNumber")
    return str(res).strip() if res else None


def cmd_follow(args, api_client, config=None):
    """Attaches current user (or specified user) as watcher to one or more support cases.
    If --account is specified without case numbers, delegates to cmd_follow_account.
    If --sync is specified without case numbers, syncs followed cases from account."""
    case_numbers = _gather_cases(args)
    if not case_numbers:
        if getattr(args, "sync", False):
            case_service = CaseService(api_client, config=config)
            user = getattr(args, "user", None) or case_service.get_current_user()
            acc = getattr(args, "account", None)
            print(f"Scanning account cases to sync watchers for '{user}'...")
            synced = case_service.sync_followed_cases(username=user, account=acc)
            print(
                f"Sync complete. Found {len(synced)} followed case(s): {', '.join(synced) if synced else 'None'}."
            )
            return
        if getattr(args, "account", None) or getattr(args, "account_pos", None):
            return cmd_follow_account(args, api_client, config=config)
        sys.exit(
            "Error: Please specify at least one case number (e.g. follow 12345678 or -c 12345678), --account to follow all account cases, or use --sync."
        )

    case_service = CaseService(api_client, config=config)
    user = getattr(args, "user", None) or case_service.get_current_user()
    if getattr(args, "dry_run", False):
        for c_num in case_numbers:
            print(f"[Dry-run] Would follow Case #{c_num} for '{user}'.")
        return
    print(f"Adding '{user}' as watcher on {len(case_numbers)} case(s)...")
    has_error = False
    for c_num in case_numbers:
        try:
            case_service.follow_case(c_num, username=user)
            print(f"Success: Added '{user}' as watcher to Case #{c_num}.")
        except Exception as e:  # noqa: BLE001
            print(f"Error adding watcher to Case #{c_num}: {e}")
            has_error = True

    if has_error:
        sys.exit(1)


def cmd_following(args, api_client, config=None):
    """Lists cases currently followed by the user."""
    from rh_support_lib.commands.list_cases import cmd_list

    args.following = True
    cmd_list(args, api_client, config=config)


def cmd_unfollow(args, api_client, config=None):
    """Removes current user (or specified user) from watchers on one or more support cases.
    If --account is specified without case numbers, delegates to cmd_unfollow_account."""
    case_numbers = _gather_cases(args)
    if not case_numbers:
        if getattr(args, "account", None) or getattr(args, "account_pos", None):
            return cmd_unfollow_account(args, api_client, config=config)
        sys.exit(
            "Error: Please specify at least one case number (e.g. unfollow 12345678 or -c 12345678) or --account to unfollow account cases."
        )

    case_service = CaseService(api_client, config=config)
    user = getattr(args, "user", None) or case_service.get_current_user()

    if getattr(args, "dry_run", False):
        for c_num in case_numbers:
            print(f"[Dry-run] Would unfollow Case #{c_num} for '{user}'.")
        return
    print(f"Removing '{user}' from watchers on {len(case_numbers)} case(s)...")
    has_error = False
    for c_num in case_numbers:
        try:
            case_service.unfollow_case(c_num, username=user)
            print(f"Success: Removed '{user}' from watchers on Case #{c_num}.")
        except Exception as e:  # noqa: BLE001
            print(f"Error removing watcher from Case #{c_num}: {e}")
            has_error = True

    if has_error:
        sys.exit(1)


def cmd_follow_account(args, api_client, config=None):
    """Follows all matching cases for an account (by default: open cases)."""
    acc = _resolve_account(args, config)
    if not acc:
        sys.exit(
            "Error: Please specify an account number (e.g. follow-account 123456 or -a 123456) or configure an account in bookmarks."
        )

    case_service = CaseService(api_client, config=config)
    user = getattr(args, "user", None) or case_service.get_current_user()
    is_all = bool(getattr(args, "all", False))
    raw_status = "all" if is_all else (getattr(args, "status", None) or "open")
    severity = getattr(args, "severity", None)
    dry_run = bool(getattr(args, "dry_run", False))
    fmt = "json" if getattr(args, "json", False) else getattr(args, "format", "text")

    try:
        receipt = case_service.follow_account(
            account=acc,
            status=raw_status,
            severity=severity,
            username=user,
            dry_run=dry_run,
        )
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Error following account {acc}: {e}")

    if fmt in ("json", "yaml"):
        serializable = {
            "account": receipt["account"],
            "user": receipt["user"],
            "status_filter": receipt["status_filter"],
            "followed": receipt["followed"],
            "already_followed": receipt["already_followed"],
            "count": receipt["count"],
            "total_matching": receipt["total_matching"],
            "dry_run": receipt["dry_run"],
            "success": True,
        }
        print(format_structured_output(serializable, fmt=fmt))
        return

    matching = receipt["matching_cases"]
    followed = receipt["followed"]
    already = set(receipt["already_followed"])
    status_desc = "all" if is_all or raw_status == "all" else str(raw_status)
    action_verb = "Would follow" if dry_run else "Followed"
    prefix = "[Dry-run] " if dry_run else ""

    if not matching:
        print(f"No matching cases found for account {acc} (status: {status_desc}).")
        return

    print(
        f"{prefix}Following cases for account {acc} (User: {user}, Status: {status_desc})..."
    )
    for c in matching:
        c_num = str(c.case_number)
        status_part = f" ({c.status})" if c.status else ""
        summary_part = f": {c.summary}" if c.summary else ""
        if c_num in already:
            print(f"  • Already following Case #{c_num}{summary_part}{status_part}")
        else:
            marker = "-" if dry_run else "+"
            print(f"  {marker} {action_verb} Case #{c_num}{summary_part}{status_part}")

    if dry_run:
        print(
            f"[Dry-run] Would follow {len(followed)} case(s) for account {acc} ({len(already)} already followed)."
        )
    else:
        print(
            f"Success: Followed {len(followed)} case(s) for account {acc} ({len(already)} already followed)."
        )


def cmd_unfollow_account(args, api_client, config=None):
    """Unfollows cases belonging to an account, optionally filtered by status."""
    acc = _resolve_account(args, config)
    if not acc:
        sys.exit(
            "Error: Please specify an account number (e.g. unfollow-account 123456 or -a 123456) or configure an account in bookmarks."
        )

    case_service = CaseService(api_client, config=config)
    user = getattr(args, "user", None) or case_service.get_current_user()
    status = getattr(args, "status", None)
    dry_run = bool(getattr(args, "dry_run", False))
    fmt = "json" if getattr(args, "json", False) else getattr(args, "format", "text")

    try:
        receipt = case_service.unfollow_account(
            account=acc,
            status=status,
            username=user,
            dry_run=dry_run,
        )
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Error unfollowing account {acc}: {e}")

    if fmt in ("json", "yaml"):
        serializable = {
            "account": receipt["account"],
            "user": receipt["user"],
            "status_filter": receipt["status_filter"],
            "unfollowed": receipt["unfollowed"],
            "count": receipt["count"],
            "dry_run": receipt["dry_run"],
            "success": True,
        }
        print(format_structured_output(serializable, fmt=fmt))
        return

    unfollowed = receipt["unfollowed"]
    action_verb = "Would unfollow" if dry_run else "Unfollowed"
    prefix = "[Dry-run] " if dry_run else ""
    status_suffix = f" (Status: {status})" if status else ""

    if not unfollowed:
        print(f"No followed cases found for account {acc}{status_suffix}.")
        return

    print(
        f"{prefix}Unfollowing cases for account {acc} (User: {user}){status_suffix}..."
    )
    for c_num in unfollowed:
        marker = "-" if dry_run else "✓"
        print(f"  {marker} {action_verb} Case #{c_num}")

    if dry_run:
        print(f"[Dry-run] Would unfollow {len(unfollowed)} case(s) for account {acc}.")
    else:
        print(f"Success: Unfollowed {len(unfollowed)} case(s) for account {acc}.")
