"""Ensure `lark_integration` is importable when App source root is this package."""

from __future__ import annotations

import sys
import types
from pathlib import Path


def ensure_package_importable() -> Path:
    """Return package root and fix sys.path / namespace for App uploads.

    Local repo layout:  <repo>/lark_integration/...
    App upload layout:  <source_root>/{bot,bridge,app,...}  (folder may not be named lark_integration)
    """
    pkg_root = Path(__file__).resolve().parents[1]
    parent = pkg_root.parent

    if pkg_root.name == "lark_integration":
        if str(parent) not in sys.path:
            sys.path.insert(0, str(parent))
        return pkg_root

    # Flattened deploy: register this directory as the lark_integration package.
    if str(pkg_root) not in sys.path:
        sys.path.insert(0, str(pkg_root))
    if "lark_integration" not in sys.modules:
        pkg = types.ModuleType("lark_integration")
        pkg.__path__ = [str(pkg_root)]  # type: ignore[attr-defined]
        sys.modules["lark_integration"] = pkg
    if str(parent) not in sys.path:
        # Also allow `import bot` style if needed
        sys.path.insert(0, str(pkg_root))
    return pkg_root
