"""Synthetic demo lecture tooling for Phase 7 (ADR 0047).

This package holds the typed logic for the demo lecture source, its
measurement and its recovery evidence. Submodules are imported directly (for
example ``lecture_study.demo.script``); the package aggregates nothing, so
independent demo tools never import each other. Pipeline packages never import
this package.
"""

__all__: list[str] = []
