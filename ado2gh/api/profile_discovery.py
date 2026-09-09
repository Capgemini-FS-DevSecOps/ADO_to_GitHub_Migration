"""Bridge profile discovery scans to migration state (risk scores, waves)."""
from __future__ import annotations

from typing import Any

from ado2gh.api.migration_scan import load_scan_results, persist_scan_results, scan_with_credentials
from ado2gh.api.profile_governance import ProfileGovernanceError, assert_profile_active_for_run
from ado2gh.api.settings_store import MigrationProfile, SettingsStore
from ado2gh.api.state_db import get_state_db
from ado2gh.models import RepoConfig, RiskScore, WaveConfig


def resolve_gh_org(
    profile: MigrationProfile | None = None,
    *,
    global_cfg: dict[str, Any] | None = None,
    config_path: str | None = None,
    scan: dict[str, Any] | None = None,
    profile_id: str | None = None,
) -> str:
    """Resolve GitHub target org: profile → migration.yaml → scan metadata."""
    if profile and (profile.gh_org or "").strip():
        return profile.gh_org.strip()
    if global_cfg is None and config_path:
        from ado2gh.core.config_loader import ConfigLoader
        global_cfg, _ = ConfigLoader.load(config_path)
    if global_cfg and str(global_cfg.get("gh_org", "")).strip():
        return str(global_cfg["gh_org"]).strip()
    if scan and str(scan.get("gh_org", "")).strip():
        return str(scan["gh_org"]).strip()
    pid = profile_id or (profile.id if profile else None)
    if pid:
        cached = load_scan_results(pid)
        if cached and str(cached.get("gh_org", "")).strip():
            return str(cached["gh_org"]).strip()
    return ""


def require_gh_org(
    profile: MigrationProfile | None = None,
    *,
    global_cfg: dict[str, Any] | None = None,
    config_path: str | None = None,
    scan: dict[str, Any] | None = None,
    profile_id: str | None = None,
) -> str:
    """Resolve the GitHub target org, refusing to continue without one.

    Args:
        profile: Profile whose configured GitHub organization wins if set.
        global_cfg: Already-loaded migration config to read the fallback
            organization from.
        config_path: Migration config file to load the fallback
            organization from when one was not passed in.
        scan: Scan result to read the organization recorded at scan time
            from.
        profile_id: Profile whose persisted scan is consulted as the last
            fallback. Defaults to the given profile's identifier.

    Returns:
        str: The resolved GitHub organization name, never empty.

    Raises:
        ValueError: If no source names a target organization, with a
            message telling the operator where to configure one.

    """
    org = resolve_gh_org(
        profile,
        global_cfg=global_cfg,
        config_path=config_path,
        scan=scan,
        profile_id=profile_id,
    )
    if not org:
        raise ValueError(
            "GitHub target organization (gh_org) is not configured. "
            "Set it under Settings → Profiles → GitHub org, "
            "or set global.gh_org in migration.yaml."
        )
    return org


def risk_score_from_repo_dict(repo: dict[str, Any], gh_org: str = "") -> RiskScore:
    """Convert one scanned repository record into a risk score row.

    Args:
        repo: Scanned repository record holding its ADO project and name,
            total risk score, target GitHub names and the pipeline, branch
            and staleness counts the score was derived from.
        gh_org: Target GitHub organization to use when the record does not
            name one of its own.

    Returns:
        RiskScore: The repository's score ready to persist, with no phase
        assigned yet and its target GitHub repository defaulting to the
        ADO repository name. Missing counts become zero.

    """
    return RiskScore(
        project=repo.get("project", ""),
        repo_name=repo.get("repo_name", ""),
        total_score=float(repo.get("total_score", 0)),
        assigned_phase=None,
        gh_org=repo.get("gh_org") or gh_org,
        gh_repo=repo.get("gh_repo") or repo.get("repo_name", ""),
        pipeline_count=int(repo.get("pipeline_count", 0)),
        branch_count=int(repo.get("branch_count", 0)),
        last_commit_days=int(repo.get("last_commit_days", 0)),
    )


def manual_phase_overrides(repos: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """Detect repos where assigned_phase differs from suggested_phase (manual overrides)."""
    overrides: dict[tuple[str, str], str] = {}
    for r in repos:
        assigned = r.get("assigned_phase")
        suggested = r.get("suggested_phase")
        if assigned and suggested and assigned != suggested:
            overrides[(r.get("project", ""), r.get("repo_name", ""))] = assigned
    return overrides


def iter_scan_repos(scan: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten every repository out of a scan's per-phase recommendations.

    Args:
        scan: Scan result whose recommendations are grouped by phase.

    Returns:
        list[dict[str, Any]]: A copy of each scanned repository record
        across all phases, with the assigned and suggested phase fields
        stripped so callers cannot mistake a recommendation for a
        decision.

    """
    repos: list[dict[str, Any]] = []
    for bucket in scan.get("recommendations", {}).values():
        for repo in bucket.get("repos", []):
            item = dict(repo)
            item.pop("assigned_phase", None)
            item.pop("suggested_phase", None)
            repos.append(item)
    return repos


def sync_profile_scan_to_risk_scores(
    profile_id: str,
    scan: dict[str, Any] | None = None,
    db_path: str | None = None,
    config_path: str | None = None,
) -> int:
    """Copy profile discovery repos into repo_risk_scores for migration runs."""
    db = get_state_db(db_path)
    if scan is None and hasattr(db, "build_profile_scan_payload"):
        data = db.build_profile_scan_payload(profile_id)
    else:
        data = scan or load_scan_results(profile_id)
    if not data:
        return 0
    store = SettingsStore()
    profile = store.get_profile(profile_id)
    gh_org = resolve_gh_org(profile, config_path=config_path, scan=data, profile_id=profile_id)
    scan_keys: set[tuple[str, str]] = set()
    count = 0
    for repo in iter_scan_repos(data):
        if not repo.get("project") or not repo.get("repo_name"):
            continue
        key = (repo["project"], repo["repo_name"])
        scan_keys.add(key)
        db.upsert_risk_score(risk_score_from_repo_dict(repo, gh_org=gh_org))
        count += 1
    if scan_keys and hasattr(db, "prune_risk_scores_not_in"):
        db.prune_risk_scores_not_in(scan_keys)
    return count


def ensure_profile_scan(profile: MigrationProfile, settings: SettingsStore | None = None) -> dict[str, Any]:
    """Load persisted scan or run a fresh ADO scan for the profile."""
    try:
        assert_profile_active_for_run(profile)
    except ProfileGovernanceError as exc:
        raise ValueError(exc.code) from exc
    store = settings or SettingsStore()
    existing = load_scan_results(profile.id)
    if existing and existing.get("repos_scanned", 0) > 0:
        return existing
    if not profile.ado_org_url or not profile.ado_pat:
        raise ValueError("Profile is missing ADO credentials — complete profile setup first")
    adv = store.load().advanced
    gh_org = resolve_gh_org(profile, config_path=adv.config_path)
    raw = scan_with_credentials(
        profile.ado_org_url,
        profile.ado_pat,
        gh_org=gh_org,
    )
    if gh_org and not raw.get("gh_org"):
        raw["gh_org"] = gh_org
    persist_scan_results(profile.id, raw)
    store.record_scan_summary(profile.id, raw)
    return raw


def build_wave_from_profile_phase(
    phase: str,
    profile: MigrationProfile,
    *,
    wave_id: int = 9000,
    db_path: str | None = None,
    config_path: str | None = None,
) -> WaveConfig | None:
    """Build a migration wave from a profile's discovered repositories.

    Args:
        phase: Phase to keep; repositories explicitly assigned to a
            different phase are left out. An empty value keeps everything.
        profile: Profile whose discovery results the wave is built from.
        wave_id: Identifier to stamp on the wave.
        db_path: State database to read the discovery results from.
            Defaults to the configured database.
        config_path: Migration config file used to resolve the target
            GitHub organization for repositories that do not name one.

    Returns:
        WaveConfig | None: A wave holding one repository entry per
        discovered repository, scoped to the repository itself and
        carrying its risk score, with the default concurrency limits for
        the caller to override. ``None`` when the profile has no
        discovered repositories and no risk scores to fall back on.

    Raises:
        ValueError: With the governance error code as its message when the
            profile is not active.

    """
    try:
        assert_profile_active_for_run(profile)
    except ProfileGovernanceError as exc:
        raise ValueError(exc.code) from exc
    db = get_state_db(db_path)
    gh_org = resolve_gh_org(profile, config_path=config_path)
    repos: list[RepoConfig] = []

    if hasattr(db, "get_profile_scan_repos"):
        for row in db.get_profile_scan_repos(profile.id):
            if phase and row.get("assigned_phase") and row["assigned_phase"] != phase:
                continue
            target_org = (row.get("gh_org") or gh_org or "").strip()
            repos.append(RepoConfig(
                ado_project=row["project"],
                ado_repo=row["repo_name"],
                gh_org=target_org,
                gh_repo=row.get("gh_repo") or row["repo_name"],
                phase="",
                risk_score=float(row.get("total_score", 0)),
                scopes=["repo"],
            ))

    if not repos:
        scores = db.get_risk_scores_for_phase(None)
        for row in scores:
            target_org = (row.get("gh_org") or gh_org or "").strip()
            repos.append(RepoConfig(
                ado_project=row["project"],
                ado_repo=row["repo_name"],
                gh_org=target_org,
                gh_repo=row.get("gh_repo") or row["repo_name"],
                phase="",
                risk_score=float(row.get("total_score", 0)),
                scopes=["repo"],
            ))

    if not repos:
        return None
    return WaveConfig(
        wave_id=wave_id,
        name=f"profile-{phase}",
        description=f"Repos from profile discovery (phase {phase} ignored)",
        repos=repos,
        phase="",
    )


def repo_configs_for_phase(
    phase: str,
    profile: MigrationProfile,
    db_path: str | None = None,
    config_path: str | None = None,
) -> list[RepoConfig]:
    """Select the repositories a phase should migrate.

    Args:
        phase: Phase to select for; repositories explicitly assigned to a
            different phase are left out.
        profile: Profile whose discovery results are used.
        db_path: State database to read the discovery results from.
            Defaults to the configured database.
        config_path: Migration config file used to resolve the target
            GitHub organization for repositories that do not name one.

    Returns:
        list[RepoConfig]: One entry per selected repository, each scoped to
        the repository itself and carrying its target GitHub names and
        risk score. Empty when nothing was discovered for the profile.

    Raises:
        ValueError: With the governance error code as its message when the
            profile is not active.

    """
    wave = build_wave_from_profile_phase(
        phase, profile, db_path=db_path, config_path=config_path,
    )
    return wave.repos if wave else []
