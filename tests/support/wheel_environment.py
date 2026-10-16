"""Populate a clean wheel-test venv from already provisioned runtime dependencies."""

import os
import shutil
from importlib.metadata import distribution
from pathlib import Path

from pip._vendor.packaging.requirements import Requirement


def copy_runtime_dependencies(requirements, target):
    """Copy distribution-owned files only, never editable paths or .pth hooks."""
    pending = [Requirement(value) for value in requirements]
    visited = set()
    while pending:
        requirement = pending.pop()
        key = (requirement.name.lower(), frozenset(requirement.extras))
        if key in visited:
            continue
        visited.add(key)
        dist = distribution(requirement.name)
        if not requirement.specifier.contains(dist.version):
            raise RuntimeError(f"Provisioned dependency does not satisfy {requirement}")
        root = Path(dist.locate_file("")).resolve()
        for item in dist.files or ():
            source = Path(dist.locate_file(item)).resolve()
            if not source.is_file() or source.suffix == ".pyc":
                continue
            if source.suffix == ".pth" or os.path.commonpath((root, source)) != str(
                root
            ):
                continue
            destination = Path(target, item)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        for value in dist.requires or ():
            child = Requirement(value)
            if child.marker is None or any(
                child.marker.evaluate({"extra": extra})
                for extra in requirement.extras or {""}
            ):
                pending.append(child)
