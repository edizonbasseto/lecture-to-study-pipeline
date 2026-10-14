"""Import-boundary guard for the LLM gateway package (ADR 0046, D-02).

Dependency rule: domain <- quality <- ops <- llm <- {cli, scripts}. The site
image never ships ``llm/``, ``ops/`` never imports it, ``llm/`` imports only a
fixed set of inner modules, and the inner modules import no adapter.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "lecture_study"
LLM_PACKAGE = "lecture_study.llm"
ALLOWED_LLM_IMPORT_PREFIXES = (
    "lecture_study.domain",
    "lecture_study.quality.prompt_budget",
    "lecture_study.ops.process_lifecycle",
    "lecture_study.ops.process_environment",
    "lecture_study.ops.llm_response_contract",
    "lecture_study.ops.strict_yaml",
    LLM_PACKAGE,
)
INNER_MODULE_FILE_NAMES = (
    "gateway.py",
    "json_completion.py",
    "config.py",
    "egress.py",
    "run_record.py",
)
ADAPTER_MODULES = (
    "lecture_study.llm.codex_adapter",
    "lecture_study.llm.llama_cpp_adapter",
    "lecture_study.llm.bounded_call",
    "lecture_study.llm.factory",
)
RELATIVE_IMPORT_MARKER = "<relative import>"


def _is_module_or_child(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _imported_modules(source_text: str) -> set[str]:
    """Return every absolute module name a source text imports."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source_text)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0 or node.module is None:
                modules.add(RELATIVE_IMPORT_MARKER)
                continue
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _source_files(subpackage: str) -> list[Path]:
    return sorted((PACKAGE_ROOT / subpackage).rglob("*.py"))


def _lecture_study_imports(path: Path) -> set[str]:
    imports = _imported_modules(path.read_text(encoding="utf-8"))
    return {
        module
        for module in imports
        if module == RELATIVE_IMPORT_MARKER
        or _is_module_or_child(module, "lecture_study")
    }


def test_scanner_reports_plain_and_from_imports() -> None:
    source_text = "import a.b\nfrom c import d\nfrom lecture_study import llm\n"

    assert _imported_modules(source_text) >= {
        "a.b",
        "c",
        "c.d",
        "lecture_study",
        "lecture_study.llm",
    }


def test_scanner_flags_relative_imports() -> None:
    assert RELATIVE_IMPORT_MARKER in _imported_modules("from . import gateway\n")


@pytest.mark.parametrize("subpackage", ["site", "llm"])
def test_scanned_packages_are_not_empty(subpackage: str) -> None:
    assert _source_files(subpackage)


def test_site_never_imports_the_llm_package() -> None:
    offenders = {
        path.name: sorted(
            module
            for module in _lecture_study_imports(path)
            if _is_module_or_child(module, LLM_PACKAGE)
        )
        for path in _source_files("site")
    }

    assert {name: modules for name, modules in offenders.items() if modules} == {}


def test_ops_never_imports_the_llm_package() -> None:
    offenders = {
        path.name: sorted(
            module
            for module in _lecture_study_imports(path)
            if _is_module_or_child(module, LLM_PACKAGE)
        )
        for path in _source_files("ops")
    }

    assert {name: modules for name, modules in offenders.items() if modules} == {}


def test_llm_modules_import_only_allowed_inner_modules() -> None:
    offenders = {
        path.name: sorted(
            module
            for module in _lecture_study_imports(path)
            if not any(
                _is_module_or_child(module, prefix)
                for prefix in ALLOWED_LLM_IMPORT_PREFIXES
            )
            and not any(
                _is_module_or_child(allowed, module)
                for allowed in ALLOWED_LLM_IMPORT_PREFIXES
            )
        )
        for path in _source_files("llm")
    }

    assert {name: modules for name, modules in offenders.items() if modules} == {}


def test_inner_llm_modules_import_no_adapter_module() -> None:
    inner_files = [
        path for path in _source_files("llm") if path.name in INNER_MODULE_FILE_NAMES
    ]
    offenders = {
        path.name: sorted(
            module
            for module in _lecture_study_imports(path)
            if any(_is_module_or_child(module, adapter) for adapter in ADAPTER_MODULES)
        )
        for path in inner_files
    }

    assert {name for name in INNER_MODULE_FILE_NAMES[:2]} <= {
        path.name for path in inner_files
    }
    assert {name: modules for name, modules in offenders.items() if modules} == {}


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
GENERATION_SCRIPT_NAMES = (
    "generate_codex_semantic_ledgers.py",
    "generate_codex_drafts.py",
    "generate_codex_critic_review.py",
    "generate_codex_mock_exam.py",
    "generate_codex_podcast.py",
    "generate_codex_personal_study_flashcards.py",
)
GATEWAY_WIRING_MODULE = "lecture_study.cli.llm_gateway_wiring"
PROCESS_SPAWNING_MODULES = (
    "tempfile",
    "subprocess",
    "lecture_study.ops.process_lifecycle",
    "lecture_study.ops.process_environment",
)


def test_generation_script_list_matches_the_scripts_on_disk() -> None:
    on_disk = {path.name for path in SCRIPTS_ROOT.glob("generate_codex_*.py")}

    assert on_disk == set(GENERATION_SCRIPT_NAMES)


@pytest.mark.parametrize("script_name", GENERATION_SCRIPT_NAMES)
def test_generation_scripts_reach_the_model_only_through_the_gateway(
    script_name: str,
) -> None:
    imports = _imported_modules((SCRIPTS_ROOT / script_name).read_text("utf-8"))

    spawning_imports = sorted(
        module
        for module in imports
        if any(
            _is_module_or_child(module, banned) for banned in PROCESS_SPAWNING_MODULES
        )
    )
    assert GATEWAY_WIRING_MODULE in imports
    assert spawning_imports == []
