"""Lightweight detectors for ADO YAML pipeline patterns.

These detectors don't attempt to fully parse/expand templates. They exist to:
- help an AI agent understand what kind of pipeline it's looking at
- add actionable warnings/notes during migration

Supported detections:
- Azure DevOps YAML templates (`template:` usage)
- Bicep usage (`bicep` CLI, `*.bicep` file references)

Non-goal:
- full template expansion (requires repo checkout + path resolution)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PipelineSignals:
    uses_templates: bool = False
    template_refs: list[str] = field(default_factory=list)
    uses_bicep: bool = False
    bicep_refs: list[str] = field(default_factory=list)


_TEMPLATE_RE = re.compile(r"(^|\s)template\s*:\s*([^\s#]+)")
_BICEP_EXT_RE = re.compile(r"\b[^\s]+\.bicep\b", re.IGNORECASE)
_BICEP_CLI_RE = re.compile(r"\bbicep\s+(build|restore|publish|decompile)\b", re.IGNORECASE)
_AZ_DEPLOY_BICEP_RE = re.compile(r"az\s+deployment\s+(group|sub|mg|tenant)\s+create\b.*\b--template-file\s+[^\s]+\.bicep\b", re.IGNORECASE)


def detect_pipeline_signals(yaml_text: str) -> PipelineSignals:
    """Detect common execution/migration signals in ADO YAML content."""
    if not yaml_text:
        return PipelineSignals()

    template_refs = [m.group(2).strip().strip('"\'') for m in _TEMPLATE_RE.finditer(yaml_text)]
    uses_templates = bool(template_refs)

    bicep_refs = []
    bicep_refs.extend(m.group(0) for m in _BICEP_EXT_RE.finditer(yaml_text))
    uses_bicep = bool(bicep_refs) or bool(_BICEP_CLI_RE.search(yaml_text)) or bool(_AZ_DEPLOY_BICEP_RE.search(yaml_text))

    # de-dupe while preserving order
    def dedupe(items: list[str]) -> list[str]:
        seen = set()
        out = []
        for i in items:
            if i in seen:
                continue
            seen.add(i)
            out.append(i)
        return out

    return PipelineSignals(
        uses_templates=uses_templates,
        template_refs=dedupe(template_refs),
        uses_bicep=uses_bicep,
        bicep_refs=dedupe(bicep_refs),
    )

