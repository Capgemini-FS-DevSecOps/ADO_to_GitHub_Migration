"""Regression check for register entry GAP-021 (GAP-ARCH-01): the package layers may not import upward.

``ado2gh`` is layered — ``models``/``http_utils`` at the bottom, then
``clients``/``state``, then ``core``/``phase``/``pipelines``, then ``api``, then
``cli``. Three inversions were measured during the 013 assessment and closed by
T078 by relocating the shared symbols down a layer:

* ``state → api`` — ``JobRecord``/``JobStatus``/``JobTypeEnum`` moved to
  ``ado2gh/models.py``; ``manual_phase_overrides``, ``pack_scan_summary_json``
  and ``extract_discovery_fields`` moved to ``ado2gh/state/scan_payload.py``.
* ``clients → core`` — ``get_thread_session`` moved to ``ado2gh/http_utils.py``.
* ``api → cli`` — ``load_repos`` moved to ``ado2gh/api/repo_input.py``.

Function-local ("deferred") imports count: hiding an upward import inside a
function body is what masked these edges in the first place, and
``_build_import_graph`` walks the whole AST, so they are caught here too.

Not asserted: ``core → api``. ``ado2gh/core/orchestration/worker.py`` is the
background job worker and its job *is* to drive the Accelerator SDK, so it
imports ``ado2gh.api.accelerator`` and ``ado2gh.api.contracts`` at module level;
``ado2gh/core/conflict_detection.py`` reaches for ``api.repo_lock`` and
``api.pipeline_store`` from inside two functions. Closing that edge means moving
the worker out of ``ado2gh/core/``, which T078 does not cover. It stays on the
GAP-021 register as a residual rather than being asserted with a carve-out for
its only violator, which would make the assertion meaningless.
"""
from __future__ import annotations

from tests.unit.test_no_orphaned_modules import _build_import_graph

# (importing layer, layer it must not import) — prefixes of dotted module names.
FORBIDDEN_EDGES = [
    ("ado2gh.state", "ado2gh.api"),
    ("ado2gh.clients", "ado2gh.core"),
    ("ado2gh.api", "ado2gh.cli"),
]


def _in_layer(module: str, layer: str) -> bool:
    """True when ``module`` is the layer package itself or lives inside it."""
    return module == layer or module.startswith(layer + ".")


def test_no_upward_imports_between_layers():
    """No module in a lower layer may import from a higher one (GAP-021)."""
    _modules, reverse = _build_import_graph()

    violations = []
    for lower, higher in FORBIDDEN_EDGES:
        for imported, importers in reverse.items():
            if not _in_layer(imported, higher):
                continue
            for importer in sorted(importers):
                if _in_layer(importer, lower):
                    violations.append(f"{importer} -> {imported}")

    assert not violations, (
        "Upward imports found (GAP-021); move the shared symbol down a layer "
        "rather than importing upward: " + ", ".join(sorted(violations))
    )
