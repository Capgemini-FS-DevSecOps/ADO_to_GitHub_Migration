"""Tests for module structure flattening (US6)."""
import os
from pathlib import Path


def test_tools_directory_removed():
    """Verify ado2gh/tools/ directory does not exist (T071)."""
    tools_dir = Path("ado2gh/tools")
    assert not tools_dir.exists(), f"ado2gh/tools/ should not exist but found at {tools_dir}"


def test_infra_directory_removed():
    """Verify ado2gh/infra/ directory does not exist and core files are in place (T072)."""
    infra_dir = Path("ado2gh/infra")
    assert not infra_dir.exists(), f"ado2gh/infra/ should not exist but found at {infra_dir}"
    
    # Verify files moved to core/
    concurrency = Path("ado2gh/core/concurrency.py")
    sessions = Path("ado2gh/core/sessions.py")
    assert concurrency.exists(), f"ado2gh/core/concurrency.py should exist"
    assert sessions.exists(), f"ado2gh/core/sessions.py should exist"


def test_llm_subpackage_exists():
    """Verify ado2gh/api/llm/ subpackage exists with __init__.py (T073)."""
    llm_dir = Path("ado2gh/api/llm")
    assert llm_dir.exists(), f"ado2gh/api/llm/ should exist"
    assert (llm_dir / "__init__.py").exists(), f"ado2gh/api/llm/__init__.py should exist"


def test_credentials_subpackage_exists():
    """Verify ado2gh/api/credentials/ subpackage exists with __init__.py (T073)."""
    creds_dir = Path("ado2gh/api/credentials")
    assert creds_dir.exists(), f"ado2gh/api/credentials/ should exist"
    assert (creds_dir / "__init__.py").exists(), f"ado2gh/api/credentials/__init__.py should exist"
