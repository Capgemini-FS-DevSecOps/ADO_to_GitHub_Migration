"""Tests for Docker Compose file consolidation (US4)."""
from pathlib import Path


def test_only_two_compose_files_exist():
    """Verify only docker-compose.yml and docker-compose.prod.yml exist (T083)."""
    root = Path(".")
    compose_files = list(root.glob("docker-compose*.yml"))
    
    expected_files = {"docker-compose.yml", "docker-compose.prod.yml"}
    actual_files = {f.name for f in compose_files}
    
    assert actual_files == expected_files, f"Expected {expected_files}, found {actual_files}"
    
    # Verify lightweight and serverless files do not exist
    assert not (root / "docker-compose.lightweight.yml").exists()
    assert not (root / "docker-compose.serverless.yml").exists()
