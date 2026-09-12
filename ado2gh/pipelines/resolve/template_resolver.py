"""Resolve ADO pipeline template extends / - template: references."""
from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING, Callable, Optional, cast

import yaml

from ado2gh.logging_config import log

if TYPE_CHECKING:
    from ado2gh.clients.ado_client import ADOClient
    from ado2gh.models import PipelineMetadata
    from ado2gh.pipelines.extractor import PipelineMetadataExtractor

#: Loads the YAML of a referenced template. Called with the template name, the
#: reference itself (a string or the mapping that carries its parameters) and
#: the repository path of the file that holds the reference; returns the
#: template YAML, or ``None`` when it cannot be read.
TemplateFetcher = Callable[[str, object, str], Optional[str]]


def resolve_templates(
    yaml_content: str,
    fetch_template: Optional[TemplateFetcher] = None,
    source_path: str = "",
    max_depth: int = 5,
) -> str:
    """Inline every template reference into a single YAML document.

    Args:
        yaml_content: Pipeline YAML to resolve. Empty or unparseable content
            is returned unchanged.
        fetch_template: Loads the YAML of a referenced template. Without it
            nothing is inlined.
        source_path: Repository path of the file holding the references, used
            to resolve relative template paths.
        max_depth: How deep template references are followed before the
            remaining ones are left in place and a warning is logged.

    Returns:
        The YAML with the templates inlined.
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
        # _resolve_template_lists recurses over arbitrary YAML nodes, so its
        # signature is `object`; its own dict branch always returns a dict,
        # and `merged` is a dict going in.
        merged = cast(
            "dict",
            _resolve_template_lists(
                merged, fetch_template, source_path=source_path, depth=0, max_depth=max_depth,
            ),
        )
    return yaml.dump(merged, default_flow_style=False, sort_keys=False, allow_unicode=True)


def extract_template_refs(yaml_content: str) -> list[str]:
    """Find the templates a pipeline YAML refers to.

    Args:
        yaml_content: Pipeline YAML to scan. Empty or unparseable content
            yields an empty list rather than an error.

    Returns:
        The sorted unique template names referenced by ``extends`` or
        ``- template:``.
    """
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
    ado: ADOClient,
    project: str,
    repo_id: str,
    branch: str,
    yaml_path: str = "",
) -> TemplateFetcher:
    """Build a fetcher that reads templates from an Azure DevOps repository.

    Args:
        ado: Client used to read files from the repository.
        project: Azure DevOps project name.
        repo_id: Repository the templates live in.
        branch: Branch the templates are read from.
        yaml_path: Path of the pipeline YAML, used as the base for relative
            template paths when a reference does not carry its own.

    Returns:
        A fetcher that returns the template YAML, or ``None`` when the file is
        missing or empty.
    """

    def fetch(template_name: str, _template_ref: object, source_path: str) -> Optional[str]:
        """Read one referenced template from the captured repository and branch.

        Args:
            template_name: Template reference exactly as written in the pipeline YAML.
            _template_ref: Unused; present to satisfy the fetcher signature.
            source_path: Path of the file holding the reference, used as the base for
                resolving a relative template path.

        Returns:
            The template YAML, or ``None`` when the reference is empty, the file is
            absent from the repository, or its content is blank.
        """
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
    """Resolve a template path against the file that references it.

    Args:
        template_name: Path as written in the reference. A leading ``/`` makes
            it repository-absolute.
        source_path: Repository path of the referencing file.

    Returns:
        The repository path of the template, without a leading slash.
    """
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


def _collect_refs(obj: object, out: list[str]) -> None:
    """Collect every template reference in a parsed YAML document.

    Args:
        obj: Any node of the parsed document.
        out: List the template names are appended to.
    """
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
    """Merge a document onto the template it extends.

    Args:
        doc: Parsed document, which may carry an ``extends`` key.
        fetch_template: Loads the YAML of the extended template.
        source_path: Repository path of the document, for relative paths.
        depth: How many templates deep this call already is.
        max_depth: Depth at which resolution stops.

    Returns:
        The merged document with ``extends`` removed, or the document
        unchanged when there is nothing to merge or the template is missing.
    """
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
    # fetch_template is already guaranteed non-None by the early return above.
    base = cast(
        "dict",
        _resolve_template_lists(base, fetch_template, template_path, depth + 1, max_depth),
    )
    merged = _deep_merge(base, doc)
    merged.pop("extends", None)
    return merged


def _resolve_template_lists(
    doc: object,
    fetch_template: TemplateFetcher,
    source_path: str,
    depth: int,
    max_depth: int,
) -> object:
    """Inline the templates referenced from ``steps``, ``jobs`` and ``stages``.

    Args:
        doc: Any node of the parsed document.
        fetch_template: Loads the YAML of a referenced template.
        source_path: Repository path of the document, for relative paths.
        depth: How many templates deep this call already is.
        max_depth: Depth at which resolution stops.

    Returns:
        The node with its template references replaced by their contents.
    """
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


def _expand_template_list(  # noqa: PLR0913
    items: list,
    list_kind: str,
    fetch_template: TemplateFetcher,
    source_path: str,
    depth: int,
    max_depth: int,
) -> list:
    """Replace each template entry of one list with the template's contents.

    Args:
        items: Entries of a ``steps``, ``jobs`` or ``stages`` list.
        list_kind: Which of the three lists is being expanded.
        fetch_template: Loads the YAML of a referenced template.
        source_path: Repository path of the referencing file.
        depth: How many templates deep this call already is.
        max_depth: Depth at which resolution stops.

    Returns:
        The list with every resolvable reference expanded. A reference that
        cannot be read or holds nothing to inline is kept as it was and a
        warning is logged, so nothing is lost silently.
    """
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
        template_doc = cast(
            "dict",
            _resolve_template_lists(
                template_doc, fetch_template, template_path, depth + 1, max_depth,
            ),
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
    """Return the entries of a template to splice into the parent list.

    Args:
        template_doc: Parsed template document.
        list_kind: Which of ``steps``, ``jobs`` or ``stages`` is being
            expanded.
        template_ref: The reference itself, whose parameters are not
            substituted yet.

    Returns:
        The entries to splice in, or an empty list when the template holds
        nothing usable for this list kind.
    """
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
    meta: PipelineMetadata,
    fetch_template: Optional[TemplateFetcher],
    extractor: Optional[PipelineMetadataExtractor] = None,
    var_groups: Optional[list[dict]] = None,
) -> list[str]:
    """Resolve the templates of a pipeline and refresh its stages.

    Args:
        meta: Metadata updated in place; its YAML is replaced by the resolved
            document.
        fetch_template: Loads the YAML of a referenced template. Without it
            nothing is resolved.
        extractor: Re-reads the stages from the resolved YAML. The stages are
            left as they were when it is omitted.
        var_groups: Variable groups defined in the project, passed on to the
            extractor.

    Returns:
        One warning naming the inlined templates, or an empty list when the
        pipeline referenced none.
    """
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
    """Merge one document over another.

    Args:
        base: Document being merged onto, normally the template.
        override: Document whose values win, normally the pipeline.

    Returns:
        A new merged document. Nested mappings are merged recursively, the
        ``steps``, ``jobs`` and ``stages`` lists are concatenated so the
        template keeps its own entries, any other list is replaced, and
        ``extends`` is dropped because it has been resolved.
    """
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
