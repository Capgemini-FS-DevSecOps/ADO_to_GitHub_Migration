"""ADO YAML template resolver.

Goal: expand Azure DevOps YAML `template:` references into a resolved document
that the transformer can reason about.

This is intentionally *best effort*:
- supports `steps: - template: ...` and `jobs: - template: ...` and
  `stages: - template: ...`
- supports `parameters:` blocks by doing string substitution for
  ` ${{ parameters.name }}` patterns in the included template

Non-goals (for now):
- full ADO expression evaluation
- runtime template includes from other repos/resources

The output is designed for an AI agent: it can see each template as a separate
unit, and we return a graph of template nodes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Callable

import yaml


@dataclass(frozen=True)
class TemplateNode:
    name: str
    path: str
    kind: str  # steps|jobs|stages|unknown
    parameters: dict[str, Any] = field(default_factory=dict)
    source_yaml: str = ""


@dataclass(frozen=True)
class TemplateResolution:
    root_doc: dict[str, Any]
    nodes: list[TemplateNode]
    warnings: list[str]


@dataclass(frozen=True)
class RepositoryResource:
    alias: str
    project: str | None = None
    name: str | None = None
    ref: str | None = None


_PARAM_REF_RE = re.compile(r"\$\{\{\s*parameters\.([a-zA-Z0-9_]+)\s*}}")


def _normalize_posix(path: str) -> str:
    return str(PurePosixPath(path.replace("\\", "/")))


def _load_yaml(text: str) -> dict[str, Any]:
    doc = yaml.safe_load(text) or {}
    return doc if isinstance(doc, dict) else {}


def _apply_parameters(raw_text: str, params: dict[str, Any]) -> str:
    """Very small parameter substitution: replaces ${{ parameters.X }}."""

    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in params:
            return m.group(0)
        val = params[key]
        if isinstance(val, (dict, list)):
            return yaml.safe_dump(val, sort_keys=False).strip()
        return str(val)

    return _PARAM_REF_RE.sub(repl, raw_text)


def _parse_repo_resources(doc: dict[str, Any]) -> dict[str, RepositoryResource]:
    resources = doc.get("resources") if isinstance(doc, dict) else None
    if not isinstance(resources, dict):
        return {}
    repos = resources.get("repositories")
    if not isinstance(repos, list):
        return {}

    out: dict[str, RepositoryResource] = {}
    for r in repos:
        if not isinstance(r, dict):
            continue
        alias = r.get("repository") or r.get("alias") or r.get("name")
        if not alias:
            continue
        out[str(alias)] = RepositoryResource(
            alias=str(alias),
            project=r.get("project"),
            name=r.get("name") or r.get("repository"),
            ref=r.get("ref"),
        )
    return out


_EXTENDS_KEY = "extends"


def _resolve_template_ref(
    *,
    parent_path: str,
    raw_ref: str,
) -> tuple[str, str | None]:
    """Resolve a template reference to (path, alias).

    Supports:
      - relative paths: templates/foo.yml
      - repo qualified: templates/foo.yml@templatesRepo
    """
    raw_ref = str(raw_ref).strip()
    if "@" in raw_ref:
        p, alias = raw_ref.rsplit("@", 1)
        return _normalize_posix(p), alias.strip() or None
    return _normalize_posix(raw_ref), None


_IF_KEY_RE = re.compile(r"^\$\{\{\s*if\s+(.+?)\s*}}\s*$")
_ELSE_KEY_RE = re.compile(r"^\$\{\{\s*else\s*}}\s*$")
_EACH_KEY_RE = re.compile(r"^\$\{\{\s*each\s+(\w+)\s+in\s+(.+?)\s*}}\s*$")


def _eval_expr(expr: str, ctx: dict[str, Any]) -> bool | None:
    """Evaluate a small subset of ADO compile-time expressions.

    Supported:
      - eq(a,b), ne(a,b)
      - and(x,y), or(x,y), not(x)
      - variables/parameters access: parameters.foo

    Returns True/False if resolvable, else None.
    """
    expr = (expr or "").strip()

    def get_value(token: str):
        token = token.strip()
        if token.startswith("'") and token.endswith("'"):
            return token[1:-1]
        if token.startswith('"') and token.endswith('"'):
            return token[1:-1]
        if token.lower() == "true":
            return True
        if token.lower() == "false":
            return False
        if token.startswith("parameters."):
            return ctx.get("parameters", {}).get(token.split(".", 1)[1])
        if token.startswith("variables."):
            return ctx.get("variables", {}).get(token.split(".", 1)[1])
        return ctx.get(token)

    def parse_call(s: str):
        m = re.match(r"^(\w+)\((.*)\)$", s)
        if not m:
            return None, []
        fn = m.group(1)
        args_s = m.group(2)
        # naive comma-split (doesn't handle nested commas well; adequate for common eq/ne)
        args = [a.strip() for a in re.split(r",(?![^()]*\))", args_s) if a.strip()]
        return fn, args

    fn, args = parse_call(expr)
    if not fn:
        v = get_value(expr)
        return bool(v) if v is not None else None

    fn_l = fn.lower()
    if fn_l == "eq" and len(args) == 2:
        a, b = get_value(args[0]), get_value(args[1])
        return None if a is None or b is None else a == b
    if fn_l == "ne" and len(args) == 2:
        a, b = get_value(args[0]), get_value(args[1])
        return None if a is None or b is None else a != b
    if fn_l == "and" and len(args) >= 2:
        vals = [_eval_expr(a, ctx) for a in args]
        if any(v is None for v in vals):
            return None
        return all(bool(v) for v in vals)
    if fn_l == "or" and len(args) >= 2:
        vals = [_eval_expr(a, ctx) for a in args]
        if any(v is None for v in vals):
            return None
        return any(bool(v) for v in vals)
    if fn_l == "not" and len(args) == 1:
        v = _eval_expr(args[0], ctx)
        return None if v is None else (not bool(v))

    return None


def _expand_compile_time_list(items: list[Any], ctx: dict[str, Any], warnings: list[str]) -> list[Any]:
    """Expand compile-time if/else/each patterns inside YAML lists."""
    out: list[Any] = []
    i = 0
    while i < len(items):
        it = items[i]
        if isinstance(it, dict) and len(it) == 1:
            k = next(iter(it.keys()))
            v = it[k]
            if isinstance(k, str):
                m_if = _IF_KEY_RE.match(k)
                m_each = _EACH_KEY_RE.match(k)
                if m_if:
                    cond = m_if.group(1)
                    res = _eval_expr(cond, ctx)
                    else_branch = None
                    if i + 1 < len(items) and isinstance(items[i + 1], dict) and len(items[i + 1]) == 1:
                        k2 = next(iter(items[i + 1].keys()))
                        if isinstance(k2, str) and _ELSE_KEY_RE.match(k2):
                            else_branch = items[i + 1][k2]
                            i += 1
                    chosen = v if res is True else else_branch if res is False else None
                    if chosen is None:
                        warnings.append(f"Could not evaluate template if-condition: {cond!r} (keeping both branches)" )
                        # keep both branches as-is
                        if isinstance(v, list):
                            out.extend(v)
                        elif v is not None:
                            out.append(v)
                        if else_branch is not None:
                            if isinstance(else_branch, list):
                                out.extend(else_branch)
                            else:
                                out.append(else_branch)
                    else:
                        if isinstance(chosen, list):
                            out.extend(chosen)
                        elif chosen is not None:
                            out.append(chosen)
                    i += 1
                    continue
                if m_each:
                    var = m_each.group(1)
                    expr = m_each.group(2)
                    seq = None
                    if expr.startswith("parameters."):
                        seq = ctx.get("parameters", {}).get(expr.split(".", 1)[1])
                    if not isinstance(seq, list):
                        warnings.append(f"Could not expand each-loop (non-list): {expr!r}")
                        i += 1
                        continue
                    body = v
                    for elem in seq:
                        sub_ctx = {**ctx, var: elem}
                        # body usually list
                        if isinstance(body, list):
                            out.extend(body)
                        else:
                            out.append(body)
                    i += 1
                    continue
        out.append(it)
        i += 1
    return out


def resolve_templates(
    *,
    root_yaml_text: str,
    root_path: str,
    fetch_text: Callable[[str], str],
    fetch_text_with_alias: Callable[[dict, str], str] | None = None,
    max_depth: int = 8,
) -> TemplateResolution:
    """Resolve template references starting at the root YAML.

    Parameters
    - root_yaml_text: contents of the root azure-pipelines.yml
    - root_path: repo-relative path used for resolving relative includes
    - fetch_text(path): function that returns file text (repo-relative path)

    Returns
    - TemplateResolution(root_doc, nodes, warnings)

    Notes
    - We inline templates into the root doc, but also return `nodes` so callers
      can choose to emit a *separate workflow file per template*.
    """
    warnings: list[str] = []
    nodes: list[TemplateNode] = []

    root_path = _normalize_posix(root_path or "azure-pipelines.yml")

    def resolve_list(parent_path: str, key: str, items: list[Any], depth: int) -> list[Any]:
        if depth > max_depth:
            warnings.append(f"Max template depth exceeded at {parent_path}")
            return items

        out: list[Any] = []
        for item in items:
            if isinstance(item, dict) and "template" in item:
                tpl_raw = item.get("template", "")
                tpl_path, tpl_alias = _resolve_template_ref(parent_path=parent_path, raw_ref=str(tpl_raw))
                # Relative paths resolve from parent file directory (only for non-aliased templates).
                base_dir = str(PurePosixPath(parent_path).parent)
                if not tpl_path.startswith("/") and base_dir not in {".", ""}:
                    tpl_path = _normalize_posix(str(PurePosixPath(base_dir) / tpl_path))
                tpl_path = tpl_path.lstrip("/")

                params = item.get("parameters", {}) if isinstance(item.get("parameters"), dict) else {}

                raw_tpl = ""
                if tpl_alias and fetch_text_with_alias and tpl_alias in repo_resources:
                    # Fetch from aliased repo.
                    # repo_resources entries are RepositoryResource, but keep original dict for fetcher.
                    # We don't have the original dict, so reconstruct minimal shape.
                    alias_obj = {"name": repo_resources[tpl_alias].name or tpl_alias, "ref": repo_resources[tpl_alias].ref or "", "project": repo_resources[tpl_alias].project or ""}
                    raw_tpl = fetch_text_with_alias(alias_obj, tpl_path)
                if not raw_tpl:
                    raw_tpl = fetch_text(tpl_path)

                if not raw_tpl:
                    warnings.append(f"Template not found or empty: {tpl_raw}")
                    out.append({"name": f"TODO: missing template {tpl_raw}", "script": "echo missing template"})
                    continue

                applied = _apply_parameters(raw_tpl, params)
                tpl_doc = _load_yaml(applied)

                nodes.append(
                    TemplateNode(
                        name=PurePosixPath(tpl_path).stem,
                        path=tpl_path,
                        kind=key,
                        parameters=params,
                        source_yaml=raw_tpl,
                    )
                )

                # Common ADO template structures:
                # - steps: [...]
                # - jobs: [...]
                # - stages: [...]
                # If template provides the same key, inline it. If not, inline whole doc.
                if isinstance(tpl_doc.get(key), list):
                    inlined = tpl_doc.get(key, [])
                elif isinstance(tpl_doc.get("steps"), list) and key == "steps":
                    inlined = tpl_doc.get("steps", [])
                elif isinstance(tpl_doc.get("jobs"), list) and key == "jobs":
                    inlined = tpl_doc.get("jobs", [])
                elif isinstance(tpl_doc.get("stages"), list) and key == "stages":
                    inlined = tpl_doc.get("stages", [])
                else:
                    # Best-effort: treat full doc as one item.
                    inlined = [tpl_doc] if tpl_doc else []

                # Recurse within the inlined items.
                inlined = resolve_list(tpl_path, key, inlined, depth + 1)
                out.extend(inlined)
            else:
                out.append(item)
        return out

    root_doc = _load_yaml(root_yaml_text)

    # Parse repo resources up-front (may be defined either in child or base). We'll update it after extends.
    repo_resources = _parse_repo_resources(root_doc)

    # Handle `extends` at root.
    if isinstance(root_doc.get(_EXTENDS_KEY), dict):
        ext = root_doc.get(_EXTENDS_KEY, {})
        tpl_raw = ext.get("template", "")
        if tpl_raw:
            tpl_path, tpl_alias = _resolve_template_ref(parent_path=root_path, raw_ref=str(tpl_raw))
            tpl_path = tpl_path.lstrip("/")
            params = ext.get("parameters", {}) if isinstance(ext.get("parameters"), dict) else {}
            raw_tpl = ""
            if tpl_alias and fetch_text_with_alias and tpl_alias in repo_resources:
                alias_obj = {"name": repo_resources[tpl_alias].name or tpl_alias, "ref": repo_resources[tpl_alias].ref or "", "project": repo_resources[tpl_alias].project or ""}
                raw_tpl = fetch_text_with_alias(alias_obj, tpl_path)
            if not raw_tpl:
                raw_tpl = fetch_text(tpl_path)
            if raw_tpl:
                applied = _apply_parameters(raw_tpl, params)
                base_doc = _load_yaml(applied)
                # Shallow overlay: child fields override base.
                child_no_ext = {k: v for k, v in root_doc.items() if k != _EXTENDS_KEY}
                root_doc = {**base_doc, **child_no_ext}
                nodes.append(TemplateNode(name=PurePosixPath(tpl_path).stem, path=tpl_path, kind="extends", parameters=params, source_yaml=raw_tpl))
            else:
                warnings.append(f"Extends template not found: {tpl_raw}")

    # Update repo resources after extends merge, so repo alias map includes base template additions.
    repo_resources = _parse_repo_resources(root_doc) or repo_resources

    ctx = {"parameters": {}, "variables": {}}
    if isinstance(root_doc.get("parameters"), list):
        # ADO parameter definitions list
        for p in root_doc.get("parameters", []):
            if isinstance(p, dict) and p.get("name"):
                ctx["parameters"][p["name"]] = p.get("default")
    if isinstance(root_doc.get("variables"), dict):
        ctx["variables"].update(root_doc.get("variables", {}))

    for k in ("steps", "jobs", "stages"):
        v = root_doc.get(k)
        if isinstance(v, list):
            v2 = _expand_compile_time_list(v, ctx, warnings)
            root_doc[k] = resolve_list(root_path, k, v2, 1)

    return TemplateResolution(root_doc=root_doc, nodes=nodes, warnings=warnings)
