"""Tests for scripts cleanup (US8)."""
from pathlib import Path


def test_only_scripts_dev_remains():
    """Verify only scripts/dev/ directory remains in scripts/ (no top-level script files) and contains at most 3 files (T120a)."""
    scripts_dir = Path("scripts")
    
    # Check no top-level script files (only dev/ directory should exist)
    top_level_items = [item for item in scripts_dir.iterdir() if item.is_file()]
    assert len(top_level_items) == 0, f"scripts/ should have no top-level files, found: {[i.name for i in top_level_items]}"
    
    # Check scripts/dev/ exists
    dev_dir = scripts_dir / "dev"
    assert dev_dir.exists(), "scripts/dev/ should exist"
    
    # Check it has at most 3 files
    dev_files = [item for item in dev_dir.iterdir() if item.is_file()]
    assert len(dev_files) <= 3, f"scripts/dev/ should have at most 3 files, found {len(dev_files)}: {[i.name for i in dev_files]}"


def test_no_stale_script_references_in_docs():
    """Verify no stale file paths or script names referenced in CLAUDE.md, README.md, or docs/*.md reference deleted scripts (T120b)."""
    docs_to_check = [
        Path("CLAUDE.md"),
        Path("README.md"),
    ] + list(Path("docs").glob("*.md"))
    
    # Scripts that should not be referenced (deleted or moved)
    # Note: COMMAND_REFERENCE.md has a "Removed Scripts" section documenting migration, which is allowed
    stale_scripts = [
        "discover.sh",
        "migrate.sh",
        "scripts/deploy/",
    ]
    
    for doc_path in docs_to_check:
        if doc_path.exists():
            content = doc_path.read_text(encoding='utf-8')
            for stale in stale_scripts:
                # Allow references in documentation that documents the migration
                if doc_path.name in ["COMMAND_REFERENCE.md", "STRUCTURAL_CHANGELOG.md"]:
                    continue
                assert stale not in content, f"{doc_path} should not reference deleted script {stale}"
