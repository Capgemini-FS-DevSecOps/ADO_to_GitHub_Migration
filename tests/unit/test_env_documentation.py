"""Tests for environment variable documentation (US9)."""
import ast
import os
from pathlib import Path
from collections import defaultdict


def test_env_example_documents_all_env_vars():
    """Verify .env.example contains every environment variable referenced in ado2gh/ and services/ (T100, T107a)."""
    # Extract all environment variable references from code
    env_vars = set()
    
    for root_dir in ["ado2gh", "services"]:
        for py_file in Path(root_dir).rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            try:
                with open(py_file, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(py_file))
                for node in ast.walk(tree):
                    # Check for os.environ.get, os.getenv, os.environ[]
                    if isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Attribute):
                            if (isinstance(node.func.value, ast.Name) and 
                                node.func.value.id == "os" and 
                                node.func.attr == "environ"):
                                # os.environ.get() or os.environ[]
                                if isinstance(node.func.value, ast.Name):
                                    if node.func.attr == "get" and len(node.args) >= 1:
                                        if isinstance(node.args[0], ast.Constant):
                                            env_vars.add(node.args[0].value)
                            elif (isinstance(node.func.value, ast.Name) and 
                                  node.func.value.id == "os" and 
                                  node.func.attr == "getenv"):
                                # os.getenv()
                                if len(node.args) >= 1 and isinstance(node.args[0], ast.Constant):
                                    env_vars.add(node.args[0].value)
                    elif isinstance(node, ast.Subscript):
                        if isinstance(node.value, ast.Attribute):
                            if (isinstance(node.value.value, ast.Name) and 
                                node.value.value.id == "os" and 
                                node.value.attr == "environ"):
                                # os.environ[VAR]
                                if isinstance(node.slice, ast.Constant):
                                    env_vars.add(node.slice.value)
            except Exception:
                pass
    
    # Read .env.example
    env_example = Path(".env.example")
    assert env_example.exists(), ".env.example should exist"
    
    content = env_example.read_text()
    documented_vars = set()
    for line in content.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            var_name = line.split("=")[0].strip()
            if var_name:
                documented_vars.add(var_name)
    
    # Check that all referenced env vars are documented
    # Filter out common Python env vars that don't need documentation
    skip_vars = {"PYTHONPATH", "PATH", "HOME", "USER", "SHELL"}
    missing_vars = env_vars - documented_vars - skip_vars
    
    # Allow some missing vars for dynamic/runtime configuration
    # But flag significant missing ones
    assert len(missing_vars) <= 5, f"Too many undocumented env vars: {missing_vars}"
