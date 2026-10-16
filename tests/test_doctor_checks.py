"""Sandboxed, offline regressions for individual doctor checks."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rh_support_lib.commands import doctor


class DoctorCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(self.home / "config"),
                "XDG_CACHE_HOME": str(self.home / "cache"),
                "XDG_STATE_HOME": str(self.home / "state"),
            },
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_cache_probe_preserves_existing_file_and_symlink_target(self):
        cache = self.home / "cache" / "rh-support-cli"
        cache.mkdir(parents=True)
        probe = cache / ".perm_test"
        target = self.home / "precious"
        target.write_text("keep me")
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                if symlink:
                    probe.symlink_to(target)
                else:
                    probe.write_text("existing probe")
                self.assertEqual(doctor.check_disk_cache()["status"], "PASS")
                self.assertTrue(probe.exists())
                self.assertEqual(target.read_text(), "keep me")
                self.assertEqual(
                    probe.read_text(), "keep me" if symlink else "existing probe"
                )
                self.assertEqual(list(cache.iterdir()), [probe])
                probe.unlink()
