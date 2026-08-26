"""P0 composition hygiene: the assembly root must be statically resolvable.

P0 finding: ``Application._wire_services`` referenced
``ThermalStateRepository`` without importing it anywhere in scope — a
latent NameError on the composed runtime-activation barrier that only a
runtime workflow would hit. This guard walks the symbol table of
``app.py`` and proves every referenced name resolves through the closure
chain (parameters, assignments, imports) or module/builtin scopes, so a
missing import in any production closure fails a test instead of an
operator's recovery path.
"""

from __future__ import annotations

import builtins
import symtable
from pathlib import Path

APP_SOURCE = (Path(__file__).parent.parent / "bc250_llm_mode" / "app.py").read_text(
    encoding="utf-8"
)

_BUILTINS = frozenset(dir(builtins))


def _bound_names(scope: symtable.SymbolTable) -> set[str]:
    return {
        symbol.get_name()
        for symbol in scope.get_symbols()
        if symbol.is_parameter() or symbol.is_assigned() or symbol.is_imported()
    }


def _unresolved(scope: symtable.SymbolTable, inherited: set[str], out: list) -> None:
    if scope.get_type() != "module":
        chain = inherited | _bound_names(scope)
        for symbol in scope.get_symbols():
            name = symbol.get_name()
            if not symbol.is_referenced():
                continue
            if name in _BUILTINS or name in chain:
                continue
            # is_global covers real module-level imports and builtins; a
            # name that is neither bound locally nor global is exactly the
            # latent NameError this guard exists for.
            if symbol.is_global():
                continue
            out.append((scope.get_name(), name))
    else:
        chain = inherited
    for child in scope.get_children():
        if child.get_type() == "module":
            continue
        _unresolved(child, chain, out)


def test_every_reference_in_composition_resolves():
    table = symtable.symtable(APP_SOURCE, "bc250_llm_mode/app.py", "exec")
    unresolved: list[tuple[str, str]] = []
    _unresolved(table, set(), unresolved)
    assert not unresolved, (
        "names referenced but never bound in any enclosing scope "
        f"(NameError at call time): {sorted(unresolved)}"
    )


def test_the_thermal_barrier_specifically_binds_its_repository():
    """Regression pin for the P0 finding itself."""
    assert "ThermalStateRepository" in APP_SOURCE
    assert "from .repositories import ThermalStateRepository" in APP_SOURCE
