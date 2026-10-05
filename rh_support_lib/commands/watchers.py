from __future__ import annotations

import sys

from rh_support_lib.services.cases import CaseService


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


def cmd_follow(args, api_client):
    """Attaches current user (or specified user) as watcher to one or more support cases.
    If --sync is specified without case numbers, syncs followed cases from account."""
    case_service = CaseService(api_client)
    user = getattr(args, "user", None) or case_service.get_current_user()

    case_numbers = _gather_cases(args)
    if not case_numbers:
        if getattr(args, "sync", False):
            acc = getattr(args, "account", None)
            print(f"Scanning account cases to sync watchers for '{user}'...")
            synced = case_service.sync_followed_cases(username=user, account=acc)
            print(
                f"Sync complete. Found {len(synced)} followed case(s): {', '.join(synced) if synced else 'None'}."
            )
            return
        sys.exit(
            "Error: Please specify at least one case number (e.g. follow 12345678 or -c 12345678) or use --sync."
        )

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


def cmd_unfollow(args, api_client):
    """Removes current user (or specified user) from watchers on one or more support cases."""
    case_numbers = _gather_cases(args)
    if not case_numbers:
        sys.exit(
            "Error: Please specify at least one case number (e.g. unfollow 12345678 or -c 12345678)."
        )

    case_service = CaseService(api_client)
    user = getattr(args, "user", None) or case_service.get_current_user()

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
