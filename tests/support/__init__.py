"""Offline unittest support; importing this package does not enable isolation."""

from .sandbox import SandboxSuite

__all__ = ["SandboxSuite"]
