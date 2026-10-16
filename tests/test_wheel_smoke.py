"""Offline installed-wheel smoke tests verifying packaging, data files, and entry points outside the source checkout."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
import zipfile
from email.parser import Parser

from tests.support.wheel_environment import copy_runtime_dependencies


class WheelSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="rh-wheel-test-")
        cls.addClassCleanup(cls.temp_dir.cleanup)
        cls.wheel_dir = os.path.join(cls.temp_dir.name, "wheels")
        cls.install_dir = os.path.join(cls.temp_dir.name, "installed")
        cls.guard_dir = os.path.join(cls.temp_dir.name, "guard")
        os.mkdir(cls.guard_dir)
        for name in ("sitecustomize.py", "network_guard.py"):
            shutil.copy2(
                os.path.join(cls.repo_root, "tests", "support", name), cls.guard_dir
            )
        os.makedirs(cls.wheel_dir, exist_ok=True)
        build_source = os.path.join(cls.temp_dir.name, "source")
        os.mkdir(build_source)
        for name in ("setup.py", "pyproject.toml", "MANIFEST.in"):
            source = os.path.join(cls.repo_root, name)
            if os.path.isfile(source):
                shutil.copy2(source, build_source)
        shutil.copytree(
            os.path.join(cls.repo_root, "rh_support_lib"),
            os.path.join(build_source, "rh_support_lib"),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        res = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-index",
                "--no-build-isolation",
                "--no-deps",
                "-w",
                cls.wheel_dir,
                build_source,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(
                "Mandatory wheel build failed; install pip, setuptools and wheel "
                f"in the test environment.\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
            )
        wheels = [f for f in os.listdir(cls.wheel_dir) if f.endswith(".whl")]
        if not wheels:
            cls.temp_dir.cleanup()
            raise RuntimeError("No wheel found in build directory.")
        cls.wheel_path = os.path.join(cls.wheel_dir, wheels[0])

        # Install the artifact, not a source/editable distribution, entirely offline.
        with zipfile.ZipFile(cls.wheel_path, "r") as zf:
            cls.wheel_members = zf.namelist()
            metadata_name = next(
                n for n in cls.wheel_members if n.endswith("/METADATA")
            )
            cls.metadata_text = zf.read(metadata_name).decode("utf-8")
        venv.EnvBuilder(with_pip=True).create(cls.install_dir)
        cls.installed_python = os.path.join(cls.install_dir, "bin", "python")
        cls.console_script = os.path.join(cls.install_dir, "bin", "rh-support-cli")
        site_packages = os.path.join(
            cls.install_dir,
            "lib",
            f"python{sys.version_info.major}.{sys.version_info.minor}",
            "site-packages",
        )
        requirements = Parser().parsestr(cls.metadata_text).get_all("Requires-Dist", [])
        copy_runtime_dependencies(requirements, site_packages)
        result = subprocess.run(
            [
                cls.installed_python,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                cls.wheel_path,
            ],
            cwd=cls.temp_dir.name,
            env=cls._installed_env(),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(
                f"Offline wheel install failed: {result.stdout}\n{result.stderr}"
            )

    @classmethod
    def _installed_env(cls):
        env = os.environ.copy()
        env["PYTHONPATH"] = cls.guard_dir
        env["PYTHONNOUSERSITE"] = "1"
        env["HOME"] = cls.temp_dir.name
        env["XDG_CONFIG_HOME"] = os.path.join(cls.temp_dir.name, "config")
        env["XDG_CACHE_HOME"] = os.path.join(cls.temp_dir.name, "cache")
        env["XDG_STATE_HOME"] = os.path.join(cls.temp_dir.name, "state")
        return env

    def _run_installed_python(self, code: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.installed_python, "-c", code],
            cwd=self.temp_dir.name,
            env=self._installed_env(),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_wheel_excludes_test_package_and_fixtures(self):
        """Verify the built wheel does not package tests/ or private test fixtures."""
        for name in self.wheel_members:
            self.assertFalse(
                name.startswith(("tests/", "tests\\")),
                f"Wheel should not contain tests package: {name}",
            )
            self.assertFalse(
                name.endswith("test_rh_support_cli.py"),
                f"Wheel contains test file: {name}",
            )

    def test_installed_child_retains_loopback_guard(self):
        code = """
import socket
import sitecustomize
assert hasattr(sitecustomize, "_guard"), "Child lost sandbox network guard"
for operation in (
    lambda: socket.getaddrinfo("example.invalid", 443),
    lambda: socket.socket().connect(("192.0.2.1", 443)),
    lambda: socket.socket().connect_ex(("192.0.2.1", 443)),
):
    try:
        operation()
    except RuntimeError as exc:
        assert "Offline tests blocked" in str(exc)
    else:
        raise AssertionError("Non-loopback operation was allowed")
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    with socket.create_connection(listener.getsockname(), timeout=2):
        connection, _ = listener.accept()
        connection.close()
print("GUARDED")
"""
        result = self._run_installed_python(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GUARDED", result.stdout)

    def test_wheel_contains_bundled_product_catalog_and_metadata(self):
        """Verify the built wheel includes the bundled catalog data file and valid packaging metadata."""
        self.assertIn(
            "rh_support_lib/data/products.json",
            self.wheel_members,
            "Wheel must bundle rh_support_lib/data/products.json",
        )

        # Inspect wheel metadata
        metadata_files = [f for f in self.wheel_members if f.endswith("METADATA")]
        self.assertTrue(bool(metadata_files), "Wheel must contain dist-info/METADATA")
        meta_text = self.metadata_text
        self.assertIn("Requires-Python: >=3.9", meta_text)
        self.assertIn("Requires-Dist: markdown-it-py", meta_text)

    def test_installed_wheel_imports_and_reads_bundled_catalog(self):
        """Verify installed wheel can import rh_support_lib and read bundled catalog without git repo."""
        code = """
import rh_support_lib
from rh_support_lib.completers import DEFAULT_FALLBACK_PRODUCTS, product_completer
import os
import sys
from importlib import metadata, resources, util

prods = product_completer("Red")
assert len(prods) > 0, "Product completer must return products"
assert any("Enterprise Linux" in p or "Enterprise\\ Linux" in p for p in prods)

# Ensure it was imported from installed target, not checkout
installed_file = os.path.abspath(rh_support_lib.__file__)
assert "installed" in installed_file, f"Imported from unexpected location: {installed_file}"
assert os.path.commonpath((sys.prefix, installed_file)) == sys.prefix
assert util.find_spec("tests") is None, "Source tests leaked into installed environment"
assert not any("rh-support-cli" in p for p in sys.path), sys.path
assert metadata.version("rh-support-cli") == "0.1.0"
assert resources.files("rh_support_lib").joinpath("data/products.json").is_file()
print("OK")
"""
        res = self._run_installed_python(code)
        self.assertEqual(
            res.returncode,
            0,
            f"Installed wheel import failed:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}",
        )
        self.assertIn("OK", res.stdout)

    def test_installed_wheel_markdown_rich_rendering(self):
        """Verify installed wheel can execute markdown-it-py portal formatting cleanly."""
        code = """
from rh_support_lib.utils import format_body_for_portal

html = format_body_for_portal("Test **bold** comment\\n\\nNew paragraph")
assert "<p>" in html
assert "<strong>bold</strong>" in html
print("OK")
"""
        res = self._run_installed_python(code)
        self.assertEqual(
            res.returncode,
            0,
            f"Markdown rendering failed:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}",
        )
        self.assertIn("OK", res.stdout)

    def test_installed_wheel_console_entry_point(self):
        """Execute pip's generated console script, not a direct call to main."""
        res = subprocess.run(
            [self.console_script, "completion"],
            check=False,
            cwd=self.temp_dir.name,
            env=self._installed_env(),
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            res.returncode,
            0,
            f"Console entry point execution failed:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}",
        )
        self.assertIn("register-python-argcomplete", res.stdout)
