"""Phase gate checker — validates success thresholds before advancing."""
from __future__ import annotations

from datetime import datetime, timezone

from ado2gh.logging_config import log
from ado2gh.models import (
    DEFAULT_PHASES, GateStatus, PhaseGateResult, PhaseType,
)
from ado2gh.state.db import StateDB


class PhaseGateChecker:
    def __init__(self, db: StateDB, phase_configs: dict = None):
        self.db = db
        self.phases = phase_configs or DEFAULT_PHASES

    def check(self, phase: PhaseType) -> PhaseGateResult:
        cfg = self.phases[phase]
        scores = self.db.get_risk_scores_for_phase(phase)
        if not scores:
            result = PhaseGateResult(
                phase=phase, status=GateStatus.FAIL,
                repo_success_pct=0.0, pipeline_success_pct=0.0,
                repos_completed=0, repos_total=0,
                pipelines_completed=0, pipelines_total=0,
                failures=["No repos assigned. Run: phase assign"],
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
            self.db.upsert_phase_gate(result)
            return result

        # Repository names are only unique inside an ADO project.  Every gate
        # calculation therefore uses the composite source identity; using a
        # repo-name IN clause can mix unrelated repositories across projects.
        repo_keys = {
            (str(score["project"]), str(score["repo_name"]))
            for score in scores
        }
        total_repos = len(repo_keys)
        failures: list[str] = []

        try:
            # Only the latest row for a logical scope is authoritative.  A
            # prior failed attempt in another wave must not poison a later
            # successful retry, nor may an old success mask a newer failure.
            with self.db._conn() as conn:
                migration_rows = conn.execute(
                    "WITH ranked AS ("
                    " SELECT m.ado_project,m.ado_repo,m.scope,m.status,m.id,"
                    "        m.gh_org,m.gh_repo,"
                    "        r.gh_org planned_gh_org,r.gh_repo planned_gh_repo,"
                    " ROW_NUMBER() OVER ("
                    "  PARTITION BY m.ado_project,m.ado_repo,m.scope "
                    "  ORDER BY m.id DESC"
                    " ) AS rn FROM migrations m"
                    " JOIN repo_risk_scores r"
                    "  ON r.project=m.ado_project AND r.repo_name=m.ado_repo"
                    " WHERE r.assigned_phase=?"
                    ") SELECT ado_project,ado_repo,"
                    " COUNT(*) total_scopes,"
                    " SUM(CASE WHEN status='completed'"
                    "  AND (planned_gh_org='' OR lower(planned_gh_org)=lower(gh_org))"
                    "  AND (planned_gh_repo='' OR lower(planned_gh_repo)=lower(gh_repo))"
                    "  THEN 1 ELSE 0 END) ok_scopes,"
                    " SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed_scopes,"
                    " SUM(CASE WHEN status='needs_review' THEN 1 ELSE 0 END) review_scopes,"
                    " SUM(CASE WHEN"
                    "  (planned_gh_org<>'' AND lower(planned_gh_org)<>lower(gh_org))"
                    "  OR (planned_gh_repo<>'' AND lower(planned_gh_repo)<>lower(gh_repo))"
                    "  THEN 1 ELSE 0 END) mapping_scopes"
                    " FROM ranked WHERE rn=1 GROUP BY ado_project,ado_repo"
                , (phase.value,)).fetchall()
                latest_scope_rows = conn.execute(
                    "WITH ranked AS ("
                    " SELECT m.ado_project,m.ado_repo,m.scope,m.status,m.id,"
                    "        m.gh_org,m.gh_repo,"
                    " ROW_NUMBER() OVER ("
                    "  PARTITION BY m.ado_project,m.ado_repo,m.scope "
                    "  ORDER BY m.id DESC"
                    " ) rn FROM migrations m"
                    " JOIN repo_risk_scores r"
                    "  ON r.project=m.ado_project AND r.repo_name=m.ado_repo"
                    " WHERE r.assigned_phase=?"
                    ") SELECT ado_project,ado_repo,scope,status,gh_org,gh_repo "
                    "FROM ranked WHERE rn=1",
                    (phase.value,),
                ).fetchall()
                expectation_rows = conn.execute(
                    "SELECT DISTINCT e.ado_project,e.ado_repo,e.scope,e.gh_org,e.gh_repo "
                    "FROM migration_expectations e "
                    "JOIN repo_risk_scores r "
                    " ON r.project=e.ado_project AND r.repo_name=e.ado_repo "
                    "WHERE r.assigned_phase=?",
                    (phase.value,),
                ).fetchall()
                inventory_run_rows = conn.execute(
                    "WITH ranked AS ("
                    " SELECT pir.*,ROW_NUMBER() OVER ("
                    "  PARTITION BY pir.project "
                    "  ORDER BY pir.started_at DESC,pir.run_id DESC"
                    " ) rn FROM pipeline_inventory_runs pir"
                    ") SELECT * FROM ranked WHERE rn=1"
                ).fetchall()

            rows = [
                row for row in migration_rows
                if (str(row["ado_project"]), str(row["ado_repo"])) in repo_keys
            ]

            # Inventory, rather than migration-attempt rows, defines the
            # denominator.  Missing executor output is therefore 0%, not 100%.
            with self.db._conn() as conn:
                inventory_rows = conn.execute(
                    "SELECT pi.project,pi.repo_name,pi.pipeline_id,pi.pipeline_type "
                    "FROM pipeline_inventory pi "
                    "JOIN repo_risk_scores r "
                    " ON r.project=pi.project AND r.repo_name=pi.repo_name "
                    "WHERE r.assigned_phase=?",
                    (phase.value,),
                ).fetchall()
                pipeline_rows = conn.execute(
                    "WITH phase_pipelines AS ("
                    " SELECT pi.project,pi.repo_name,pi.pipeline_id,pi.pipeline_type,"
                    "        r.gh_org,r.gh_repo"
                    " FROM pipeline_inventory pi"
                    " JOIN repo_risk_scores r"
                    "  ON r.project=pi.project AND r.repo_name=pi.repo_name"
                    " WHERE r.assigned_phase=?"
                    "), ranked AS ("
                    " SELECT pm.project,pm.repo_name,pm.pipeline_id,pm.pipeline_type,"
                    "        pm.status,pm.id,"
                    "        pm.gh_org,pm.gh_repo,"
                    "        p.gh_org planned_gh_org,p.gh_repo planned_gh_repo,"
                    " ROW_NUMBER() OVER ("
                    "  PARTITION BY pm.project,pm.pipeline_id,pm.pipeline_type "
                    "  ORDER BY pm.id DESC"
                    " ) AS rn FROM pipeline_migrations pm"
                    " JOIN phase_pipelines p"
                    "  ON p.project=pm.project AND p.repo_name=pm.repo_name"
                    "  AND p.pipeline_id=pm.pipeline_id"
                    "  AND p.pipeline_type=pm.pipeline_type"
                    ") SELECT project,repo_name,pipeline_id,pipeline_type,"
                    " CASE WHEN"
                    "  (planned_gh_org='' OR lower(planned_gh_org)=lower(gh_org))"
                    "  AND (planned_gh_repo='' OR lower(planned_gh_repo)=lower(gh_repo))"
                    " THEN status ELSE 'mapping_mismatch' END status "
                    "FROM ranked WHERE rn=1",
                    (phase.value,),
                ).fetchall()
        except Exception as exc:
            result = PhaseGateResult(
                phase=phase, status=GateStatus.FAIL,
                repo_success_pct=0.0, pipeline_success_pct=0.0,
                repos_completed=0, repos_total=total_repos,
                pipelines_completed=0, pipelines_total=0,
                failures=[
                    "Gate evidence query failed closed: "
                    f"{type(exc).__name__}: {str(exc)[:500]}"
                ],
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
            self.db.upsert_phase_gate(result)
            return result

        expected_by_repo: dict[tuple[str, str], dict[str, tuple[str, str]]] = {}
        for row in expectation_rows:
            key = (str(row["ado_project"]), str(row["ado_repo"]))
            if key in repo_keys:
                expected_by_repo.setdefault(key, {})[str(row["scope"])] = (
                    str(row["gh_org"]), str(row["gh_repo"])
                )
        latest_scope_by_key = {
            (
                str(row["ado_project"]), str(row["ado_repo"]), str(row["scope"])
            ): row
            for row in latest_scope_rows
        }
        missing_expected: list[str] = []
        if expected_by_repo:
            repos_done = 0
            for project, repo_name in sorted(repo_keys):
                expected = expected_by_repo.get((project, repo_name), {})
                if not expected:
                    missing_expected.append(
                        f"{project}/{repo_name}: no persisted scope expectations"
                    )
                    continue
                complete = True
                for scope, (expected_org, expected_repo) in expected.items():
                    observed = latest_scope_by_key.get((project, repo_name, scope))
                    if observed is None:
                        missing_expected.append(f"{project}/{repo_name}:{scope}")
                        complete = False
                        continue
                    if (
                        observed["status"] != "completed"
                        or str(observed["gh_org"]).casefold() != expected_org.casefold()
                        or str(observed["gh_repo"]).casefold() != expected_repo.casefold()
                    ):
                        complete = False
                if complete:
                    repos_done += 1
        else:
            # Legacy databases have no expectation receipts. Preserve their
            # old observed-scope behavior, while all v6 executions register
            # the exact approved scope set before the first write.
            repos_done = sum(
                1 for row in rows
                if row["ok_scopes"] == row["total_scopes"]
                and row["total_scopes"] > 0
            )
        repo_pct = repos_done / total_repos if total_repos else 0.0

        expected_pipelines = {
            (
                str(row["project"]), str(row["repo_name"]),
                int(row["pipeline_id"]), str(row["pipeline_type"]),
            )
            for row in inventory_rows
            if (str(row["project"]), str(row["repo_name"])) in repo_keys
        }
        latest_pipeline_status = {
            (
                str(row["project"]), str(row["repo_name"]),
                int(row["pipeline_id"]), str(row["pipeline_type"]),
            ): str(row["status"])
            for row in pipeline_rows
        }
        total_pipes = len(expected_pipelines)
        pipes_done = sum(
            1 for key in expected_pipelines
            if latest_pipeline_status.get(key) == "completed"
        )
        pipe_pct = pipes_done / total_pipes if total_pipes else 1.0

        if missing_expected:
            failures.append(
                f"Missing expected scope receipts ({len(missing_expected)}): "
                + ", ".join(missing_expected[:10])
            )

        if expected_by_repo:
            latest_inventory_by_project = {
                str(row["project"]): row for row in inventory_run_rows
            }
            pipeline_projects = {
                project for (project, repo_name), scopes in expected_by_repo.items()
                if "pipelines" in scopes
            }
            bad_inventory: list[str] = []
            for project in sorted(pipeline_projects):
                receipt = latest_inventory_by_project.get(project)
                if receipt is None:
                    bad_inventory.append(f"{project}: not scanned")
                elif (
                    receipt["status"] != "completed"
                    or int(receipt["failed_count"]) > 0
                    or int(receipt["unmapped_count"]) > 0
                ):
                    bad_inventory.append(
                        f"{project}: {receipt['status']} "
                        f"(failed={receipt['failed_count']}, "
                        f"unmapped={receipt['unmapped_count']})"
                    )
            if bad_inventory:
                failures.append(
                    f"Incomplete pipeline inventory ({len(bad_inventory)}): "
                    + ", ".join(bad_inventory[:10])
                )

        # gate_min_completed is configured against the phase cap (e.g. PILOT=95),
        # so cap the required count at the actual phase population — otherwise a
        # phase with fewer repos than the cap could never pass.
        min_required = min(cfg.gate_min_completed, total_repos)
        if total_repos > 0 and repos_done < min_required:
            failures.append(f"repos_completed={repos_done} < min={min_required}")
        if repo_pct < cfg.gate_repo_success_pct:
            failures.append(
                f"repo_success={repo_pct:.1%} < threshold={cfg.gate_repo_success_pct:.0%}")
        if total_pipes > 0 and pipe_pct < cfg.gate_pipeline_success_pct:
            failures.append(
                f"pipeline_success={pipe_pct:.1%} < threshold={cfg.gate_pipeline_success_pct:.0%}")

        failed_repos = [
            f"{row['ado_project']}/{row['ado_repo']}" for row in rows
            if row["failed_scopes"]
        ]
        if failed_repos:
            failures.append(f"Failed repos ({len(failed_repos)}): {', '.join(failed_repos[:10])}")

        review_repos = [
            f"{row['ado_project']}/{row['ado_repo']}" for row in rows
            if row["review_scopes"]
        ]
        if review_repos:
            failures.append(
                f"Repos needing review ({len(review_repos)}): "
                f"{', '.join(review_repos[:10])}"
            )

        mapping_repos = [
            f"{row['ado_project']}/{row['ado_repo']}" for row in rows
            if row["mapping_scopes"]
        ]
        if mapping_repos:
            failures.append(
                f"Target mapping mismatches ({len(mapping_repos)}): "
                f"{', '.join(mapping_repos[:10])}"
            )

        review_pipelines = [
            f"{project}/{repo_name}#{pipeline_type}:{pipeline_id}"
            for project, repo_name, pipeline_id, pipeline_type
            in sorted(expected_pipelines)
            if latest_pipeline_status.get(
                (project, repo_name, pipeline_id, pipeline_type)
            )
            == "needs_review"
        ]
        if review_pipelines:
            failures.append(
                f"Pipelines needing review ({len(review_pipelines)}): "
                f"{', '.join(review_pipelines[:10])}"
            )

        mapping_pipelines = [
            f"{project}/{repo_name}#{pipeline_type}:{pipeline_id}"
            for project, repo_name, pipeline_id, pipeline_type
            in sorted(expected_pipelines)
            if latest_pipeline_status.get(
                (project, repo_name, pipeline_id, pipeline_type)
            )
            == "mapping_mismatch"
        ]
        if mapping_pipelines:
            failures.append(
                f"Pipeline target mapping mismatches ({len(mapping_pipelines)}): "
                f"{', '.join(mapping_pipelines[:10])}"
            )

        status = GateStatus.PASS if not failures else GateStatus.FAIL
        result = PhaseGateResult(
            phase=phase, status=status,
            repo_success_pct=repo_pct, pipeline_success_pct=pipe_pct,
            repos_completed=repos_done, repos_total=total_repos,
            pipelines_completed=pipes_done, pipelines_total=total_pipes,
            failures=failures, checked_at=datetime.now(timezone.utc).isoformat(),
        )
        self.db.upsert_phase_gate(result)
        return result

    def override(self, phase: PhaseType, reason: str) -> PhaseGateResult:
        result = self.check(phase)
        result.status = GateStatus.OVERRIDE
        result.override_reason = reason
        self.db.upsert_phase_gate(result)
        log.warning(f"Gate {phase.value} OVERRIDDEN: {reason}")
        return result

    def can_advance(self, phase: PhaseType) -> bool:
        gate = self.db.get_phase_gate(phase)
        return gate is not None and gate["status"] in (
            GateStatus.PASS.value, GateStatus.OVERRIDE.value)

    def check_for_assignment(
        self,
        phase: PhaseType,
        cohort_repo_names: list[str],
    ) -> PhaseGateResult:
        """Evaluate gate metrics scoped to assignment cohort repos (FR-034)."""
        if not cohort_repo_names:
            return PhaseGateResult(
                phase=phase, status=GateStatus.FAIL,
                repo_success_pct=0.0, pipeline_success_pct=0.0,
                repos_completed=0, repos_total=0,
                pipelines_completed=0, pipelines_total=0,
                failures=["Assignment cohort has no repositories"],
            )
        cfg = self.phases[phase]
        failures: list[str] = []
        total_repos = len(cohort_repo_names)

        with self.db._conn() as conn:
            rows = conn.execute(
                "SELECT ado_repo, COUNT(*) total_scopes, "
                "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) ok_scopes "
                "FROM migrations WHERE ado_repo IN ({}) GROUP BY ado_repo".format(
                    ",".join("?" * len(cohort_repo_names))
                ),
                cohort_repo_names,
            ).fetchall()

        repos_done = sum(
            1 for r in rows
            if r["ok_scopes"] == r["total_scopes"] and r["total_scopes"] > 0
        )
        repo_pct = repos_done / total_repos if total_repos else 0.0

        with self.db._conn() as conn:
            pr = conn.execute(
                "SELECT COUNT(*) total, "
                "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) done "
                "FROM pipeline_migrations WHERE repo_name IN ({})".format(
                    ",".join("?" * len(cohort_repo_names))
                ),
                cohort_repo_names,
            ).fetchone()
        total_pipes = pr["total"] if pr else 0
        pipes_done = pr["done"] if pr else 0
        pipe_pct = pipes_done / total_pipes if total_pipes else 1.0

        min_required = min(cfg.gate_min_completed, total_repos)
        if total_repos > 0 and repos_done < min_required:
            failures.append(f"repos_completed={repos_done} < min={min_required}")
        if repo_pct < cfg.gate_repo_success_pct:
            failures.append(
                f"repo_success={repo_pct:.1%} < threshold={cfg.gate_repo_success_pct:.0%}")
        if total_pipes > 0 and pipe_pct < cfg.gate_pipeline_success_pct:
            failures.append(
                f"pipeline_success={pipe_pct:.1%} < threshold={cfg.gate_pipeline_success_pct:.0%}")

        status = GateStatus.PASS if not failures else GateStatus.FAIL
        return PhaseGateResult(
            phase=phase, status=status,
            repo_success_pct=repo_pct, pipeline_success_pct=pipe_pct,
            repos_completed=repos_done, repos_total=total_repos,
            pipelines_completed=pipes_done, pipelines_total=total_pipes,
            failures=failures,
            checked_at=datetime.now(timezone.utc).isoformat(),
        )
