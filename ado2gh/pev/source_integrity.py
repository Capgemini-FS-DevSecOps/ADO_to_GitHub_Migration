"""Content-addressed source boundaries for non-Git migration scopes.

The approved plan stores only a digest and aggregate counts.  Source payloads
can contain work-item text, wiki content, or infrastructure names, so they are
never embedded in the plan, task database, or audit evidence.
"""
from __future__ import annotations

import hmac
import re
from typing import Any, Iterable, Mapping, Union
from urllib.parse import unquote

from ado2gh.models import MigrationScope
from ado2gh.pev.contracts import canonical_json, content_digest


SNAPSHOT_SCHEMA_VERSION = 1
BOUND_NON_GIT_SCOPES = frozenset({
    MigrationScope.WORK_ITEMS.value,
    MigrationScope.WIKI.value,
    MigrationScope.SECRETS.value,
    MigrationScope.BRANCH_POLICIES.value,
})
ACCESS_SNAPSHOT_SCHEMA_VERSION = 2
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def fetch_scope_payload(
    ado: Any,
    repo: Any,
    scope: str,
    *,
    source_repo_id: str,
    include_unlinked_work_items: bool = False,
) -> Any:
    """Fetch one complete source payload for a migration scope.

    Callers deliberately pass the immutable repository ID from the approved
    Git snapshot.  The returned object is kept in memory and must be the same
    object subsequently transformed by the executor.
    """
    repo_id = str(source_repo_id).strip()
    if not repo_id:
        raise ValueError("source_repo_id is required for non-Git source binding")
    if scope == MigrationScope.WORK_ITEMS.value:
        return ado.list_work_items(
            repo.ado_project,
            repo_id=repo_id,
            include_unlinked=bool(include_unlinked_work_items),
        )
    if scope == MigrationScope.WIKI.value:
        return ado.list_wiki_pages(repo.ado_project)
    if scope == MigrationScope.SECRETS.value:
        return {
            "variable_groups": ado.list_variable_groups(repo.ado_project),
            "service_connections": ado.list_service_connections(repo.ado_project),
        }
    if scope == MigrationScope.BRANCH_POLICIES.value:
        return ado.list_branch_policies(repo.ado_project, repo_id)
    raise ValueError(f"Scope {scope!r} does not have a non-Git source boundary")


def fetch_project_work_item_payloads(
    ado: Any,
    repositories: Iterable[Any],
    *,
    include_unlinked_work_items: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Hydrate each ADO project's work items once and index them by repo.

    Work items are project-scoped in ADO. Calling ``list_work_items`` once per
    repository turns a 5,000-repository organization into an O(R*N) API and
    memory workload. This helper creates one complete, phase-local project
    snapshot, then derives the same repository association deterministically
    from immutable repository IDs. Callers intentionally invoke it separately
    during planning, execution, and validation so no stale payload crosses a
    PEV trust boundary.
    """
    grouped: dict[str, list[Any]] = {}
    for repo in repositories:
        project = str(getattr(repo, "ado_project", "")).strip()
        source_key = str(getattr(repo, "source_key", "") or "").strip()
        repo_id = str(getattr(repo, "source_repo_id", "") or "").strip()
        if not project or not source_key or not repo_id:
            raise ValueError(
                "work-item indexing requires project, source_key, and "
                "immutable source_repo_id"
            )
        grouped.setdefault(project, []).append(repo)

    indexed: dict[str, list[dict[str, Any]]] = {}
    for project, project_repos in sorted(grouped.items()):
        items = ado.list_work_items(project)
        if not isinstance(items, list):
            raise TypeError("ADO project work-item snapshot must be a list")
        repo_key_by_id = {
            str(repo.source_repo_id).casefold(): str(repo.source_key)
            for repo in project_repos
        }
        if len(repo_key_by_id) != len(project_repos):
            raise ValueError(
                f"ADO project {project!r} has duplicate repository IDs"
            )
        for source_key in repo_key_by_id.values():
            indexed[source_key] = []
        # Unlinked project backlog has no repository-relative mapping. When
        # explicitly requested, assign it once to a stable project anchor
        # instead of multiplying the entire backlog across every repository.
        unlinked_owner = min(
            repo_key_by_id.values(), key=lambda value: value.casefold()
        )
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise TypeError(
                    f"ADO work-item snapshot entry {index} must be an object"
                )
            relations = item.get("relations", [])
            if not isinstance(relations, list):
                raise TypeError(
                    f"ADO work-item {item.get('id')!r} relations must be a list"
                )
            linked: set[str] = set()
            for relation in relations:
                if not isinstance(relation, Mapping) \
                        or relation.get("rel") != "ArtifactLink":
                    continue
                # ADO artifact links URL-encode path separators. Repository
                # IDs themselves (normally UUIDs) remain one token after
                # decoding, so this is linear in relation text rather than in
                # the number of repositories.
                tokens = re.split(
                    r"[^a-z0-9_.-]+",
                    unquote(str(relation.get("url", ""))).casefold(),
                )
                linked.update(
                    repo_key_by_id[token]
                    for token in tokens if token in repo_key_by_id
                )
            if linked:
                for source_key in sorted(linked, key=str.casefold):
                    indexed[source_key].append(item)
            elif include_unlinked_work_items:
                indexed[unlinked_owner].append(item)
    return indexed


def fetch_project_access_snapshots(
    ado: Any,
    repositories: Iterable[Any],
    *,
    require_stable: bool = False,
) -> dict[str, dict[str, Any]]:
    """Capture ACL and effective-membership digests once per ADO project.

    A repository ACL is not a complete access boundary when an ACE points to
    an ADO/Entra group.  The project inventory therefore resolves every
    distinct ACL principal with ``ExpandedDown`` membership, then projects the
    already-fetched result onto each repository.  Principal identifiers are
    represented in the immutable plan only by domain-separated hashes.
    """
    grouped: dict[str, list[Any]] = {}
    for repo in repositories:
        project = str(getattr(repo, "ado_project", "")).strip()
        source_key = str(getattr(repo, "source_key", "") or "").strip()
        repo_id = str(getattr(repo, "source_repo_id", "") or "").strip()
        if not project or not source_key or not repo_id:
            raise ValueError(
                "access indexing requires project, source_key, and source_repo_id"
            )
        grouped.setdefault(project, []).append(repo)

    def canonical_acl(row: Any) -> dict[str, Any]:
        if not isinstance(row, Mapping):
            raise TypeError("ADO Git ACL entry must be an object")
        token = str(row.get("token", "")).strip()
        aces = row.get("acesDictionary", {})
        if not token or not isinstance(aces, Mapping):
            raise TypeError("ADO Git ACL entry is incomplete")
        entries = []
        for descriptor, raw in aces.items():
            if not isinstance(raw, Mapping):
                raise TypeError("ADO Git ACL ACE must be an object")
            identity = str(raw.get("descriptor") or descriptor).strip()
            allow, deny = raw.get("allow"), raw.get("deny")
            if (
                not identity
                or isinstance(allow, bool) or not isinstance(allow, int)
                or isinstance(deny, bool) or not isinstance(deny, int)
            ):
                raise TypeError("ADO Git ACL ACE is incomplete")
            extended = raw.get("extendedInfo", {}) or {}
            if not isinstance(extended, Mapping):
                raise TypeError("ADO Git ACL extendedInfo must be an object")
            effective: dict[str, int] = {}
            for key in (
                "effectiveAllow", "effectiveDeny", "inheritedAllow",
                "inheritedDeny",
            ):
                if key in extended:
                    value = extended[key]
                    if isinstance(value, bool) or not isinstance(value, int):
                        raise TypeError("ADO Git ACL effective permission is invalid")
                    effective[key] = value
            entries.append({
                "descriptor": identity,
                "allow": allow,
                "deny": deny,
                "effective": effective,
            })
        return {
            "token": token,
            "inherit_permissions": bool(row.get("inheritPermissions", False)),
            "aces": sorted(entries, key=lambda item: item["descriptor"].casefold()),
        }

    def identity_key(kind: str, value: str) -> str:
        normalized = str(value).strip().casefold()
        if not normalized:
            raise TypeError(f"ADO identity {kind} is empty")
        return "sha256:" + content_digest({
            "schema_version": ACCESS_SNAPSHOT_SCHEMA_VERSION,
            "kind": kind,
            "value": normalized,
        })

    def canonical_member(raw: Any) -> str:
        if isinstance(raw, str):
            descriptor = raw.strip()
        elif isinstance(raw, Mapping):
            identity_type = str(raw.get("identityType", "")).strip()
            identifier = str(raw.get("identifier", "")).strip()
            if not identity_type or not identifier:
                raise TypeError("ADO group member descriptor is incomplete")
            descriptor = f"{identity_type};{identifier}"
        else:
            raise TypeError("ADO group member descriptor is invalid")
        return identity_key("expanded_member_descriptor", descriptor)

    def canonical_identity(expected_descriptor: str, row: Any) -> dict[str, Any]:
        if not isinstance(row, Mapping):
            raise TypeError("ADO identity inventory item must be an object")
        descriptor = str(row.get("descriptor", "")).strip()
        if descriptor.casefold() != expected_descriptor.casefold():
            raise RuntimeError("ADO identity descriptor does not match its ACL ACE")
        storage_id = str(row.get("id", "")).strip()
        subject_descriptor = str(row.get("subjectDescriptor", "")).strip()
        is_container = row.get("isContainer")
        if is_container is None:
            properties = row.get("properties", {})
            schema_value: Any = None
            if isinstance(properties, Mapping):
                schema_value = properties.get("SchemaClassName")
            if isinstance(schema_value, Mapping):
                schema_value = schema_value.get("$value")
            schema_class = str(schema_value or "").strip().casefold()
            if schema_class in {"user", "group"}:
                is_container = schema_class == "group"
        is_active = row.get("isActive")
        members = row.get("members")
        member_ids = row.get("memberIds")
        if (
            not storage_id or not subject_descriptor
            or not isinstance(is_container, bool)
            or not isinstance(is_active, bool)
            or not isinstance(members, list)
            or not isinstance(member_ids, list)
        ):
            raise TypeError("ADO identity membership inventory is incomplete")
        canonical_members = sorted({canonical_member(item) for item in members})
        canonical_member_ids = sorted({
            identity_key("ado_identity_storage_id", str(item))
            for item in member_ids
        })
        if len(canonical_members) != len(canonical_member_ids):
            raise RuntimeError(
                "ADO expanded member descriptors and storage IDs disagree"
            )
        if not is_container and (canonical_members or canonical_member_ids):
            raise RuntimeError("ADO non-container identity returned group members")
        return {
            "principal_key": identity_key("acl_principal", descriptor),
            "storage_key": identity_key("ado_identity_storage_id", storage_id),
            "subject_key": identity_key(
                "principal_subject_descriptor", subject_descriptor
            ),
            "is_active": is_active,
            "is_container": is_container,
            "expanded_member_descriptors": canonical_members,
            "expanded_member_storage_ids": canonical_member_ids,
        }

    def index_acl_rows(
        project_id: str,
        rows: list[dict[str, Any]],
        project_repos: list[Any],
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        project_token = f"repoV2/{project_id}".casefold()
        root_token = "repov2"
        repo_rows: dict[str, list[dict[str, Any]]] = {}
        for repo in project_repos:
            repo_id = str(repo.source_repo_id).strip().casefold()
            if not repo_id or repo_id in repo_rows:
                raise RuntimeError(
                    "ADO project access inventory has duplicate repository IDs"
                )
            repo_rows[repo_id] = []
        project_rows: list[dict[str, Any]] = []
        child_prefix = project_token + "/"
        for row in rows:
            token = row["token"].casefold().rstrip("/")
            if token in {root_token, project_token}:
                project_rows.append(row)
                continue
            if not token.startswith(child_prefix):
                continue
            repo_id = token[len(child_prefix):].split("/", 1)[0]
            selected = repo_rows.get(repo_id)
            if selected is not None:
                selected.append(row)
        return project_rows, repo_rows

    root_reader = getattr(ado, "list_git_root_acls", None)
    if not callable(root_reader):
        raise RuntimeError(
            "ADO client cannot inventory inherited organization Git ACLs"
        )

    def query_root() -> list[dict[str, Any]]:
        raw_rows = root_reader()
        if not isinstance(raw_rows, list):
            raise TypeError("ADO organization Git ACL inventory must be a list")
        return sorted(
            (canonical_acl(row) for row in raw_rows),
            key=lambda item: item["token"].casefold(),
        )

    root_rows = query_root()
    if require_stable and query_root() != root_rows:
        raise RuntimeError(
            "ADO inherited Git ACLs changed while snapshotting the organization"
        )
    observed_identities: dict[str, dict[str, Any]] = {}

    def query_project(
        project: str,
        project_repos: list[Any],
    ) -> tuple[str, list[dict], dict[str, dict[str, Any]]]:
        metadata = ado.get_repo(
            project, str(project_repos[0].source_repo_id)
        )
        project_data = metadata.get("project", {}) \
            if isinstance(metadata, Mapping) else {}
        project_id = str(project_data.get("id", "")).strip() \
            if isinstance(project_data, Mapping) else ""
        if not project_id:
            raise RuntimeError(
                f"ADO project {project!r} has no immutable project ID"
            )
        reader = getattr(ado, "list_project_git_acls", None)
        if not callable(reader):
            raise RuntimeError("ADO client cannot inventory Git repository ACLs")
        raw_rows = reader(project_id)
        if not isinstance(raw_rows, list):
            raise TypeError("ADO project Git ACL inventory must be a list")
        canonical_rows = sorted(
            root_rows + [canonical_acl(row) for row in raw_rows],
            key=lambda item: item["token"].casefold(),
        )
        tokens = [row["token"].casefold() for row in canonical_rows]
        if len(tokens) != len(set(tokens)):
            raise RuntimeError("ADO Git ACL inventory contains duplicate tokens")
        project_rows, repo_rows = index_acl_rows(
            project_id, canonical_rows, project_repos
        )
        membership_rows = project_rows + [
            row
            for repo_id in sorted(repo_rows)
            for row in repo_rows[repo_id]
        ]
        descriptors = sorted({
            ace["descriptor"]
            for row in membership_rows for ace in row["aces"]
        }, key=str.casefold)
        identity_reader = getattr(ado, "resolve_acl_identities", None)
        if not callable(identity_reader):
            raise RuntimeError(
                "ADO client cannot inventory ACL principal membership"
            )
        raw_identities = identity_reader(descriptors)
        if not isinstance(raw_identities, Mapping):
            raise TypeError("ADO ACL identity inventory must be an object")
        returned_by_fold = {
            str(descriptor).casefold(): (str(descriptor), value)
            for descriptor, value in raw_identities.items()
        }
        if len(returned_by_fold) != len(raw_identities):
            raise RuntimeError("ADO ACL identity inventory has duplicate principals")
        expected_folds = {descriptor.casefold() for descriptor in descriptors}
        if set(returned_by_fold) != expected_folds:
            raise RuntimeError(
                "ADO ACL identity inventory does not cover every principal exactly"
            )
        identities = {
            descriptor: canonical_identity(
                descriptor,
                returned_by_fold[descriptor.casefold()][1],
            )
            for descriptor in descriptors
        }
        for descriptor, identity in identities.items():
            folded = descriptor.casefold()
            previous = observed_identities.get(folded)
            if previous is not None and previous != identity:
                raise RuntimeError(
                    "ADO principal membership changed across project snapshots"
                )
            observed_identities[folded] = identity
        return project_id, canonical_rows, identities

    result: dict[str, dict[str, Any]] = {}
    for project, project_repos in sorted(grouped.items()):
        project_id, rows, identities = query_project(project, project_repos)
        if require_stable:
            confirmed_id, confirmed_rows, confirmed_identities = query_project(
                project, project_repos
            )
            if (
                confirmed_id != project_id
                or confirmed_rows != rows
                or confirmed_identities != identities
            ):
                raise RuntimeError(
                    "ADO Git ACLs or principal memberships changed while "
                    f"snapshotting project {project!r}"
                )
        project_rows, repo_rows = index_acl_rows(
            project_id, rows, project_repos
        )
        for repo in project_repos:
            selected = project_rows + repo_rows[
                str(repo.source_repo_id).strip().casefold()
            ]
            descriptors = {
                ace["descriptor"]
                for row in selected for ace in row["aces"]
            }
            selected_identities = [
                identities[descriptor]
                for descriptor in sorted(descriptors, key=str.casefold)
            ]
            principal_keys = sorted(
                identity["principal_key"] for identity in selected_identities
            )
            principal_memberships = {
                identity["principal_key"]: (
                    list(identity["expanded_member_storage_ids"])
                    if identity["is_container"]
                    else [identity["storage_key"]]
                )
                for identity in selected_identities
            }
            acl_digest = content_digest(selected)
            membership_digest = content_digest(selected_identities)
            result[str(repo.source_key)] = {
                "schema_version": ACCESS_SNAPSHOT_SCHEMA_VERSION,
                "mode": "ado_git_acl_expanded_membership",
                "digest": content_digest({
                    "schema_version": ACCESS_SNAPSHOT_SCHEMA_VERSION,
                    "mode": "ado_git_acl_expanded_membership",
                    "acls": selected,
                    "identities": selected_identities,
                }),
                "acl_digest": acl_digest,
                "membership_digest": membership_digest,
                "acl_count": len(selected),
                "principal_count": len(descriptors),
                "group_count": sum(
                    1 for identity in selected_identities
                    if identity["is_container"]
                ),
                "membership_edge_count": sum(
                    len(identity["expanded_member_descriptors"])
                    for identity in selected_identities
                ),
                "principal_keys": principal_keys,
                "principal_memberships": dict(sorted(principal_memberships.items())),
            }
    return result


def create_scope_snapshot(scope: str, payload: Any) -> dict[str, Any]:
    """Return non-sensitive, deterministic evidence for one source payload."""
    canonical, counts = _canonical_scope_payload(scope, payload)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "scope": scope,
        "digest": content_digest({
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "scope": scope,
            "content": canonical,
        }),
        "item_count": counts["item_count"],
        "counts": counts,
    }


def validate_scope_snapshot(scope: str, value: Any) -> dict[str, Any]:
    """Validate the shape of plan evidence without reading a source payload."""
    if not isinstance(value, Mapping):
        raise ValueError(f"Approved {scope} source snapshot must be an object")
    if value.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported {scope} source snapshot schema")
    if value.get("scope") != scope:
        raise ValueError(f"Approved source snapshot scope mismatch for {scope}")
    digest = value.get("digest")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError(f"Approved {scope} source snapshot digest is invalid")
    item_count = value.get("item_count")
    if isinstance(item_count, bool) or not isinstance(item_count, int) \
            or item_count < 0:
        raise ValueError(f"Approved {scope} source item_count is invalid")
    counts = value.get("counts")
    if not isinstance(counts, Mapping) or counts.get("item_count") != item_count:
        raise ValueError(f"Approved {scope} source counts are inconsistent")
    clean_counts: dict[str, int] = {}
    for key, count in counts.items():
        if not isinstance(key, str) or isinstance(count, bool) \
                or not isinstance(count, int) or count < 0:
            raise ValueError(f"Approved {scope} source counts are invalid")
        clean_counts[key] = count
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "scope": scope,
        "digest": digest,
        "item_count": item_count,
        "counts": dict(sorted(clean_counts.items())),
    }


def verify_scope_payload(
    scope: str, payload: Any, approved: Any
) -> dict[str, Any]:
    """Fail closed unless an in-memory payload matches approved evidence."""
    expected = validate_scope_snapshot(scope, approved)
    observed = create_scope_snapshot(scope, payload)
    if not hmac.compare_digest(expected["digest"], observed["digest"]):
        raise RuntimeError(
            f"{scope} source drift: approved digest {expected['digest']}, "
            f"observed {observed['digest']}"
        )
    if expected["item_count"] != observed["item_count"] \
            or expected["counts"] != observed["counts"]:
        raise RuntimeError(
            f"{scope} source drift: approved counts {expected['counts']}, "
            f"observed {observed['counts']}"
        )
    return observed


def _canonical_scope_payload(
    scope: str, payload: Any
) -> tuple[Any, dict[str, int]]:
    if scope == MigrationScope.WORK_ITEMS.value:
        items = _require_list(payload, scope)
        canonical = []
        seen: set[str] = set()
        fields_used = (
            "System.Title",
            "System.WorkItemType",
            "System.State",
            "System.Description",
        )
        for index, item in enumerate(items):
            row = _require_mapping(item, f"{scope}[{index}]")
            item_id = str(row.get("id", "")).strip()
            if not item_id or item_id in seen:
                raise ValueError(f"{scope} contains a missing or duplicate work-item ID")
            seen.add(item_id)
            fields = _require_mapping(row.get("fields", {}), f"{scope}[{index}].fields")
            canonical.append({
                "id": item_id,
                "fields": {key: fields.get(key) for key in fields_used},
            })
        canonical.sort(key=lambda row: (_numeric_key(row["id"]), row["id"]))
        return canonical, {"item_count": len(canonical)}

    if scope == MigrationScope.WIKI.value:
        wikis = _require_list(payload, scope)
        canonical_wikis: list[dict[str, Any]] = []
        page_count = 0
        for index, wiki_data in enumerate(wikis):
            row = _require_mapping(wiki_data, f"{scope}[{index}]")
            wiki = _require_mapping(row.get("wiki", {}), f"{scope}[{index}].wiki")
            pages = _flatten_wiki_page(
                _require_mapping(row.get("root", {}), f"{scope}[{index}].root")
            )
            page_count += len(pages)
            canonical_wikis.append({
                "wiki_id": str(wiki.get("id", "")),
                "wiki_name": str(wiki.get("name", "")),
                "pages": pages,
            })
        canonical_wikis.sort(key=canonical_json)
        return canonical_wikis, {
            "item_count": page_count,
            "wiki_count": len(canonical_wikis),
        }

    if scope == MigrationScope.SECRETS.value:
        root = _require_mapping(payload, scope)
        groups = _require_list(root.get("variable_groups"), "variable_groups")
        connections = _require_list(
            root.get("service_connections"), "service_connections"
        )
        canonical_groups: list[dict[str, Any]] = []
        variable_count = 0
        for index, group in enumerate(groups):
            row = _require_mapping(group, f"variable_groups[{index}]")
            variables = _require_mapping(
                row.get("variables", {}), f"variable_groups[{index}].variables"
            )
            projected_variables = []
            for name, descriptor in variables.items():
                details = _require_mapping(
                    descriptor, f"variable_groups[{index}].variables[{name!r}]"
                )
                projected_variables.append({
                    "name": str(name),
                    "is_secret": bool(details.get("isSecret", False)),
                })
            projected_variables.sort(key=lambda item: item["name"].casefold())
            variable_count += len(projected_variables)
            canonical_groups.append({
                "id": str(row.get("id", "")),
                "name": str(row.get("name", "")),
                "type": str(row.get("type", "")),
                "variables": projected_variables,
            })
        canonical_connections = []
        for index, connection in enumerate(connections):
            row = _require_mapping(connection, f"service_connections[{index}]")
            canonical_connections.append({
                "id": str(row.get("id", "")),
                "name": str(row.get("name", "")),
                "type": str(row.get("type", "")),
            })
        canonical_groups.sort(key=canonical_json)
        canonical_connections.sort(key=canonical_json)
        return {
            "variable_groups": canonical_groups,
            "service_connections": canonical_connections,
        }, {
            "item_count": len(canonical_groups) + len(canonical_connections),
            "variable_group_count": len(canonical_groups),
            "service_connection_count": len(canonical_connections),
            "variable_count": variable_count,
        }

    if scope == MigrationScope.BRANCH_POLICIES.value:
        policies = _require_list(payload, scope)
        canonical = [_canonical_unordered_json(item) for item in policies]
        canonical.sort(key=canonical_json)
        branches = {
            str(scope_row.get("refName"))
            for policy in policies
            for scope_row in _policy_scopes(policy)
            if isinstance(scope_row.get("refName"), str)
            and scope_row.get("refName")
        }
        return canonical, {
            "item_count": len(canonical),
            "affected_branch_count": len(branches),
        }

    raise ValueError(f"Scope {scope!r} does not have a non-Git source boundary")


def _flatten_wiki_page(page: Mapping[str, Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []

    def visit(node: Mapping[str, Any]) -> None:
        path = str(node.get("path", "/Home"))
        content = node.get("content")
        flattened.append({
            "path": path,
            "content": content if content is not None else f"# {(path.split('/')[-1] or 'Home')}\n",
        })
        children = _require_list(node.get("subPages", []), f"wiki page {path}.subPages")
        for index, child in enumerate(children):
            visit(_require_mapping(child, f"wiki page {path}.subPages[{index}]"))

    visit(page)
    flattened.sort(key=lambda item: (item["path"].casefold(), canonical_json(item)))
    return flattened


def _policy_scopes(policy: Any) -> list[Mapping[str, Any]]:
    if not isinstance(policy, Mapping):
        return []
    settings = policy.get("settings", {})
    if not isinstance(settings, Mapping):
        return []
    scopes = settings.get("scope", [])
    if not isinstance(scopes, list):
        return []
    return [item for item in scopes if isinstance(item, Mapping)]


def _canonical_unordered_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_unordered_json(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        items = [_canonical_unordered_json(item) for item in value]
        return sorted(items, key=canonical_json)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"Source payload contains unsupported value type {type(value).__name__}")


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a complete list")
    return value


def _numeric_key(value: str) -> tuple[int, Union[int, str]]:
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)
