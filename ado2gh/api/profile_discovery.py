"""Bridge profile discovery scans to migration state (risk scores, waves)."""
from __future__ import annotations

from typing import Any

from ado2gh.api.profile_governance import assert_profile_active_for_run, ProfileGovernanceError

from ado2gh.api.migration_scan import load_scan_results, persist_scan_results, scan_with_credentials
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
    **kwargs: Any,
) -> str:
    org = resolve_gh_org(profile, **kwargs)
    if not org:
        raise ValueError(
            "GitHub target organization (gh_org) is not configured. "
            "Set it under Settings → Profiles → GitHub org, "
            "or set global.gh_org in migration.yaml."
        )
    return org


def risk_score_from_repo_dict(repo: dict[str, Any], gh_org: str = "") -> RiskScore:
    phase_str = repo.get("assigned_phase") or repo.get("suggested_phase") or "poc"
    return RiskScore(
        project=repo.get("project", ""),
        repo_name=repo.get("repo_name", ""),
        total_score=float(repo.get("total_score", 0)),
        assigned_phase=phase_str,
        gh_org=repo.get("gh_org") or gh_org,
        gh_repo=repo.get("gh_repo") or repo.get("repo_name", ""),
        pipeline_count=int(repo.get("pipeline_count", 0)),
        branch_count=int(repo.get("branch_count", 0)),
        last_commit_days=int(repo.get("last_commit_days", 0)),
    )


def manual_phase_overrides(repos: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """Repo keys where the operator assigned a phase different from the risk suggestion."""
    overrides: dict[tuple[str, str], str] = {}
    for repo in repos:
        project = (repo.get("project") or "").strip()
        repo_name = (repo.get("repo_name") or "").strip()
        if not project or not repo_name:
            continue
        assigned = (repo.get("assigned_phase") or "").strip()
        suggested = (repo.get("suggested_phase") or "").strip()
        if assigned and suggested and assigned != suggested:
            overrides[(project, repo_name)] = assigned
    return overrides


def iter_scan_repos(scan: dict[str, Any]) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    for bucket in scan.get("recommendations", {}).values():
        for repo in bucket.get("repos", []):
            item = dict(repo)
            if not item.get("assigned_phase"):
                item["assigned_phase"] = bucket.get("phase") or item.get("suggested_phase")
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
    from ado2gh.api.settings_store import SettingsStore
    store_ref = settings or SettingsStore()
    adv = store_ref.load().advanced
    gh_org = resolve_gh_org(profile, config_path=adv.config_path)
    raw = scan_with_credentials(
        profile.ado_org_url,
        profile.ado_pat,
        gh_org=gh_org,
        phase_definitions=[p.to_dict() for p in store_ref.get_phases()],
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
    parallel: int = 4,
    pipeline_parallel: int = 8,
    config_path: str | None = None,
) -> WaveConfig | None:
    """Build a migration wave from profile discovery assignments."""
    try:
        assert_profile_active_for_run(profile)
    except ProfileGovernanceError as exc:
        raise ValueError(exc.code) from exc
    db = get_state_db(db_path)
    gh_org = resolve_gh_org(profile, config_path=config_path)
    repos: list[RepoConfig] = []

    if hasattr(db, "get_profile_scan_repos"):
        rows = db.get_profile_scan_repos(profile.id, phase=phase)
        for row in rows:
            assigned = row.get("assigned_phase") or row.get("suggested_phase") or phase
            if assigned != phase:
                continue
            target_org = (row.get("gh_org") or gh_org or "").strip()
            repos.append(RepoConfig(
                ado_project=row["project"],
                ado_repo=row["repo_name"],
                gh_org=target_org,
                gh_repo=row.get("gh_repo") or row["repo_name"],
                phase=phase,
                risk_score=float(row.get("total_score", 0)),
                scopes=["repo"],
            ))

    if not repos:
        scores = db.get_risk_scores_for_phase(phase)
        for row in scores:
            target_org = (row.get("gh_org") or gh_org or "").strip()
            repos.append(RepoConfig(
                ado_project=row["project"],
                ado_repo=row["repo_name"],
                gh_org=target_org,
                gh_repo=row.get("gh_repo") or row["repo_name"],
                phase=phase,
                risk_score=float(row.get("total_score", 0)),
                scopes=["repo"],
            ))

    if not repos:
        return None
    return WaveConfig(
        wave_id=wave_id,
        name=f"profile-{phase}",
        description=f"Repos assigned to {phase} from profile discovery",
        repos=repos,
        parallel=parallel,
        pipeline_parallel=pipeline_parallel,
        phase=phase,
    )


def repo_configs_for_phase(
    phase: str,
    profile: MigrationProfile,
    db_path: str | None = None,
    config_path: str | None = None,
) -> list[RepoConfig]:
    wave = build_wave_from_profile_phase(
        phase, profile, db_path=db_path, config_path=config_path,
    )
    return wave.repos if wave else []
