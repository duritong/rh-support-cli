"""Focused tests for systemd timer and service unit generation."""

from __future__ import annotations

import os
import shutil
import tempfile

from tests.support.mock_server import MockServerTestCase


class TestCliSystemd(MockServerTestCase):
    def test_sync_command_and_systemd(self):
        """Test sync CLI command and --install-systemd unit generation."""

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        orig_state = self.env.get("XDG_STATE_HOME")
        self.env["HOME"] = temp_home
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_home, ".config")
        self.env["XDG_STATE_HOME"] = os.path.join(temp_home, ".local", "state")
        try:
            # 1. Follow a case first
            res_f = self.run_cli(["follow", "12345"])
            self.assertEqual(res_f.returncode, 0)

            # 2. Run sync command
            res_sync = self.run_cli(["sync"])
            self.assertEqual(res_sync.returncode, 0)
            self.assertIn("Syncing tracked cases for", res_sync.stdout)
            self.assertIn("Sync complete:", res_sync.stdout)

            # 3. Run sync --install-systemd
            res_systemd = self.run_cli(
                ["sync", "--install-systemd", "--interval", "10m"]
            )
            self.assertEqual(res_systemd.returncode, 0)
            self.assertIn("Installed systemd user service:", res_systemd.stdout)
            self.assertIn("Installed systemd user timer:", res_systemd.stdout)
            service_path = os.path.join(
                temp_home, ".config", "systemd", "user", "rh-support-sync.service"
            )
            timer_path = os.path.join(
                temp_home, ".config", "systemd", "user", "rh-support-sync.timer"
            )
            self.assertTrue(os.path.exists(service_path))
            self.assertTrue(os.path.exists(timer_path))
            with open(timer_path) as f:
                content = f.read()
                self.assertIn("OnUnitActiveSec=10m", content)
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            if orig_state:
                self.env["XDG_STATE_HOME"] = orig_state
            shutil.rmtree(temp_home)

    def test_systemd_install_and_management_commands(self):
        """Test dedicated install-systemd and systemd install/status/uninstall commands."""

        temp_home = tempfile.mkdtemp()
        orig_home = self.env.get("HOME")
        orig_config = self.env.get("XDG_CONFIG_HOME")
        self.env["HOME"] = temp_home
        self.env["XDG_CONFIG_HOME"] = os.path.join(temp_home, ".config")
        service_path = os.path.join(
            temp_home, ".config", "systemd", "user", "rh-support-sync.service"
        )
        timer_path = os.path.join(
            temp_home, ".config", "systemd", "user", "rh-support-sync.timer"
        )
        try:
            # 1. install-systemd with --interval 30m and --notify
            res_install = self.run_cli(
                ["install-systemd", "--interval", "30m", "--notify"]
            )
            self.assertEqual(res_install.returncode, 0)
            self.assertIn("Installed systemd user service:", res_install.stdout)
            self.assertIn("Installed systemd user timer:", res_install.stdout)
            self.assertIn("Desktop notifications:", res_install.stdout)
            self.assertTrue(os.path.exists(service_path))
            self.assertTrue(os.path.exists(timer_path))
            with open(service_path) as f:
                s_content = f.read()
                self.assertIn("--notify", s_content)
            with open(timer_path) as f:
                t_content = f.read()
                self.assertIn("OnUnitActiveSec=30m", t_content)

            # 2. systemd status
            res_status = self.run_cli(["systemd", "status"])
            self.assertEqual(res_status.returncode, 0)
            self.assertIn("Timer unit:   Installed", res_status.stdout)
            self.assertIn("Service unit: Installed", res_status.stdout)

            # 3. systemd uninstall
            res_uninstall = self.run_cli(["systemd", "uninstall"])
            self.assertEqual(res_uninstall.returncode, 0)
            self.assertIn("uninstalled", res_uninstall.stdout)
            self.assertFalse(os.path.exists(service_path))
            self.assertFalse(os.path.exists(timer_path))

            # 4. systemd install subcommand
            res_sub_install = self.run_cli(["systemd", "install", "--interval", "5m"])
            self.assertEqual(res_sub_install.returncode, 0)
            self.assertTrue(os.path.exists(timer_path))
            with open(timer_path) as f:
                self.assertIn("OnUnitActiveSec=5m", f.read())
        finally:
            if orig_home:
                self.env["HOME"] = orig_home
            if orig_config:
                self.env["XDG_CONFIG_HOME"] = orig_config
            shutil.rmtree(temp_home)
