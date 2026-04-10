"""ADO YAML compiler.

Purpose
- Compile Azure DevOps YAML into a resolved form that is easier to transform
  into GitHub Actions.
- Expand templates (inline + extends + resources repositories).
- Evaluate a safe subset of compile-time expressions.

This is not a full reimplementation of Azure DevOps template engine, but it is
structured so we can extend coverage while keeping behavior deterministic.

Key output
- resolved_yaml (string)
- template_units: per-template resolved docs so we can emit one workflow per template
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import yaml

from ado2gh.pipelines.template_resolver import TemplateResolution, resolve_templates


@dataclass(frozen=True)
class CompiledTemplateUnit:
    name: str
    path: str
    kind: str
    resolved_doc: dict[str, Any]


@dataclass(frozen=True)
class CompileResult:
    resolved_doc: dict[str, Any]
    resolved_yaml: str
    warnings: list[str]
    template_units: list[CompiledTemplateUnit]


def compile_ado_yaml(
    *,
    root_yaml_text: str,
    root_path: str,
    fetch_text: Callable[[str], str],
    fetch_text_with_alias: Callable[[dict, str], str] | None = None,
) -> CompileResult:
    """Compile ADO YAML into resolved form.

    For now this delegates to `resolve_templates` and additionally builds
    per-template units (parsed template YAML after parameter substitution).
    """
    res: TemplateResolution = resolve_templates(
        root_yaml_text=root_yaml_text,
        root_path=root_path,
        fetch_text=fetch_text,
        fetch_text_with_alias=fetch_text_with_alias,
    )

    # Re-parse each template node into a doc (applied parameters are already
    # baked into res.root_doc, but nodes carry raw source; we keep units small).
    template_units: list[CompiledTemplateUnit] = []
    for n in res.nodes:
        try:
            doc = yaml.safe_load(n.source_yaml) or {}
            if not isinstance(doc, dict):
                doc = {}
        except Exception:
            doc = {}
        template_units.append(
            CompiledTemplateUnit(
                name=n.name,
                path=n.path,
                kind=n.kind,
                resolved_doc=doc,
            )
        )

    resolved_yaml = yaml.safe_dump(res.root_doc, sort_keys=False)
    return CompileResult(
        resolved_doc=res.root_doc,
        resolved_yaml=resolved_yaml,
        warnings=list(res.warnings),
        template_units=template_units,
    )

