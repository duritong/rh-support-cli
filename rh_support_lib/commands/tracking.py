from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys

from rh_support_lib.constants import COLORS
from rh_support_lib.services.cases import CaseService
from rh_support_lib.utils import colorize


def cmd_sync(args, api_client, config=None):
    """Syncs tracked cases against remote GraphQL metadata, detecting updates and new comments."""
    # 1. Systemd unit generator
    if getattr(args, "install_systemd", False):
        install_systemd_timer(interval=getattr(args, "interval", "15m"))
        return

    case_service = CaseService(api_client)
    user = getattr(args, "user", None) or case_service.get_current_user()
    acc = getattr(args, "account", None)
    fmt = getattr(args, "format", "text")
    is_structured = fmt in ("json", "yaml") or getattr(args, "json", False)

    if not is_structured:
        print(f"Syncing tracked cases for '{user}'...")
    try:
        res = case_service.sync_tracked_cases(username=user, account=acc)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Network Error during sync: {e}")

    if is_structured:
        from rh_support_lib.utils import format_structured_output

        print(format_structured_output(res, fmt="yaml" if fmt == "yaml" else "json"))
        return
    synced_count = res.get("synced_count", 0)
    updated = res.get("updated_cases", [])
    new_comments = res.get("new_comments", 0)

    c_green = colorize(
        f"{len(updated)} updated", COLORS.GREEN, getattr(args, "simple_output", False)
    )
    print(
        f"Sync complete: {synced_count} tracked case(s) checked. "
        f"{c_green} ({new_comments} new comment(s))."
    )

    # Optional desktop notifications
    if getattr(args, "notify", False) and updated:
        send_desktop_notification(
            title="Red Hat Support CLI",
            message=f"{len(updated)} case(s) updated with {new_comments} new comment(s).",
        )


def cmd_updates(args, api_client, config=None):
    """Displays unread updates and new comments across tracked cases."""
    case_service = CaseService(api_client)
    user = getattr(args, "user", None) or case_service.get_current_user()
    state_service = case_service.state_service

    all_tracked = state_service.get_tracked_cases(user)
    if not all_tracked:
        print(
            "No cases currently tracked. Follow cases with 'rh-support-cli follow <case>'."
        )
        return

    # Filter to specific case if requested
    target_case = getattr(args, "case", None)
    if target_case:
        target_case = str(target_case).strip()
        if target_case not in all_tracked:
            print(f"Case #{target_case} is not currently tracked.")
            return
        cases_to_check = [all_tracked[target_case]]
    else:
        cases_to_check = list(all_tracked.values())

    # Filter to updated cases (unless --all is specified)
    show_all = getattr(args, "all", False)
    if not show_all:
        cases_to_check = [
            tc
            for tc in cases_to_check
            if tc.latest_known.unread_comment_count > 0
            or tc.latest_known.status_changed
        ]
    mark_read_flag = bool(getattr(args, "mark_read", False))
    fmt = getattr(args, "format", "text")
    is_structured = fmt in ("json", "yaml") or getattr(args, "json", False)

    if is_structured:
        from rh_support_lib.utils import format_structured_output

        print(
            format_structured_output(
                cases_to_check, fmt="yaml" if fmt == "yaml" else "json"
            )
        )
        if mark_read_flag:
            for tc in cases_to_check:
                state_service.mark_case_read(user, tc.case_number)
        return

    if not cases_to_check:
        print("No unread updates across tracked cases.")
        return
    full_output = getattr(args, "full", False)
    simple = getattr(args, "simple_output", False)

    for tc in cases_to_check:
        c_num = tc.case_number
        lk = tc.latest_known
        cp = tc.read_checkpoint

        title_str = colorize(f"Case #{c_num}", COLORS.BOLD, simple)
        sum_str = f" — {tc.summary}" if tc.summary else ""
        print(f"\n{title_str}{sum_str}")
        print(
            f"Status: {lk.status or cp.status or 'Unknown'} │ Relationship: {tc.relationship.title()}"
        )

        if lk.status_changed and cp.status and lk.status:
            old_s = colorize(cp.status, COLORS.YELLOW, simple)
            new_s = colorize(lk.status, COLORS.GREEN, simple)
            print(f"  ⚡ Status Changed: {old_s} ➔ {new_s}")

        if lk.unread_comment_count > 0:
            c_badge = colorize(
                f"{lk.unread_comment_count} new comment(s)", COLORS.CYAN, simple
            )
            since_time = (
                cp.marked_read_at or cp.last_read_comment_date or "last checkpoint"
            )
            print(f"  💬 {c_badge} since {since_time}:")

            if full_output:
                # Fetch live case details to render full unread comments
                try:
                    details = case_service.get_case_details(
                        c_num, first_comments=50, all_comments=False
                    )
                    if details and details.comments:
                        last_read_date = cp.last_read_comment_date or ""
                        last_read_id = cp.last_read_comment_id or ""
                        for comm in details.comments:
                            is_new = False
                            if (
                                last_read_date
                                and comm.created_date > last_read_date
                                or not last_read_date
                                and last_read_id
                                and comm.id != last_read_id
                            ):
                                is_new = True
                            if is_new:
                                author = colorize(comm.created_by, COLORS.BOLD, simple)
                                vis = f"({comm.visibility})" if comm.visibility else ""
                                print(
                                    f"    ╭── {author} {vis} on {comm.created_date} ──"
                                )
                                for line in comm.body.splitlines():
                                    print(f"    │ {line}")
                                print("    ╰────────────────────────────────")
                except Exception as e:  # noqa: BLE001
                    print(f"    (Failed to load full comments: {e})")
            else:
                author = lk.latest_comment_author or "Author"
                date_str = lk.latest_comment_date or "Recently"
                snippet = lk.latest_comment_snippet or "New comment activity"
                print(f"    • Latest by {author} ({date_str}):")
                print(f'      "{snippet}"')

    # Advance checkpoints if --mark-read is passed
    if getattr(args, "mark_read", False):
        for tc in cases_to_check:
            state_service.mark_case_read(user, tc.case_number)
        print(f"\n✓ Marked {len(cases_to_check)} case(s) as read.")


def send_desktop_notification(title: str, message: str) -> None:
    """Invokes notify-send if installed on the host system."""
    notify_send_bin = shutil.which("notify-send")
    if notify_send_bin:
        with contextlib.suppress(Exception):
            subprocess.run(
                [notify_send_bin, "-a", "rh-support-cli", title, message],
                check=False,
                capture_output=True,
            )


def install_systemd_timer(interval: str = "15m") -> None:
    """Generates and installs systemd user service and timer units."""
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(unit_dir, exist_ok=True)

    cli_bin = sys.argv[0]
    if not os.path.isabs(cli_bin):
        cli_bin = shutil.which(cli_bin) or os.path.abspath(cli_bin)

    service_content = f"""[Unit]
Description=Red Hat Support Case Periodic Sync Service
After=network-online.target

[Service]
Type=oneshot
ExecStart={cli_bin} sync
Environment=PYTHONUNBUFFERED=1
"""

    timer_content = f"""[Unit]
Description=Periodic Red Hat Support Case Sync Timer

[Timer]
OnBootSec=2min
OnUnitActiveSec={interval}
Persistent=true

[Install]
WantedBy=timers.target
"""

    service_path = os.path.join(unit_dir, "rh-support-sync.service")
    timer_path = os.path.join(unit_dir, "rh-support-sync.timer")

    with open(service_path, "w") as f:
        f.write(service_content)
    with open(timer_path, "w") as f:
        f.write(timer_content)

    print(f"Installed systemd user service: {service_path}")
    print(f"Installed systemd user timer:   {timer_path} (interval: {interval})\n")
    print("To enable and start the timer, run:")
    print("  systemctl --user daemon-reload")
    print("  systemctl --user enable --now rh-support-sync.timer")
