"""Shared fixtures for the demo tooling tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

DEMO_SITE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "demo_site.py"


@pytest.fixture
def demo_site() -> ModuleType:
    """The `scripts/demo_site.py` module, loaded by path (scripts are not a package)."""

    spec = importlib.util.spec_from_file_location("demo_site", DEMO_SITE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
