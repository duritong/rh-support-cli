from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys


def get_cli_binary() -> str:
    """Resolves the absolute path to the rh-support-cli executable."""
    cli_bin = sys.argv[0]
    if not os.path.isabs(cli_bin):
        cli_bin = shutil.which(cli_bin) or os.path.abspath(cli_bin)
    return cli_bin


def install_systemd_timer(
    interval: str = "15m",
    notify: bool = False,
    enable: bool = False,
) -> tuple[str, str]:
    """Generates and installs systemd user service and timer units."""
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(unit_dir, exist_ok=True)

    cli_bin = get_cli_binary()
    exec_args = f"{cli_bin} sync"
    if notify:
        exec_args += " --notify"

    service_content = f"""[Unit]
Description=Red Hat Support Case Periodic Sync Service
After=network-online.target

[Service]
Type=oneshot
ExecStart={exec_args}
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
    print(f"Installed systemd user timer:   {timer_path} (interval: {interval})")
    if notify:
        print("Desktop notifications:          enabled (--notify)")

    if enable:
        print("\nReloading systemd user daemon and starting timer...")
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", "rh-support-sync.timer"],
                check=True,
            )
            print("✓ rh-support-sync.timer enabled and running successfully.")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Warning: Failed to automatically start timer via systemctl: {e}")
            _print_manual_instructions()
    else:
        _print_manual_instructions()

    return service_path, timer_path


def _print_manual_instructions() -> None:
    print("\nTo enable and start the timer, run:")
    print("  systemctl --user daemon-reload")
    print("  systemctl --user enable --now rh-support-sync.timer")


def uninstall_systemd_timer() -> bool:
    """Stops, disables, and deletes systemd user service and timer units."""
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    service_path = os.path.join(unit_dir, "rh-support-sync.service")
    timer_path = os.path.join(unit_dir, "rh-support-sync.timer")

    # Stop and disable via systemctl if available
    with contextlib.suppress(Exception):
        subprocess.run(
            ["systemctl", "--user", "stop", "rh-support-sync.timer"], check=False
        )
        subprocess.run(
            ["systemctl", "--user", "disable", "rh-support-sync.timer"], check=False
        )

    removed = False
    for p in (timer_path, service_path):
        if os.path.exists(p):
            with contextlib.suppress(OSError):
                os.remove(p)
                removed = True

    with contextlib.suppress(Exception):
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)

    if removed:
        print(f"Removed systemd unit files from {unit_dir}.")
        print("✓ rh-support-sync timer and service uninstalled.")
    else:
        print(f"No installed systemd units found in {unit_dir}.")
    return removed


def status_systemd_timer() -> None:
    """Inspects status of the systemd user timer and service."""
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    timer_path = os.path.join(unit_dir, "rh-support-sync.timer")
    service_path = os.path.join(unit_dir, "rh-support-sync.service")

    print(f"Unit files directory: {unit_dir}")
    print(
        f"  Timer unit:   {'Installed' if os.path.exists(timer_path) else 'Not installed'}"
    )
    print(
        f"  Service unit: {'Installed' if os.path.exists(service_path) else 'Not installed'}\n"
    )

    if shutil.which("systemctl"):
        print("--- Timer Status (systemctl --user status rh-support-sync.timer) ---")
        subprocess.run(
            ["systemctl", "--user", "status", "rh-support-sync.timer"], check=False
        )
    else:
        print("Note: 'systemctl' command not found on host.")


def cmd_systemd(args) -> None:
    """Manages systemd timer and service units for background sync."""
    action = getattr(args, "systemd_action", None) or "install"

    if action == "install":
        interval = getattr(args, "interval", "15m") or "15m"
        notify = bool(getattr(args, "notify", False))
        enable = bool(getattr(args, "enable", False))
        install_systemd_timer(interval=interval, notify=notify, enable=enable)
    elif action in ("uninstall", "remove"):
        uninstall_systemd_timer()
    elif action == "status":
        status_systemd_timer()
    else:
        print(f"Unknown systemd action: {action}. Choices: install, uninstall, status.")


def cmd_install_systemd(args) -> None:
    """Shortcut command to install systemd user timer for regular sync."""
    interval = getattr(args, "interval", "15m") or "15m"
    notify = bool(getattr(args, "notify", False))
    enable = bool(getattr(args, "enable", False))
    install_systemd_timer(interval=interval, notify=notify, enable=enable)
