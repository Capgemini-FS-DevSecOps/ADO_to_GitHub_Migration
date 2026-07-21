"""Scan ADO pipeline YAML for tasks and service connection references."""
from __future__ import annotations

from typing import Any

import yaml

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
    """Return (all_task_names, unsupported_task_names) from YAML content."""
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
    """Extract service connection name references from YAML step inputs."""
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
    """Match referenced names against project service endpoints."""
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
    meta,
    project_service_connections: list[dict] | None = None,
    *,
    build_def: dict | None = None,
) -> None:
    """Populate unsupported_tasks and service_connections on metadata in-place."""
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
        import json as _json
        build_def_str = _json.dumps(build_def)
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


def _walk_tasks(obj: Any, out: list[str]) -> None:
    if isinstance(obj, dict):
        task = obj.get("task") or obj.get("taskName")
        if task:
            out.append(str(task))
        for v in obj.values():
            _walk_tasks(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk_tasks(item, out)


def _walk_sc_refs(obj: Any, out: set[str]) -> None:
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
