"""Derive dependency facts from the pipeline inventory this platform already collected.

Nothing here talks to Azure DevOps. Every fact comes out of rows the pipeline
inventory already wrote: the repository columns, the declared variable groups,
the service connection references the task scanner left behind, the template
references the inventory now keeps, and one more walk over the pipeline text
that is stored on the row anyway.

Each dependency is recorded with how firmly it is believed. A column the
source filled in is declared; a name a heuristic spotted in free text is
inferred. The difference is the whole point: an answer that presents a guess as
a fact is worse than no answer, because the reader stops checking.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

import yaml

from ado2gh.knowledge.models import (
    Confidence,
    EdgeKind,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeScan,
    NodeKind,
    utc_now_text,
)
from ado2gh.knowledge.store import KnowledgeStore
from ado2gh.logging_config import log
from ado2gh.models import PipelineMetadata
from ado2gh.state.base import StateDBBase

EXTRACTOR_VERSION = "pipeline-inventory-1"
"""Version of this derivation, stored on the scan so older facts can be found again."""

SOURCE_SYSTEM = "azure_devops"
"""Platform the recorded things live in; the inventory only ever holds the source side."""

FEED_INPUT_KEYS = (
    "vstsFeed",
    "vstsFeedPublish",
    "feedRestore",
    "feedPublish",
    "publishVstsFeed",
    "feedList",
    "artifactFeed",
    "artifactFeeds",
    "artifactsFeeds",
    "customFeed",
    "publishFeed",
)
"""Task input names whose value is an Azure Artifacts feed.

Keys such as ``feedsToUse`` are deliberately absent: they hold a mode rather
than a feed. The set grows as more package tasks are met, which is why it is a
named constant instead of a literal buried in the walker.
"""

ARTIFACT_PIPELINE_INPUT_KEYS = (
    "pipeline",
    "definition",
)
"""Task input names whose value is another pipeline an artifact is downloaded from.

``pipeline`` is what ``DownloadPipelineArtifact`` calls the producing
definition; ``definition`` is the same thing under ``DownloadBuildArtifacts``.
Either may hold a numeric definition identifier or a pipeline name.
"""

ARTIFACT_DOWNLOAD_TASKS = (
    "downloadpipelineartifact",
    "downloadbuildartifacts",
)
"""Tasks that download another pipeline's artifacts, in lower case without their version.

The inputs above are only read on these tasks. Other tasks use the same input
names for unrelated things, and a dependency invented from one of those is
worse than a missing one.
"""

PIPELINE_RESOURCE_ALIAS_KEY = "pipeline"
"""Key that marks a pipeline resource entry, holding the alias the steps refer to."""

PIPELINE_RESOURCE_SOURCE_KEY = "source"
"""Key on a pipeline resource entry that names the pipeline the artifacts come from."""

TASK_KEYS = (
    "task",
    "taskName",
)
"""Keys that mark a step as a task, so only real task inputs are read."""

INPUTS_KEY = "inputs"
"""Key holding a task's inputs."""

SELF_REPOSITORY_ALIAS = "self"
"""Repository alias Azure DevOps uses for the pipeline's own repository."""

UNRESOLVED_PIPELINE_PREFIX = "name:"
"""Marks a pipeline node identified only by a name this scan could not resolve."""

VARIABLE_EXPRESSION_MARKERS = (
    "$(",
    "${{",
)
"""Markers of a runtime or compile-time expression, whose value is not a name."""

EDGE_KINDS_NOT_ATTEMPTED = (
    EdgeKind.CHECKS_OUT.value,
    EdgeKind.TRIGGERED_BY.value,
    EdgeKind.NEEDS_SECURE_FILE.value,
    EdgeKind.USES_CONTAINER.value,
)
"""Dependency kinds this derivation does not look for at all.

Recorded in the coverage so an empty result for one of them reads as "not
looked at" rather than "looked at and not there".
"""

_METHOD_BY_KIND = {
    EdgeKind.BUILDS: "inventory_repository_column",
    EdgeKind.NEEDS_CREDENTIAL: "inventory_service_connection_reference",
    EdgeKind.NEEDS_VARIABLE_GROUP: "inventory_variable_group",
    EdgeKind.DEPLOYS_TO: "inventory_environment",
    EdgeKind.RUNS_ON_POOL: "inventory_agent_pool",
    EdgeKind.EXTENDS_TEMPLATE_IN: "inventory_template_reference",
    EdgeKind.CONSUMES_FEED: "stored_yaml_feed_input",
    EdgeKind.CONSUMES_ARTIFACT_OF: "stored_yaml_artifact_reference",
}
"""The code path that finds each dependency kind, recorded so a mistake can be traced."""


def _is_literal(value: object) -> bool:
    """Say whether a stored value is a plain name rather than an expression.

    Args:
        value: Value read from a task input or a resource entry.

    Returns:
        ``True`` when the value names something, ``False`` when it is empty or
        built from a variable expression. A plain number counts: an unquoted
        definition identifier is read back as one.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if not isinstance(value, str) or not value.strip():
        return False
    return not any(marker in value for marker in VARIABLE_EXPRESSION_MARKERS)


def _task_name(step: dict) -> str:
    """Return a step's task name without its version, in lower case.

    Args:
        step: A parsed step, which may or may not run a task.

    Returns:
        The task name ready to compare, empty when the step runs no task.
    """
    for key in TASK_KEYS:
        value = step.get(key)
        if isinstance(value, str) and value.strip():
            return value.split("@", 1)[0].strip().lower()
    return ""


def _walk_feed_refs(obj: object, out: set[str]) -> None:
    """Collect every artifact feed named by a task input in a parsed document.

    Args:
        obj: Any node of the parsed document.
        out: Set the feed names are added to.
    """
    if isinstance(obj, dict):
        if any(key in obj for key in TASK_KEYS):
            inputs = obj.get(INPUTS_KEY, {})
            if isinstance(inputs, dict):
                for key, val in inputs.items():
                    if key in FEED_INPUT_KEYS and _is_literal(val):
                        out.add(str(val).strip())
        for v in obj.values():
            _walk_feed_refs(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk_feed_refs(item, out)


def _walk_artifact_refs(obj: object, out: set[str]) -> None:
    """Collect every other pipeline whose artifacts a parsed document consumes.

    Both forms are read: a pipeline resource, which names the producing
    pipeline outright, and a download task, whose input holds either a
    definition identifier or a pipeline name.

    Args:
        obj: Any node of the parsed document.
        out: Set the pipeline references are added to.
    """
    if isinstance(obj, dict):
        if PIPELINE_RESOURCE_ALIAS_KEY in obj and PIPELINE_RESOURCE_SOURCE_KEY in obj:
            source = obj[PIPELINE_RESOURCE_SOURCE_KEY]
            if _is_literal(source):
                out.add(str(source).strip())
        if _task_name(obj) in ARTIFACT_DOWNLOAD_TASKS:
            inputs = obj.get(INPUTS_KEY, {})
            if isinstance(inputs, dict):
                for key, val in inputs.items():
                    if key in ARTIFACT_PIPELINE_INPUT_KEYS and _is_literal(val):
                        out.add(str(val).strip())
        for v in obj.values():
            _walk_artifact_refs(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk_artifact_refs(item, out)


@dataclass
class _Row:
    """One inventory row that could be read, with the pipeline record it holds."""

    project: str
    meta: PipelineMetadata


def _read_rows(rows: list[dict]) -> tuple[list[_Row], int]:
    """Turn stored inventory rows into pipeline records, skipping the unreadable ones.

    Args:
        rows: Rows as the state store returned them.

    Returns:
        The rows that could be read, and how many could not.
    """
    read: list[_Row] = []
    unreadable = 0
    for row in rows:
        try:
            meta = PipelineMetadata.from_dict(json.loads(row.get("metadata_json") or "{}"))
        except Exception as exc:
            unreadable += 1
            log.warning(
                "  Knowledge base: inventory row %s/%s could not be read: %s",
                row.get("project", "?"), row.get("pipeline_id", "?"), exc,
            )
            continue
        read.append(_Row(project=meta.project or str(row.get("project", "")), meta=meta))
    return read, unreadable


class _Derivation:
    """One pass of turning inventory rows into nodes and dependencies.

    Holds the scan being recorded against and the identifiers already handed
    out, so a thing mentioned by ten pipelines is stored once and every
    dependency points at the same node.
    """

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        profile_id: str,
        organization: str,
        scan: KnowledgeScan,
    ) -> None:
        """Hold the store and the scan every record written by this pass belongs to.

        Args:
            store: Where nodes and dependencies are written.
            profile_id: Migration profile the facts belong to.
            organization: Organisation the inventory was read from.
            scan: The scan in progress.
        """
        self._store = store
        self._profile_id = profile_id
        self._organization = organization
        self._scan = scan
        self._seen_at = utc_now_text()
        self._node_ids: dict[tuple[str, str, str], str] = {}
        self._edge_keys: set[tuple[str, str, str]] = set()
        self._edges_by_kind: dict[str, int] = {}
        self._pipeline_ids_by_name: dict[tuple[str, str], int] = {}
        self._repo_ids_by_name: dict[tuple[str, str], str] = {}
        self._source_node_id = ""
        self._locator = ""

    # ── recording ───────────────────────────────────────────────────────────

    def _node(self, kind: NodeKind, project: str, identity_key: str, name: str) -> str:
        """Record one thing and return its identifier, storing it only once per pass.

        Args:
            kind: What the thing is.
            project: Project it belongs to, empty when it sits above project level.
            identity_key: Most stable name available for matching.
            name: Human-readable name for an answer.

        Returns:
            The identifier of the stored node.
        """
        cache_key = (kind.value, project, identity_key)
        held = self._node_ids.get(cache_key)
        if held:
            return held
        node_id = self._store.record_node(KnowledgeNode(
            node_id="",
            profile_id=self._profile_id,
            system=SOURCE_SYSTEM,
            organization=self._organization,
            project=project,
            kind=kind,
            identity_key=identity_key,
            name=name,
            source_locator=self._locator,
            last_seen_at=self._seen_at,
            last_scan_id=self._scan.scan_id,
        ))
        self._node_ids[cache_key] = node_id
        return node_id

    def _edge(
        self,
        target_node_id: str,
        kind: EdgeKind,
        confidence: Confidence,
        evidence: dict[str, Any],
    ) -> None:
        """Record one dependency from the pipeline being read to the thing it needs.

        Args:
            target_node_id: The thing being relied on.
            kind: How the pipeline depends on it.
            confidence: How firmly the dependency is believed.
            evidence: Already-masked details that justify it; never a secret value.
        """
        if not target_node_id or target_node_id == self._source_node_id:
            return
        method = _METHOD_BY_KIND[kind]
        key = (self._source_node_id, target_node_id, kind.value)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self._store.record_edge(KnowledgeEdge(
            edge_id="",
            profile_id=self._profile_id,
            source_node_id=self._source_node_id,
            target_node_id=target_node_id,
            kind=kind,
            confidence=confidence,
            extraction_method=method,
            source_locator=self._locator,
            evidence=evidence,
            last_seen_at=self._seen_at,
            last_scan_id=self._scan.scan_id,
        ))
        self._edges_by_kind[kind.value] = self._edges_by_kind.get(kind.value, 0) + 1

    # ── derivation ──────────────────────────────────────────────────────────

    def index(self, rows: list[_Row]) -> None:
        """Note what each project holds, so a reference by name resolves to the same node.

        Args:
            rows: The inventory rows that could be read.
        """
        for row in rows:
            name = row.meta.pipeline_name.strip().lower()
            if name:
                self._pipeline_ids_by_name[(row.project, name)] = row.meta.pipeline_id
            repo = row.meta.repo_name.strip().lower()
            if repo and row.meta.repo_id:
                self._repo_ids_by_name[(row.project, repo)] = row.meta.repo_id

    def derive(self, row: _Row) -> None:
        """Record every dependency one inventory row carries.

        Args:
            row: The row to read.
        """
        meta = row.meta
        self._locator = f"{row.project}/pipeline/{meta.pipeline_id}"
        self._source_node_id = self._node(
            NodeKind.PIPELINE, row.project, str(meta.pipeline_id), meta.pipeline_name,
        )
        repo_node_id = self._repository(row)
        if repo_node_id:
            self._edge(repo_node_id, EdgeKind.BUILDS, Confidence.DECLARED, {
                "repository_name": meta.repo_name,
                "branch": meta.repo_branch,
            })
        self._credentials(row)
        self._variable_groups(row)
        self._environments(row)
        self._pools(row)
        self._templates(row, repo_node_id)

    def _repository(self, row: _Row) -> str:
        """Record the repository the pipeline builds.

        Args:
            row: The row being read.

        Returns:
            The repository's node identifier, empty when the row names none.
        """
        meta = row.meta
        identity = meta.repo_id or meta.repo_name.strip().lower()
        if not identity:
            return ""
        return self._node(
            NodeKind.REPOSITORY, row.project, identity, meta.repo_name or identity,
        )

    def _credentials(self, row: _Row) -> None:
        """Record the service connections the pipeline was seen to reference.

        The stored row does not say which heuristic matched the connection, so
        every one of these is inferred.

        Args:
            row: The row being read.
        """
        for connection in row.meta.service_connections or []:
            if not isinstance(connection, dict):
                continue
            name = str(connection.get("name", "")).strip()
            if not name:
                continue
            node_id = self._node(
                NodeKind.SERVICE_CONNECTION, row.project, name.lower(), name,
            )
            self._edge(node_id, EdgeKind.NEEDS_CREDENTIAL, Confidence.INFERRED, {
                "connection_name": name,
                "connection_type": str(connection.get("type", "unknown")),
            })

    def _variable_groups(self, row: _Row) -> None:
        """Record the variable groups the pipeline declares.

        Args:
            row: The row being read.
        """
        for group in row.meta.variable_groups or []:
            if not isinstance(group, dict):
                continue
            name = str(group.get("name", "")).strip()
            if not name:
                continue
            node_id = self._node(
                NodeKind.VARIABLE_GROUP, row.project, name.lower(), name,
            )
            secrets = [str(n) for n in group.get("secret_variables", []) or []]
            self._edge(node_id, EdgeKind.NEEDS_VARIABLE_GROUP, Confidence.DECLARED, {
                "group_name": name,
                # Names only. A variable's value is never read or stored here.
                "secret_variable_names": secrets,
            })

    def _environments(self, row: _Row) -> None:
        """Record the environments the pipeline deploys to.

        Args:
            row: The row being read.
        """
        names = [e.name for e in row.meta.environments or [] if e.name]
        names += [
            stage.environment.name
            for stage in row.meta.stages or []
            if stage.environment and stage.environment.name
        ]
        for name in names:
            clean = str(name).strip()
            if not clean:
                continue
            node_id = self._node(
                NodeKind.ENVIRONMENT, row.project, clean.lower(), clean,
            )
            self._edge(node_id, EdgeKind.DEPLOYS_TO, Confidence.DECLARED, {
                "environment_name": clean,
            })

    def _pools(self, row: _Row) -> None:
        """Record the agent pools the pipeline runs on.

        Pools sit above the project, so their nodes carry no project.

        Args:
            row: The row being read.
        """
        for pool in row.meta.agent_pools or []:
            clean = str(pool).strip()
            if not clean:
                continue
            node_id = self._node(NodeKind.AGENT_POOL, "", clean.lower(), clean)
            self._edge(node_id, EdgeKind.RUNS_ON_POOL, Confidence.DECLARED, {
                "pool_name": clean,
            })

    def _templates(self, row: _Row, own_repo_node_id: str) -> None:
        """Record the repositories the pipeline extends templates from.

        A reference that names a repository is declared. A bare path had its
        repository guessed as the pipeline's own, so it is inferred.

        Args:
            row: The row being read.
            own_repo_node_id: Node of the repository the pipeline builds.
        """
        for record in row.meta.template_refs or []:
            if not isinstance(record, dict):
                continue
            ref = str(record.get("ref", "")).strip()
            if not ref:
                continue
            alias = str(record.get("repository", "")).strip()
            if not alias or alias.lower() == SELF_REPOSITORY_ALIAS:
                node_id = own_repo_node_id
                confidence = (
                    Confidence.DECLARED if alias else Confidence.INFERRED
                )
            else:
                identity = self._repo_ids_by_name.get(
                    (row.project, alias.lower()), alias.lower(),
                )
                node_id = self._node(
                    NodeKind.REPOSITORY, row.project, identity, alias,
                )
                confidence = Confidence.DECLARED
            self._edge(node_id, EdgeKind.EXTENDS_TEMPLATE_IN, confidence, {
                "template_ref": ref,
                "repository_alias": alias,
            })

    def walk_stored_text(self, row: _Row) -> bool:
        """Record what the pipeline text stored on the row says it consumes.

        The text is already on the row, so this costs no call to Azure DevOps.

        Args:
            row: The row being read.

        Returns:
            ``True`` when the stored text was read, ``False`` when it was
            missing or could not be parsed.
        """
        text = row.meta.yaml_content or ""
        if not text.strip():
            return False
        try:
            doc = yaml.safe_load(text)
        except Exception as exc:
            log.warning(
                "  Knowledge base: stored pipeline text for %s could not be read: %s",
                self._locator, exc,
            )
            return False

        feeds: set[str] = set()
        _walk_feed_refs(doc, feeds)
        for feed in sorted(feeds):
            node_id = self._node(
                NodeKind.ARTIFACT_FEED, row.project, feed.lower(), feed,
            )
            self._edge(node_id, EdgeKind.CONSUMES_FEED, Confidence.DECLARED, {
                "feed": feed,
            })

        producers: set[str] = set()
        _walk_artifact_refs(doc, producers)
        for producer in sorted(producers):
            node_id, confidence = self._producer_node(row.project, producer)
            self._edge(node_id, EdgeKind.CONSUMES_ARTIFACT_OF, confidence, {
                "pipeline_reference": producer,
            })
        return True

    def _producer_node(self, project: str, reference: str) -> tuple[str, Confidence]:
        """Find the pipeline an artifact reference points at.

        Args:
            project: Project the referring pipeline belongs to.
            reference: Definition identifier or pipeline name as written.

        Returns:
            The producing pipeline's node identifier and how firmly the match
            is believed: declared when the reference named an identifier,
            resolved when a name matched a pipeline this scan read, inferred
            when only the name is known.
        """
        if reference.isdigit():
            return (
                self._node(NodeKind.PIPELINE, project, reference, reference),
                Confidence.DECLARED,
            )
        known = self._pipeline_ids_by_name.get((project, reference.lower()))
        if known is not None:
            return (
                self._node(NodeKind.PIPELINE, project, str(known), reference),
                Confidence.RESOLVED,
            )
        return (
            self._node(
                NodeKind.PIPELINE,
                project,
                f"{UNRESOLVED_PIPELINE_PREFIX}{reference.lower()}",
                reference,
            ),
            Confidence.INFERRED,
        )

    def close(
        self,
        *,
        status: str,
        counts: tuple[int, int, int, int] = (0, 0, 0, 0),
        error: str = "",
    ) -> KnowledgeScan:
        """Mark what this pass no longer saw, close the scan and return it.

        Coverage is recorded because it is the difference between "nothing
        depends on this" and "nothing this pass could see depends on this".

        Args:
            status: How the pass ended.
            counts: Rows read, rows whose stored record could not be read,
                rows whose stored pipeline text could not be parsed, and rows
                holding no pipeline text at all.
            error: Short description of what went wrong, empty on success.

        Returns:
            The scan as it was stored, carrying its coverage.
        """
        disappeared = self._store.mark_unseen_edges_disappeared(
            profile_id=self._profile_id, scan_id=self._scan.scan_id,
        ) if status == "completed" else 0
        rows_read, rows_unreadable, unreadable_text, without_text = counts
        coverage: dict[str, Any] = {
            "source": "pipeline_inventory",
            "rows_read": rows_read,
            "rows_unreadable": rows_unreadable,
            "pipelines_with_unreadable_text": unreadable_text,
            "pipelines_without_stored_text": without_text,
            "nodes_recorded": len(self._node_ids),
            "edges_recorded": sum(self._edges_by_kind.values()),
            "edges_by_kind": dict(sorted(self._edges_by_kind.items())),
            "edges_marked_disappeared": disappeared,
            "edge_kinds_not_attempted": list(EDGE_KINDS_NOT_ATTEMPTED),
        }
        summary = f"pipeline inventory could not be read: {error}" if error else ""
        self._store.finish_scan(
            self._scan.scan_id, status=status, coverage=coverage, error_summary=summary,
        )
        return replace(
            self._scan,
            completed_at=utc_now_text(),
            status=status,
            coverage=coverage,
            error_summary=summary,
        )


def build_knowledge_base(
    store: KnowledgeStore,
    db: StateDBBase,
    *,
    profile_id: str,
    source_scope: str,
) -> KnowledgeScan:
    """Record what the pipeline inventory already knows about how things depend on each other.

    Reads the inventory rows this platform collected earlier and writes one
    node per thing and one dependency per fact found on the row. No Azure
    DevOps call is made, so the answer is only ever as fresh as the last
    inventory run, which is why the scan carries its coverage.

    A row that cannot be read is counted and passed over; one bad row never
    stops the pass. Running twice over unchanged rows rewrites the same rows
    under the same identifiers.

    Args:
        store: Knowledge store the nodes and dependencies are written to.
        db: State store holding the pipeline inventory.
        profile_id: Migration profile the facts belong to.
        source_scope: What was asked for, such as an organisation, recorded on
            the scan and used as the organisation the things belong to.

    Returns:
        The finished scan, carrying what the pass covered.
    """
    scan = store.start_scan(
        profile_id=profile_id,
        source_scope=source_scope,
        extractor_version=EXTRACTOR_VERSION,
    )
    derivation = _Derivation(
        store, profile_id=profile_id, organization=source_scope, scan=scan,
    )
    try:
        raw_rows = db.get_all_inventory()
    except Exception as exc:
        return derivation.close(status="failed", error=str(exc))

    rows, rows_unreadable = _read_rows(raw_rows)
    derivation.index(rows)
    unreadable_text = 0
    without_text = 0
    for row in rows:
        try:
            derivation.derive(row)
            if not (row.meta.yaml_content or "").strip():
                without_text += 1
            elif not derivation.walk_stored_text(row):
                unreadable_text += 1
        except Exception as exc:
            rows_unreadable += 1
            log.warning(
                "  Knowledge base: pipeline %s/%s could not be recorded: %s",
                row.project, row.meta.pipeline_id, exc,
            )
    return derivation.close(
        status="completed",
        counts=(len(rows), rows_unreadable, unreadable_text, without_text),
    )
