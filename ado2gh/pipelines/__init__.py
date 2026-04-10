"""Pipeline subpackage.

Keep imports lightweight so core mapping utilities (e.g., PipelineTransformer)
can be imported without optional runtime dependencies.
"""

from __future__ import annotations

from ado2gh.pipelines.extractor import PipelineMetadataExtractor
from ado2gh.pipelines.transformer import PipelineTransformer

# Inventory builder requires optional CLI/UI deps (rich). Import lazily.
try:
    from ado2gh.pipelines.inventory import PipelineInventoryBuilder  # noqa: F401
except Exception:  # pragma: no cover
    PipelineInventoryBuilder = None  # type: ignore
