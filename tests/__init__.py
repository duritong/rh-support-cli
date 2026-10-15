"""Offline test package with sandboxed, nonduplicating discovery."""

from pathlib import Path

from .support import SandboxSuite


def load_tests(loader, standard_tests, pattern):
    directory = Path(__file__).resolve().parent
    return SandboxSuite(
        loader.discover(
            start_dir=str(directory),
            pattern=pattern or "test*.py",
            top_level_dir=str(directory.parent),
        )
    )
