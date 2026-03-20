"""9-signal risk scorer for repos (0-100 scale)."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from ado2gh.models import (
    PipelineComplexity, PipelineType, RiskScore, RiskSignal,
)


class RiskScorer:
    """
    Signal weights (total = 100):
      1. repo_size_kb            -> 0-15   (log scale, 5 GB = max)
      2. pipeline_count          -> 0-15   (>=50 = max)
      3. complex_pipeline_ratio  -> 0-15
      4. classic_pipeline_ratio  -> 0-10
      5. release_pipeline_count  -> 0-10   (>=5 = max)
      6. variable_group_count    -> 0-10   (>=10 = max)
      7. service_connection_count-> 0-10   (>=8 = max)
      8. days_since_last_commit  -> 0-10   (active = high risk, stale = low)
      9. branch_count            -> 0-5    (>=50 = max)
    """

    def score(self, project: str, repo_meta: dict,
              pipelines: list, repo_stats: dict,
              commits: list, var_groups: list, svc_conns: list,
              gh_org: str = "") -> RiskScore:
        rs = RiskScore(
            project=project, repo_name=repo_meta.get("name", ""),
            gh_org=gh_org,
            gh_repo=re.sub(r"[^a-zA-Z0-9\-_.]", "-", repo_meta.get("name", "")),
            size_kb=repo_meta.get("size", 0),
            pipeline_count=len(pipelines),
            branch_count=repo_stats.get("branch_count", 0),
            variable_group_count=len(var_groups),
            service_connection_count=len(svc_conns),
        )
        if pipelines:
            n = len(pipelines)
            rs.complex_pipeline_pct = sum(
                1 for p in pipelines if p.complexity == PipelineComplexity.COMPLEX) / n
            rs.classic_pipeline_pct = sum(
                1 for p in pipelines if p.pipeline_type == PipelineType.CLASSIC) / n
            rs.release_pipeline_pct = sum(
                1 for p in pipelines if p.pipeline_type == PipelineType.RELEASE) / n
        if commits:
            try:
                last_date_str = (commits[0].get("committer", {}).get("date", "")
                                 or commits[0].get("author", {}).get("date", ""))
                if last_date_str:
                    last_dt = datetime.fromisoformat(last_date_str.replace("Z", "+00:00"))
                    rs.last_commit_days = (datetime.now(timezone.utc) - last_dt).days
            except Exception:
                rs.last_commit_days = 0

        signals = [
            self._s("repo_size_kb",           rs.size_kb,                   15.0,
                     lambda v: min(15.0, 15.0 * math.log1p(v) / math.log1p(5_000_000))),
            self._s("pipeline_count",          rs.pipeline_count,            15.0,
                     lambda v: min(15.0, v * 0.3)),
            self._s("complex_pipeline_ratio",  rs.complex_pipeline_pct,      15.0,
                     lambda v: v * 15.0),
            self._s("classic_pipeline_ratio",  rs.classic_pipeline_pct,      10.0,
                     lambda v: v * 10.0),
            self._s("release_pipeline_count",
                     int(rs.release_pipeline_pct * max(1, rs.pipeline_count)), 10.0,
                     lambda v: min(10.0, v * 2.0)),
            self._s("variable_group_count",    rs.variable_group_count,      10.0,
                     lambda v: min(10.0, v * 1.0)),
            self._s("service_connection_count", rs.service_connection_count, 10.0,
                     lambda v: min(10.0, v * 1.25)),
            self._s("days_since_last_commit",  rs.last_commit_days,          10.0,
                     lambda v: 10.0 if v == 0 else (
                         0.0 if v > 730 else max(0.0, 10.0 * (1 - v / 730)))),
            self._s("branch_count",            rs.branch_count,               5.0,
                     lambda v: min(5.0, v * 0.1)),
        ]
        rs.signals = signals
        rs.total_score = min(100.0, sum(s.score for s in signals))
        return rs

    def _s(self, name: str, raw: float, max_pts: float, fn) -> RiskSignal:
        pts = fn(raw)
        return RiskSignal(name, raw, pts, max_pts, f"{raw} -> {pts:.1f}/{max_pts}")
