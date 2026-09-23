"""CLI command group and registration."""
from __future__ import annotations

import click

from ado2gh.cli.discover import register as register_discover
from ado2gh.cli.migration import register as register_migration
from ado2gh.cli.misc import register as register_misc
from ado2gh.cli.phase import register as register_phase
from ado2gh.cli.pipelines import register as register_pipelines


@click.group()
@click.version_option("5.1.0")
def cli() -> None:
    """ado2gh v5 — Production-ready ADO to GitHub migration with multi-token,
    risk-based phasing, and post-migration validation."""
    pass


register_discover(cli)
register_migration(cli)
register_pipelines(cli)
register_phase(cli)
register_misc(cli)

__all__ = ["cli"]
