"""Centralised output-directory resolution.

All artifact-emitting commands derive their output paths from one base
directory. Resolution order (highest priority first):

    1. The ADO2GH_OUTPUT_DIR environment variable.
    2. The literal default "output".

The wrapper script sets ADO2GH_OUTPUT_DIR per phase (e.g. ``output/poc_run``)
so a single migration cycle's workflows, secrets, validation reports, and
audit logs all live under one folder.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_OUTPUT_BASE = "output"


def output_base() -> Path:
    """Return the base output directory as a Path.

    Read each call so a process that sets the env var mid-run picks it up.
    """
    return Path(os.environ.get("ADO2GH_OUTPUT_DIR") or DEFAULT_OUTPUT_BASE)


def output_str(*parts: str) -> str:
    """Join parts under the base and return a forward-slash str path."""
    p = output_base().joinpath(*parts)
    return str(p)
