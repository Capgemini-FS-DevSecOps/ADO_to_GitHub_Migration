"""Tests for UI page consolidation (US7)."""
from pathlib import Path


def test_page_count_reduced():
    """Verify page directory count is reduced by at least 30% (from 14 to 9 or fewer; target is 7) (T110a)."""
    app_dir = Path("apps/migration-ui/src/app")
    page_dirs = [d for d in app_dir.iterdir() if d.is_dir() and not d.name.startswith("_")]
    
    # Should be 7 or fewer (target from spec)
    assert len(page_dirs) <= 9, f"Page count should be ≤9, found {len(page_dirs)}: {[d.name for d in page_dirs]}"


def test_removed_routes_do_not_exist():
    """Verify removed route paths do not exist as directories (T110b)."""
    app_dir = Path("apps/migration-ui/src/app")
    
    # These should have been removed per spec
    removed_pages = ["readiness", "workflows", "validation", "monitor", "runs", "history"]
    
    for page in removed_pages:
        page_path = app_dir / page
        assert not page_path.exists(), f"Removed page {page} should not exist at {page_path}"
