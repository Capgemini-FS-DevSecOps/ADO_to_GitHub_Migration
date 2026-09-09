"""Collect a repository's ADO risk signals and hand them to :class:`RiskScorer`.

Two callers score repositories: the accelerator's org scan
(``ado2gh/api/migration_scan.py``) and the ``ado2gh phase assign`` command. The
per-repository half of that work — fetching branch stats and the last commit,
then scoring — lives here so the two cannot drift apart.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ado2gh.logging_config import log
from ado2gh.models import RiskScore
from ado2gh.phase.risk_scorer import RiskScorer

if TYPE_CHECKING:
    from ado2gh.clients.ado_client import ADOClient
    from ado2gh.models import PipelineMetadata
    from ado2gh.state.db import StateDB


def score_repo(
    ado: ADOClient,
    repo_id: str,
    score: RiskScore,
    pipelines: list[PipelineMetadata],
    scorer: RiskScorer | None = None,
) -> RiskScore:
    """Add the branch and commit signals to a part-filled score, then score it.

    The caller supplies the repository's identity and the counts it already
    knows (size, variable groups, service connections) on ``score``; this
    function fetches what only ADO can answer per repository. Both lookups are
    best-effort: a repository whose stats or commits cannot be read is still
    scored, from the signals that were readable.

    Args:
        ado: Client for the organisation the repository lives in.
        repo_id: ADO repository id, used for the stats and commit lookups.
        score: Part-filled record; ``branch_count`` and the derived fields are
            set on it in place.
        pipelines: The repository's pipelines, for the pipeline signals.
        scorer: Scorer to use; a fresh :class:`RiskScorer` by default.

    Returns:
        The same ``score``, with its signals and total filled in.
    """
    try:
        stats = ado.get_repo_stats(score.project, repo_id)
    except Exception as exc:
        log.warning("repo stats unavailable for %s/%s: %s",
                    score.project, score.repo_name, exc)
        stats = {}
    score.branch_count = stats.get("branch_count", 0)

    try:
        commits = ado.get_repo_commits(score.project, repo_id, top=1)
    except Exception as exc:
        log.warning("commit history unavailable for %s/%s: %s",
                    score.project, score.repo_name, exc)
        commits = []

    return (scorer or RiskScorer()).score(score, pipelines, commits)


def score_org_repos(ado: ADOClient, db: StateDB, gh_org: str = "") -> list[RiskScore]:
    """Score every enabled repository in the ADO organisation.

    Pipelines come from the state database's inventory, populated by
    ``ado2gh pipelines inventory``, rather than a second crawl of ADO. A
    repository with no inventory rows still scores, on its remaining signals.
    Disabled repositories are skipped: they are not migrated.

    Args:
        ado: Client for the organisation to scan.
        db: State database holding the pipeline inventory.
        gh_org: Default GitHub org recorded on each score.

    Returns:
        One scored record per enabled repository, in discovery order.
    """
    scorer = RiskScorer()
    scores: list[RiskScore] = []
    for project in _listing(ado.list_projects):
        name = project.get("name", "")
        if not name:
            continue
        var_groups = _listing(ado.list_variable_groups, name)
        svc_conns = _listing(ado.list_service_connections, name)
        for repo in _listing(ado.list_repos, name):
            repo_name = repo.get("name", "")
            if not repo_name or repo.get("isDisabled"):
                continue
            scores.append(score_repo(
                ado,
                repo.get("id", ""),
                RiskScore(
                    project=name,
                    repo_name=repo_name,
                    gh_org=gh_org,
                    size_kb=repo.get("size", 0),
                    variable_group_count=len(var_groups),
                    service_connection_count=len(svc_conns),
                ),
                db.get_pipelines_for_repo(name, repo_name),
                scorer,
            ))
    return scores


def _listing(fetch: Callable[..., list[dict]], *args: str) -> list[dict]:
    """Call an ADO listing method, degrading to an empty list if it fails.

    Args:
        fetch: The listing method to call.
        *args: Its arguments, normally the project name.

    Returns:
        What ``fetch`` returned, or ``[]`` if it raised.
    """
    try:
        return fetch(*args)
    except Exception as exc:
        log.warning("ADO listing %s%s failed: %s", fetch.__name__, args, exc)
        return []
