"""Scan ADO pipeline YAML for tasks and service connection references."""
from __future__ import annotations

import json

import yaml

from ado2gh.models import PipelineMetadata
from ado2gh.pipelines.transform.task_registry import ADO_TASK_MAP

# Input keys that commonly carry service connection / subscription names.
_SC_INPUT_KEYS = (
    "connectedServiceName",
    "ConnectedServiceName",
    "connectedServiceNameARM",
    "ConnectedServiceNameARM",
    "connectedServiceNameAzureRM",
    "azureSubscription",
    "azureSubscriptionEndpoint",
    "azureServiceConnection",
    "azureResourceManagerConnection",
    "azureRmServiceConnection",
    "kubernetesServiceEndpoint",
    "KubernetesServiceEndpoint",
    "dockerRegistryEndpoint",
    "DockerRegistryEndpoint",
    "serviceConnection",
    "ServiceConnection",
    "endpoint",
    "Endpoint",
    "armServiceConnection",
    "scName",
    "subscriptionId",
    "azureSubscriptionId",
)

# Substring patterns for fuzzy matching — any input key containing these
# is likely a service connection reference.
_SC_KEY_PATTERNS = (
    "connection",
    "Connection",
    "endpoint",
    "Endpoint",
    "subscription",
    "Subscription",
    "serviceprincipal",
    "ServicePrincipal",
)


def scan_yaml_tasks(yaml_content: str) -> tuple[list[str], list[str]]:
    """Find the Azure DevOps tasks a pipeline YAML uses.

    Args:
        yaml_content: Pipeline YAML to scan. Empty or unparseable content
            yields two empty lists rather than an error.

    Returns:
        The sorted unique task names, and the subset with no known GitHub
        Actions equivalent.
    """
    if not (yaml_content or "").strip():
        return [], []

    try:
        doc = yaml.safe_load(yaml_content)
    except Exception:
        return [], []

    tasks: list[str] = []
    _walk_tasks(doc, tasks)
    unique = sorted(set(tasks))
    unsupported = [t for t in unique if t not in ADO_TASK_MAP]
    return unique, unsupported


def scan_service_connection_refs(yaml_content: str) -> list[str]:
    """Find the service connections a pipeline YAML refers to.

    Args:
        yaml_content: Pipeline YAML to scan. Empty or unparseable content
            yields an empty list rather than an error.

    Returns:
        The sorted connection names found in task inputs, matched both on the
        known input keys and on keys that merely look like connection keys.
    """
    if not (yaml_content or "").strip():
        return []

    try:
        doc = yaml.safe_load(yaml_content)
    except Exception:
        return []

    names: set[str] = set()
    _walk_sc_refs(doc, names)
    return sorted(names)


def resolve_service_connections(
    project_connections: list[dict],
    referenced_names: list[str],
) -> list[dict]:
    """Match referenced connection names against the project's endpoints.

    Args:
        project_connections: Service endpoints defined in the project.
        referenced_names: Connection names a pipeline refers to.

    Returns:
        One entry per referenced name holding ``name``, ``type`` and ``id``. A
        name with no matching endpoint keeps the type ``referenced``, so an
        operator can still see it needs a GitHub secret.
    """
    if not referenced_names:
        return []

    by_name = {sc.get("name", ""): sc for sc in project_connections}
    resolved: list[dict] = []
    for name in referenced_names:
        sc = by_name.get(name)
        if sc:
            resolved.append({
                "name": sc.get("name", name),
                "type": sc.get("type", "unknown"),
                "id": sc.get("id", ""),
            })
        else:
            resolved.append({"name": name, "type": "referenced", "id": ""})
    return resolved


def enrich_pipeline_readiness(
    meta: PipelineMetadata,
    project_service_connections: list[dict] | None = None,
    *,
    build_def: dict | None = None,
) -> None:
    """Record the unsupported tasks and service connections on the metadata.

    The pipeline YAML is the main source. When it is missing, the extracted
    stages are scanned instead, and the build definition is always searched for
    connection names and ids, which covers classic pipelines and YAML pipelines
    whose file is incomplete or lives behind a template.

    Args:
        meta: Metadata updated in place.
        project_service_connections: Service endpoints defined in the project.
        build_def: Definition from the Build Definitions API, when available.
    """
    yaml_content = getattr(meta, "yaml_content", "") or ""
    if yaml_content:
        _, unsupported = scan_yaml_tasks(yaml_content)
        if unsupported:
            meta.unsupported_tasks = sorted(set(meta.unsupported_tasks) | set(unsupported))

        refs = scan_service_connection_refs(yaml_content)
        if refs:
            meta.service_connections = resolve_service_connections(
                project_service_connections or [], refs,
            )

        # Brute-force fallback: search YAML content for any project SC name
        # that appears anywhere in the YAML (variables, parameters, comments, etc.)
        if project_service_connections:
            existing_sc_names = {sc.get("name") for sc in (meta.service_connections or [])}
            for sc in project_service_connections:
                sc_name = sc.get("name", "")
                if sc_name and sc_name not in existing_sc_names and sc_name in yaml_content:
                    if not meta.service_connections:
                        meta.service_connections = []
                    meta.service_connections.append({
                        "name": sc_name,
                        "type": sc.get("type", "unknown"),
                        "id": sc.get("id", ""),
                    })
                    existing_sc_names.add(sc_name)

    elif meta.stages:
        tasks: list[str] = []
        for stage in meta.stages:
            for job in stage.jobs or []:
                for step in job.get("steps", [job]):
                    task = step.get("task", step.get("taskName", ""))
                    if task:
                        tasks.append(task)
        unsupported = [t for t in sorted(set(tasks)) if t not in ADO_TASK_MAP]
        if unsupported:
            meta.unsupported_tasks = sorted(set(meta.unsupported_tasks) | set(unsupported))

    # Extract SCs from build definition (covers classic pipelines and YAML
    # pipelines where the YAML file is incomplete or uses templates).
    if build_def and project_service_connections:
        build_def_str = json.dumps(build_def)
        existing_sc_names = {sc.get("name") for sc in (meta.service_connections or [])}
        for sc in project_service_connections:
            sc_name = sc.get("name", "")
            sc_id = sc.get("id", "")
            if not sc_name:
                continue
            found = (
                sc_name in build_def_str
                or sc_id in build_def_str
            )
            if found and sc_name not in existing_sc_names:
                if not meta.service_connections:
                    meta.service_connections = []
                meta.service_connections.append({
                    "name": sc_name,
                    "type": sc.get("type", "unknown"),
                    "id": sc_id,
                })
                existing_sc_names.add(sc_name)


def _walk_tasks(obj: object, out: list[str]) -> None:
    """Collect every task name in a parsed YAML document.

    Args:
        obj: Any node of the parsed document.
        out: List the task names are appended to.
    """
    if isinstance(obj, dict):
        task = obj.get("task") or obj.get("taskName")
        if task:
            out.append(str(task))
        for v in obj.values():
            _walk_tasks(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk_tasks(item, out)


def _walk_sc_refs(obj: object, out: set[str]) -> None:
    """Collect every service connection reference in a parsed YAML document.

    Args:
        obj: Any node of the parsed document.
        out: Set the connection names are added to.
    """
    if isinstance(obj, dict):
        if "task" in obj or "taskName" in obj:
            inputs = obj.get("inputs", {})
            if isinstance(inputs, dict):
                for key, val in inputs.items():
                    if not isinstance(val, str) or not val.strip():
                        continue
                    # Exact key match
                    if key in _SC_INPUT_KEYS:
                        out.add(val.strip())
                        continue
                    # Fuzzy key match — key contains a known pattern
                    if any(pat in key for pat in _SC_KEY_PATTERNS):
                        out.add(val.strip())
        for v in obj.values():
            _walk_sc_refs(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk_sc_refs(item, out)
