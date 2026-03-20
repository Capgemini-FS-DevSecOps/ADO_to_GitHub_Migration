from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class MigrationStatus(str, Enum):
    PENDING     = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED   = "completed"
    FAILED      = "failed"
    SKIPPED     = "skipped"
    ROLLED_BACK = "rolled_back"


class MigrationScope(str, Enum):
    REPO             = "repo"
    WORK_ITEMS       = "work_items"
    PIPELINES        = "pipelines"
    WIKI             = "wiki"
    SECRETS          = "secrets"
    BRANCH_POLICIES  = "branch_policies"


class PipelineType(str, Enum):
    YAML    = "yaml"
    CLASSIC = "classic"
    RELEASE = "release"


class PipelineComplexity(str, Enum):
    SIMPLE  = "simple"
    MEDIUM  = "medium"
    COMPLEX = "complex"


class PhaseType(str, Enum):
    POC   = "poc"
    PILOT = "pilot"
    WAVE1 = "wave1"
    WAVE2 = "wave2"
    WAVE3 = "wave3"


class GateStatus(str, Enum):
    PASS     = "pass"
    FAIL     = "fail"
    OVERRIDE = "override"


@dataclass
class RepoConfig:
    ado_project:       str
    ado_repo:          str
    gh_org:            str
    gh_repo:           str
    scopes:            list[str]  = field(default_factory=lambda: ["repo"])
    team_mapping:      dict       = field(default_factory=dict)
    skip_lfs:          bool       = False
    archive_source:    bool       = False
    tags:              list[str]  = field(default_factory=list)
    pipeline_parallel: int        = 8
    pipeline_filter:   str        = ""
    risk_score:        float      = 0.0
    phase:             str        = ""


@dataclass
class WaveConfig:
    wave_id:            int
    name:               str
    description:        str
    repos:              list[RepoConfig]
    parallel:           int  = 4
    retry_max:          int  = 3
    timeout_sec:        int  = 1800
    pipeline_parallel:  int  = 8
    phase:              str  = ""


@dataclass
class PhaseConfig:
    phase:                     PhaseType
    repo_cap:                  int
    risk_max:                  float
    batch_size:                int
    repo_parallel:             int
    pipeline_parallel:         int
    gate_repo_success_pct:     float = 0.95
    gate_pipeline_success_pct: float = 0.90
    gate_min_completed:        int   = 1


DEFAULT_PHASES: dict[PhaseType, PhaseConfig] = {
    PhaseType.POC: PhaseConfig(
        phase=PhaseType.POC, repo_cap=10, risk_max=25.0, batch_size=10,
        repo_parallel=2, pipeline_parallel=4,
        gate_repo_success_pct=0.90, gate_pipeline_success_pct=0.80,
        gate_min_completed=9),
    PhaseType.PILOT: PhaseConfig(
        phase=PhaseType.PILOT, repo_cap=100, risk_max=45.0, batch_size=25,
        repo_parallel=4, pipeline_parallel=8,
        gate_repo_success_pct=0.95, gate_pipeline_success_pct=0.90,
        gate_min_completed=95),
    PhaseType.WAVE1: PhaseConfig(
        phase=PhaseType.WAVE1, repo_cap=500, risk_max=65.0, batch_size=50,
        repo_parallel=6, pipeline_parallel=12,
        gate_repo_success_pct=0.97, gate_pipeline_success_pct=0.95,
        gate_min_completed=485),
    PhaseType.WAVE2: PhaseConfig(
        phase=PhaseType.WAVE2, repo_cap=1000, risk_max=80.0, batch_size=100,
        repo_parallel=8, pipeline_parallel=16,
        gate_repo_success_pct=0.98, gate_pipeline_success_pct=0.97,
        gate_min_completed=980),
    PhaseType.WAVE3: PhaseConfig(
        phase=PhaseType.WAVE3, repo_cap=999_999, risk_max=100.0, batch_size=500,
        repo_parallel=8, pipeline_parallel=16,
        gate_repo_success_pct=0.98, gate_pipeline_success_pct=0.97,
        gate_min_completed=1),
}

PHASE_ORDER = [PhaseType.POC, PhaseType.PILOT, PhaseType.WAVE1, PhaseType.WAVE2, PhaseType.WAVE3]


def next_phase(p: PhaseType) -> Optional[PhaseType]:
    idx = PHASE_ORDER.index(p)
    return PHASE_ORDER[idx + 1] if idx + 1 < len(PHASE_ORDER) else None


@dataclass
class PipelineVariable:
    name:        str
    value:       str
    is_secret:   bool = False
    is_settable: bool = True
    group_name:  str  = ""


@dataclass
class PipelineEnvironment:
    name:                str
    id:                  int       = 0
    required_approvers:  list[str] = field(default_factory=list)
    approval_timeout_min: int      = 1440
    pre_deploy_checks:   list[str] = field(default_factory=list)
    post_deploy_checks:  list[str] = field(default_factory=list)


@dataclass
class PipelineStage:
    name:           str
    display_name:   str                            = ""
    depends_on:     list[str]                      = field(default_factory=list)
    condition:      str                            = ""
    environment:    Optional[PipelineEnvironment]   = None
    jobs:           list[dict]                     = field(default_factory=list)
    is_deployment:  bool                           = False
    agent_pool:     str                            = "ubuntu-latest"
    variables:      list[PipelineVariable]         = field(default_factory=list)


@dataclass
class PipelineMetadata:
    """Complete normalized record for one ADO pipeline."""
    pipeline_id:         int
    pipeline_name:       str
    pipeline_type:       PipelineType
    folder:              str              = ""
    project:             str              = ""
    repo_id:             str              = ""
    repo_name:           str              = ""
    repo_type:           str              = "TfsGit"
    repo_branch:         str              = "main"
    yaml_path:           str              = ""
    yaml_content:        str              = ""
    trigger_branches:    list[str]        = field(default_factory=list)
    trigger_pr_branches: list[str]        = field(default_factory=list)
    trigger_schedules:   list[dict]       = field(default_factory=list)
    variables:           list[PipelineVariable]    = field(default_factory=list)
    variable_groups:     list[dict]       = field(default_factory=list)
    stages:              list[PipelineStage]       = field(default_factory=list)
    environments:        list[PipelineEnvironment] = field(default_factory=list)
    service_connections: list[dict]       = field(default_factory=list)
    agent_pools:         list[str]        = field(default_factory=list)
    retention_days:      int              = 30
    last_run_id:         Optional[int]    = None
    last_run_result:     str              = ""
    last_run_date:       str              = ""
    avg_duration_min:    float            = 0.0
    total_runs_30d:      int              = 0
    complexity:          PipelineComplexity = PipelineComplexity.SIMPLE
    migration_notes:     list[str]        = field(default_factory=list)
    unsupported_tasks:   list[str]        = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pipeline_id":         self.pipeline_id,
            "pipeline_name":       self.pipeline_name,
            "pipeline_type":       self.pipeline_type.value,
            "folder":              self.folder,
            "project":             self.project,
            "repo_id":             self.repo_id,
            "repo_name":           self.repo_name,
            "repo_type":           self.repo_type,
            "repo_branch":         self.repo_branch,
            "yaml_path":           self.yaml_path,
            "trigger_branches":    self.trigger_branches,
            "trigger_pr_branches": self.trigger_pr_branches,
            "trigger_schedules":   self.trigger_schedules,
            "variables":           [v.__dict__ for v in self.variables],
            "variable_groups":     self.variable_groups,
            "stages": [
                {
                    "name":          s.name,
                    "display_name":  s.display_name,
                    "depends_on":    s.depends_on,
                    "condition":     s.condition,
                    "is_deployment": s.is_deployment,
                    "agent_pool":    s.agent_pool,
                    "environment":   s.environment.__dict__ if s.environment else None,
                    "jobs":          s.jobs,
                }
                for s in self.stages
            ],
            "environments":       [e.__dict__ for e in self.environments],
            "service_connections": self.service_connections,
            "agent_pools":        self.agent_pools,
            "retention_days":     self.retention_days,
            "last_run_id":        self.last_run_id,
            "last_run_result":    self.last_run_result,
            "last_run_date":      self.last_run_date,
            "avg_duration_min":   self.avg_duration_min,
            "total_runs_30d":     self.total_runs_30d,
            "complexity":         self.complexity.value,
            "migration_notes":    self.migration_notes,
            "unsupported_tasks":  self.unsupported_tasks,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PipelineMetadata":
        m = cls(
            pipeline_id    = d["pipeline_id"],
            pipeline_name  = d["pipeline_name"],
            pipeline_type  = PipelineType(d.get("pipeline_type", "yaml")),
            folder         = d.get("folder", ""),
            project        = d.get("project", ""),
            repo_id        = d.get("repo_id", ""),
            repo_name      = d.get("repo_name", ""),
            repo_type      = d.get("repo_type", "TfsGit"),
            repo_branch    = d.get("repo_branch", "main"),
            yaml_path      = d.get("yaml_path", ""),
            yaml_content   = "",
            trigger_branches     = d.get("trigger_branches", []),
            trigger_pr_branches  = d.get("trigger_pr_branches", []),
            trigger_schedules    = d.get("trigger_schedules", []),
            variable_groups      = d.get("variable_groups", []),
            service_connections  = d.get("service_connections", []),
            agent_pools          = d.get("agent_pools", []),
            retention_days       = d.get("retention_days", 30),
            last_run_id          = d.get("last_run_id"),
            last_run_result      = d.get("last_run_result", ""),
            last_run_date        = d.get("last_run_date", ""),
            avg_duration_min     = d.get("avg_duration_min", 0.0),
            total_runs_30d       = d.get("total_runs_30d", 0),
            complexity           = PipelineComplexity(d.get("complexity", "simple")),
            migration_notes      = d.get("migration_notes", []),
            unsupported_tasks    = d.get("unsupported_tasks", []),
        )
        m.variables = [PipelineVariable(**v) for v in d.get("variables", [])]
        m.stages = [
            PipelineStage(
                name          = s["name"],
                display_name  = s.get("display_name", ""),
                depends_on    = s.get("depends_on", []),
                condition     = s.get("condition", ""),
                is_deployment = s.get("is_deployment", False),
                agent_pool    = s.get("agent_pool", "ubuntu-latest"),
                environment   = PipelineEnvironment(**s["environment"])
                                if s.get("environment") else None,
                jobs          = s.get("jobs", []),
            )
            for s in d.get("stages", [])
        ]
        m.environments = [PipelineEnvironment(**e) for e in d.get("environments", [])]
        return m


@dataclass
class RiskSignal:
    name: str
    raw_value: float
    score: float
    max_points: float
    rationale: str


@dataclass
class RiskScore:
    project: str
    repo_name: str
    total_score: float = 0.0
    signals: list = field(default_factory=list)
    assigned_phase: Optional[PhaseType] = None
    gh_org: str = ""
    gh_repo: str = ""
    size_kb: int = 0
    pipeline_count: int = 0
    branch_count: int = 0
    last_commit_days: int = 0
    complex_pipeline_pct: float = 0.0
    classic_pipeline_pct: float = 0.0
    release_pipeline_pct: float = 0.0
    variable_group_count: int = 0
    service_connection_count: int = 0

    def to_dict(self) -> dict:
        return {
            "project": self.project, "repo_name": self.repo_name,
            "total_score": round(self.total_score, 2),
            "assigned_phase": self.assigned_phase.value if self.assigned_phase else None,
            "gh_org": self.gh_org, "gh_repo": self.gh_repo,
            "size_kb": self.size_kb, "pipeline_count": self.pipeline_count,
            "branch_count": self.branch_count, "last_commit_days": self.last_commit_days,
            "complex_pct": round(self.complex_pipeline_pct, 2),
            "classic_pct": round(self.classic_pipeline_pct, 2),
            "release_pct": round(self.release_pipeline_pct, 2),
            "variable_groups": self.variable_group_count,
            "service_connections": self.service_connection_count,
            "signals": [{"name": s.name, "raw": s.raw_value,
                         "score": round(s.score, 2), "max": s.max_points}
                        for s in self.signals],
        }


@dataclass
class PhaseGateResult:
    phase: PhaseType
    status: GateStatus
    repo_success_pct: float
    pipeline_success_pct: float
    repos_completed: int
    repos_total: int
    pipelines_completed: int
    pipelines_total: int
    failures: list = field(default_factory=list)
    override_reason: str = ""
    checked_at: str = ""


@dataclass
class BatchCheckpoint:
    phase: PhaseType
    batch_num: int
    total_batches: int
    repos_done: int
    repos_total: int
    status: str
    started_at: str
    completed_at: str = ""
