"""Stable CLI package entry point.

The UI feature branch introduced this package layout.  The v6 PEV command
surface remains the authoritative implementation and is re-exported here so
``python -m ado2gh``, the console script, and existing package imports all use
the same command tree.
"""

from ado2gh.cli.pev import cli

__all__ = ["cli"]
