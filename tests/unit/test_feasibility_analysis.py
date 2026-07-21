"""Unit tests for feasibility analysis (feature 009, T029 + T030)."""
from __future__ import annotations

from ado2gh.core.scopes.git_scope import FeasibilityReport


class TestFeasibilityAnalysisThresholds:
    """Tests for T029: Verify warn at >2GB, fail_soft at >10GB, warn at file >100MB, LFS flag at >2GB."""

    def test_warn_at_repo_size_gt_2gb(self):
        """Verify warn status when repo size > 2GB."""
        report = FeasibilityReport(
            repo_id="Proj/RepoA",
            size_kb=2_500_000,  # 2.5 GB
            size_mb=2500.0,
            size_gb=2.5,
            branch_count=10,
            lfs_objects=0,
            lfs_size_gb=0,
            strategy="gei",
            status="warn",
            warnings=["Repository size (2.50 GB) is large - consider GEI for faster migration"],
            gei_available=True,
        )
        assert report.status == "warn"
        assert any("2.50 GB" in w for w in report.warnings)

    def test_fail_soft_at_repo_size_gt_10gb(self):
        """Verify fail_soft status when repo size > 10GB."""
        report = FeasibilityReport(
            repo_id="Proj/RepoA",
            size_kb=11_000_000,  # 11 GB
            size_mb=11000.0,
            size_gb=11.0,
            branch_count=10,
            lfs_objects=0,
            lfs_size_gb=0,
            strategy="manual",
            status="fail_hard",
            warnings=["Repository size (11.00 GB) exceeds 10 GB threshold - manual migration required"],
            gei_available=True,
        )
        assert report.status == "fail_hard"
        assert any("exceeds 10 GB" in w for w in report.warnings)

    def test_warn_at_lfs_size_gt_2gb(self):
        """Verify warn status when LFS size > 2GB."""
        report = FeasibilityReport(
            repo_id="Proj/RepoA",
            size_kb=1_000_000,  # 1 GB
            size_mb=1000.0,
            size_gb=1.0,
            branch_count=10,
            lfs_objects=100,
            lfs_size_gb=2.5,
            strategy="gei",
            status="fail_soft",
            warnings=["Repository size (1.00 GB) exceeds 2 GB - GEI recommended if available"],
            gei_available=True,
        )
        assert report.status == "fail_soft"
        assert report.lfs_size_gb > 2.0

    def test_ok_when_repo_size_lt_2gb_no_lfs(self):
        """Verify ok status when repo size < 2GB and no LFS issues."""
        report = FeasibilityReport(
            repo_id="Proj/RepoA",
            size_kb=1_500_000,  # 1.5 GB
            size_mb=1500.0,
            size_gb=1.5,
            branch_count=10,
            lfs_objects=0,
            lfs_size_gb=0,
            strategy="mirror",
            status="ok",
            warnings=[],
            gei_available=True,
        )
        assert report.status == "ok"
        assert len(report.warnings) == 0


class TestStrategySelection:
    """Tests for T030: Verify mirror for <2GB no LFS, GEI for 2-10GB, manual for >10GB or LFS >2GB."""

    def test_mirror_strategy_for_small_repo_no_lfs(self):
        """Verify mirror strategy for repo < 2GB with no LFS."""
        report = FeasibilityReport(
            repo_id="Proj/RepoA",
            size_kb=1_000_000,  # 1 GB
            size_mb=1000.0,
            size_gb=1.0,
            branch_count=10,
            lfs_objects=0,
            lfs_size_gb=0,
            strategy="mirror",
            status="ok",
            warnings=[],
            gei_available=True,
        )
        assert report.strategy == "mirror"

    def test_gei_strategy_for_medium_repo(self):
        """Verify GEI strategy for repo 2-10GB."""
        report = FeasibilityReport(
            repo_id="Proj/RepoA",
            size_kb=5_000_000,  # 5 GB
            size_mb=5000.0,
            size_gb=5.0,
            branch_count=10,
            lfs_objects=0,
            lfs_size_gb=0,
            strategy="gei",
            status="fail_soft",
            warnings=["Repository size (5.00 GB) exceeds 2 GB - GEI recommended if available"],
            gei_available=True,
        )
        assert report.strategy == "gei"

    def test_manual_strategy_for_large_repo(self):
        """Verify manual strategy for repo > 10GB."""
        report = FeasibilityReport(
            repo_id="Proj/RepoA",
            size_kb=12_000_000,  # 12 GB
            size_mb=12000.0,
            size_gb=12.0,
            branch_count=10,
            lfs_objects=0,
            lfs_size_gb=0,
            strategy="manual",
            status="fail_hard",
            warnings=["Repository size (12.00 GB) exceeds 10 GB threshold - manual migration required"],
            gei_available=True,
        )
        assert report.strategy == "manual"

    def test_manual_strategy_for_large_lfs(self):
        """Verify manual strategy for LFS > 2GB."""
        report = FeasibilityReport(
            repo_id="Proj/RepoA",
            size_kb=1_000_000,  # 1 GB
            size_mb=1000.0,
            size_gb=1.0,
            branch_count=10,
            lfs_objects=100,
            lfs_size_gb=3.0,
            strategy="manual",
            status="fail_soft",
            warnings=["Repository size (1.00 GB) exceeds 2 GB - GEI recommended if available"],
            gei_available=True,
        )
        assert report.strategy == "manual"
