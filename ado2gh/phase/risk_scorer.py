"""9-signal risk scorer for repos (0-100 scale)."""
from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timezone

from ado2gh.models import (
    PipelineComplexity,
    PipelineMetadata,
    PipelineType,
    RiskScore,
    RiskSignal,
)


class RiskScorer:
    """Score a repository's migration risk from nine weighted signals.

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

    def score(
        self,
        rs: RiskScore,
        pipelines: list[PipelineMetadata],
        commits: list[dict],
    ) -> RiskScore:
        """Fill in the signals and total for a repository.

        The caller supplies the repository's identity and raw counts on ``rs``
        (``project``, ``repo_name``, ``gh_org``, ``size_kb``, ``branch_count``,
        ``variable_group_count``, ``service_connection_count``); this method
        derives the pipeline ratios and commit recency and computes the score.

        Args:
            rs: The record to score; updated in place.
            pipelines: The repository's pipelines, used for the count and the
                complex / classic / release ratios.
            commits: Most-recent-first ADO commit dicts; only the first is read,
                for days since the last commit.

        Returns:
            The same ``rs`` with ``pipeline_count``, the ``*_pipeline_pct``
            fields, ``last_commit_days``, ``signals`` and ``total_score`` set.
        """
        rs.pipeline_count = len(pipelines)
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
            self._score_signal("repo_size_kb",           rs.size_kb,                   15.0,
                     lambda v: min(15.0, 15.0 * math.log1p(v) / math.log1p(5_000_000))),
            self._score_signal("pipeline_count",          rs.pipeline_count,            15.0,
                     lambda v: min(15.0, v * 0.3)),
            self._score_signal("complex_pipeline_ratio",  rs.complex_pipeline_pct,      15.0,
                     lambda v: v * 15.0),
            self._score_signal("classic_pipeline_ratio",  rs.classic_pipeline_pct,      10.0,
                     lambda v: v * 10.0),
            self._score_signal("release_pipeline_count",
                     int(rs.release_pipeline_pct * max(1, rs.pipeline_count)), 10.0,
                     lambda v: min(10.0, v * 2.0)),
            self._score_signal("variable_group_count",    rs.variable_group_count,      10.0,
                     lambda v: min(10.0, v * 1.0)),
            self._score_signal("service_connection_count", rs.service_connection_count, 10.0,
                     lambda v: min(10.0, v * 1.25)),
            self._score_signal("days_since_last_commit",  rs.last_commit_days,          10.0,
                     lambda v: 10.0 if v == 0 else (
                         0.0 if v > 730 else max(0.0, 10.0 * (1 - v / 730)))),
            self._score_signal("branch_count",            rs.branch_count,               5.0,
                     lambda v: min(5.0, v * 0.1)),
        ]
        rs.signals = signals
        rs.total_score = min(100.0, sum(s.score for s in signals))
        return rs

    def _score_signal(
        self, name: str, raw: float, max_pts: float, fn: Callable[[float], float],
    ) -> RiskSignal:
        """Apply one signal's scoring function to its raw value.

        Args:
            name: Signal name shown in reports.
            raw: The measured input.
            max_pts: The signal's weight (its maximum points).
            fn: Maps the raw value to points in ``[0, max_pts]``.

        Returns:
            The signal with its points and a ``raw -> points/max`` rationale.
        """
        pts = fn(raw)
        return RiskSignal(name, raw, pts, max_pts, f"{raw} -> {pts:.1f}/{max_pts}")
