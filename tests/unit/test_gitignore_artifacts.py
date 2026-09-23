"""Test that root-level runtime artifacts are gitignored and not tracked by git."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

ARTIFACTS = ["cloud_credentials.json", "llm_models.json", "ui_settings.json"]


def test_artifacts_in_gitignore():
    """All runtime artifacts must be listed in .gitignore."""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for artifact in ARTIFACTS:
        assert artifact in gitignore, f"{artifact} is not in .gitignore"


def test_artifacts_not_tracked_by_git():
    """No runtime artifact should be tracked by git.

    Checks the git index by looking for the filenames in the packed refs or
    the index file. Uses a simple file-existence check on the staged blob
    references rather than spawning a subprocess (which has handle issues on
    Windows under pytest).
    """
    # Read the git index file and check if any artifact filename appears as a
    # tracked path. The git index format has paths embedded as null-terminated
    # strings, so we can search for the raw bytes.
    index_path = REPO_ROOT / ".git" / "index"
    if index_path.exists():
        index_bytes = index_path.read_bytes()
        for artifact in ARTIFACTS:
            artifact_bytes = artifact.encode("utf-8")
            assert artifact_bytes not in index_bytes, (
                f"{artifact} appears in git index — still tracked"
            )
