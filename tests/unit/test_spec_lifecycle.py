"""Tests for spec consolidation and lifecycle management (US5)."""
from pathlib import Path


def test_archive_directory_contains_archived_specs():
    """Verify specs/archive/002-login-bootstrap/ and specs/archive/005-profile-onboarding/ exist (T090a)."""
    archive_dir = Path("specs/archive")
    assert archive_dir.exists(), "specs/archive/ directory should exist"
    
    spec_002 = archive_dir / "002-login-bootstrap"
    spec_005 = archive_dir / "005-profile-onboarding"
    
    assert spec_002.exists(), f"{spec_002} should exist"
    assert spec_005.exists(), f"{spec_005} should exist"
    
    # Verify they contain spec files
    assert (spec_002 / "spec.md").exists() or (spec_002 / "plan.md").exists(), f"{spec_002} should contain spec files"
    assert (spec_005 / "spec.md").exists() or (spec_005 / "plan.md").exists(), f"{spec_005} should contain spec files"


def test_specs_readme_exists_and_lists_all_specs():
    """Verify specs/README.md exists and lists all specs with status (T090b)."""
    readme = Path("specs/README.md")
    assert readme.exists(), "specs/README.md should exist"
    
    content = readme.read_text()
    
    # Verify it mentions key specs
    assert "001" in content, "README should mention spec 001"
    assert "002" in content, "README should mention spec 002"
    assert "005" in content, "README should mention spec 005"
    assert "010" in content, "README should mention spec 010"
    
    # Verify it has status information
    assert "active" in content.lower(), "README should mention active status"
    assert "archived" in content.lower(), "README should mention archived status"
