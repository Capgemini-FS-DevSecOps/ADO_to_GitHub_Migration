"""Resolve ADO pipeline template extends / - template: references."""
from __future__ import annotations

import posixpath
from typing import Any, Callable, Optional

import yaml

from ado2gh.logging_config import log

TemplateFetcher = Callable[[str, Any, str], Optional[str]]


def resolve_templates(
    yaml_content: str,
    fetch_template: Optional[TemplateFetcher] = None,
    source_path: str = "",
    max_depth: int = 5,
) -> str:
    """Inline template references into a single YAML document.

    ``fetch_template`` receives ``(template_name, template_ref, source_path)``
    and returns YAML text or None if unavailable. ``source_path`` is the repo
    path of the YAML file that contains the reference (used for relative paths).
    """
    if not (yaml_content or "").strip():
        return yaml_content

    try:
        doc = yaml.safe_load(yaml_content)
    except Exception:
        return yaml_content

    if not isinstance(doc, dict):
        return yaml_content

    merged = _merge_extends(
        doc, fetch_template, source_path=source_path, depth=0, max_depth=max_depth,
    )
    if fetch_template:
        merged = _resolve_template_lists(
            merged, fetch_template, source_path=source_path, depth=0, max_depth=max_depth,
        )
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


def make_ado_git_fetcher(
    ado: Any,
    project: str,
    repo_id: str,
    branch: str,
    yaml_path: str = "",
) -> TemplateFetcher:
    """Build a fetcher that loads template YAML from an ADO Git repository."""

    def fetch(template_name: str, _template_ref: Any, source_path: str) -> Optional[str]:
        if not repo_id or not template_name:
            return None
        base = source_path or yaml_path or ""
        path = resolve_template_path(template_name, base)
        content = ado.get_pipeline_yaml_from_git(project, repo_id, path, branch=branch)
        if not (content or "").strip():
            log.warning(
                "template_resolver: template %s (resolved %s) not found in repo %s",
                template_name, path, repo_id,
            )
            return None
        return content

    return fetch


def resolve_template_path(template_name: str, source_path: str) -> str:
    """Resolve a template path relative to the referencing YAML file."""
    name = (template_name or "").strip()
    if not name:
        return ""
    if name.startswith("/"):
        return name.lstrip("/")
    if not source_path:
        return name
    base_dir = posixpath.dirname(source_path.lstrip("/"))
    if not base_dir or base_dir == ".":
        return name
    return posixpath.normpath(posixpath.join(base_dir, name))


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
    fetch_template: Optional[TemplateFetcher],
    source_path: str,
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

    template_yaml = fetch_template(str(template_name), extends, source_path)
    if not template_yaml:
        log.warning("template_resolver: could not fetch template %s", template_name)
        return doc

    try:
        base = yaml.safe_load(template_yaml)
    except Exception:
        return doc

    if not isinstance(base, dict):
        return doc

    template_path = resolve_template_path(str(template_name), source_path)
    base = _merge_extends(
        base, fetch_template, template_path, depth + 1, max_depth,
    )
    if fetch_template:
        base = _resolve_template_lists(
            base, fetch_template, template_path, depth + 1, max_depth,
        )
    merged = _deep_merge(base, doc)
    merged.pop("extends", None)
    return merged


def _resolve_template_lists(
    doc: Any,
    fetch_template: TemplateFetcher,
    source_path: str,
    depth: int,
    max_depth: int,
) -> Any:
    if depth >= max_depth:
        log.warning("template_resolver: max depth %d reached", max_depth)
        return doc

    if isinstance(doc, dict):
        result = dict(doc)
        for key in ("steps", "jobs", "stages"):
            if key in result and isinstance(result[key], list):
                result[key] = _expand_template_list(
                    result[key],
                    key,
                    fetch_template,
                    source_path,
                    depth,
                    max_depth,
                )
        for key, val in result.items():
            if key not in ("steps", "jobs", "stages"):
                result[key] = _resolve_template_lists(
                    val, fetch_template, source_path, depth, max_depth,
                )
        return result

    if isinstance(doc, list):
        return [
            _resolve_template_lists(item, fetch_template, source_path, depth, max_depth)
            for item in doc
        ]

    return doc


def _expand_template_list(
    items: list,
    list_kind: str,
    fetch_template: TemplateFetcher,
    source_path: str,
    depth: int,
    max_depth: int,
) -> list:
    expanded: list = []
    for item in items:
        if not isinstance(item, dict) or "template" not in item:
            nested = _resolve_template_lists(
                item, fetch_template, source_path, depth, max_depth,
            )
            expanded.append(nested)
            continue

        template_name = str(item["template"])
        template_yaml = fetch_template(template_name, item, source_path)
        if not template_yaml:
            log.warning("template_resolver: could not fetch step template %s", template_name)
            expanded.append(item)
            continue

        try:
            template_doc = yaml.safe_load(template_yaml)
        except Exception:
            expanded.append(item)
            continue

        if not isinstance(template_doc, dict):
            expanded.append(item)
            continue

        template_path = resolve_template_path(template_name, source_path)
        template_doc = _merge_extends(
            template_doc, fetch_template, template_path, depth + 1, max_depth,
        )
        template_doc = _resolve_template_lists(
            template_doc, fetch_template, template_path, depth + 1, max_depth,
        )

        inlined = _inline_template_body(template_doc, list_kind, item)
        if not inlined:
            log.warning(
                "template_resolver: template %s has no %s to inline",
                template_name, list_kind,
            )
            expanded.append(item)
            continue

        for entry in inlined:
            nested = _resolve_template_lists(
                entry, fetch_template, template_path, depth + 1, max_depth,
            )
            expanded.append(nested)

    return expanded


def _inline_template_body(
    template_doc: dict,
    list_kind: str,
    template_ref: dict,
) -> list:
    """Return list items from a template to splice into the parent list."""
    if list_kind in template_doc and isinstance(template_doc[list_kind], list):
        return list(template_doc[list_kind])

    # Shorthand templates: a step template may be a single step dict without a
    # wrapping ``steps:`` block.
    if list_kind == "steps":
        if "task" in template_doc or "script" in template_doc or "bash" in template_doc:
            return [template_doc]
        if "steps" not in template_doc and "jobs" not in template_doc:
            return [template_doc]

    if list_kind == "jobs" and "job" in template_doc:
        return [template_doc]

    if list_kind == "stages" and ("stage" in template_doc or "displayName" in template_doc):
        return [template_doc]

    # Template parameters are not substituted yet; preserve the ref metadata as
    # a comment-like warning step only when we cannot inline anything useful.
    if template_ref.get("parameters"):
        return []

    return []


def apply_template_resolution_to_meta(
    meta: Any,
    fetch_template: Optional[TemplateFetcher],
    extractor: Optional[Any] = None,
    var_groups: Optional[list] = None,
) -> list[str]:
    """Resolve templates in ``meta.yaml_content`` and refresh ``meta.stages``."""
    refs = extract_template_refs(meta.yaml_content or "")
    if not refs or not fetch_template:
        return []

    resolved = resolve_templates(
        meta.yaml_content,
        fetch_template,
        source_path=getattr(meta, "yaml_path", "") or "",
    )
    warnings = [f"Inlined ADO template(s): {', '.join(refs)}"]
    meta.yaml_content = resolved

    if extractor and resolved:
        meta.stages.clear()
        extractor._extract_yaml_structure(meta, resolved, var_groups or [])

    return warnings


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key == "extends":
            continue
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        elif key in result and isinstance(result[key], list) and isinstance(val, list):
            if key in ("steps", "jobs", "stages"):
                result[key] = result[key] + val
            else:
                result[key] = val
        else:
            result[key] = val
    return result
