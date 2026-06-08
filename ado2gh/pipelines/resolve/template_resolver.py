"""Resolve ADO pipeline template extends / - template: references."""
from __future__ import annotations

from typing import Any, Optional

import yaml

from ado2gh.logging_config import log


def resolve_templates(
    yaml_content: str,
    fetch_template: Optional[callable] = None,
    max_depth: int = 5,
) -> str:
    """Inline template references into a single YAML document.

    ``fetch_template`` receives ``(template_name, template_ref)`` and returns
    YAML text or None if unavailable.
    """
    if not (yaml_content or "").strip():
        return yaml_content

    try:
        doc = yaml.safe_load(yaml_content)
    except Exception:
        return yaml_content

    if not isinstance(doc, dict):
        return yaml_content

    merged = _merge_extends(doc, fetch_template, depth=0, max_depth=max_depth)
    return yaml.dump(merged, default_flow_style=False, sort_keys=False, allow_unicode=True)


def extract_template_refs(yaml_content: str) -> list[str]:
    """Return template names referenced via ``extends`` or ``- template:``."""
    if not (yaml_content or "").strip():
        return []

    try:
        doc = yaml.safe_load(yaml_content)
    except Exception:
        return []

    refs: list[str] = []
    _collect_refs(doc, refs)
    return sorted(set(refs))


def _collect_refs(obj: Any, out: list[str]) -> None:
    if isinstance(obj, dict):
        if "extends" in obj:
            ext = obj["extends"]
            if isinstance(ext, str):
                out.append(ext)
            elif isinstance(ext, dict):
                name = ext.get("template", ext.get("repository", ""))
                if name:
                    out.append(str(name))
        if "template" in obj and isinstance(obj["template"], str):
            out.append(obj["template"])
        for v in obj.values():
            _collect_refs(v, out)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and "template" in item:
                out.append(str(item["template"]))
            _collect_refs(item, out)


def _merge_extends(
    doc: dict,
    fetch_template: Optional[callable],
    depth: int,
    max_depth: int,
) -> dict:
    if depth >= max_depth:
        log.warning("template_resolver: max depth %d reached", max_depth)
        return doc

    extends = doc.get("extends")
    if not extends or not fetch_template:
        return doc

    template_name = extends if isinstance(extends, str) else extends.get("template", "")
    if not template_name:
        return doc

    template_yaml = fetch_template(template_name, extends)
    if not template_yaml:
        log.warning("template_resolver: could not fetch template %s", template_name)
        return doc

    try:
        base = yaml.safe_load(template_yaml)
    except Exception:
        return doc

    if not isinstance(base, dict):
        return doc

    base = _merge_extends(base, fetch_template, depth + 1, max_depth)
    merged = _deep_merge(base, doc)
    merged.pop("extends", None)
    return merged


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key == "extends":
            continue
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result
