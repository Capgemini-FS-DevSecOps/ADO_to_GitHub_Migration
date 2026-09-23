"""Resolve the repository list a run acts on, from an input file or the configuration waves.

Lives in ``ado2gh/api/`` rather than ``ado2gh/cli/`` because the pipeline and
validation steps need it as much as the CLI commands do, and the API layer must
not import the CLI layer (GAP-021).
"""
from __future__ import annotations

from ado2gh.logging_config import console
from ado2gh.models import RepoConfig, WaveConfig


def load_repos(
    input_path: str, global_cfg: dict, waves: list[WaveConfig] | None = None,
) -> list[RepoConfig]:
    """Load repositories from --input file, or fall back to waves in configuration.

    Args:
        input_path: Path of the ``--input`` file naming the repositories to act on. An
            empty value means no file was given and the waves are used instead.
        global_cfg: The ``global`` block of the migration configuration, which supplies
            the target GitHub organisation and the default scope list.
        waves: The waves parsed from the configuration, used only when no input file
            was given.

    Returns:
        The repositories to act on. An empty list when neither source yielded
        any, in which case a message has already been printed to the console.
    """
    from ado2gh.core.config_loader import ConfigLoader

    if input_path:
        gh_org = global_cfg.get("gh_org", "")
        default_scopes = global_cfg.get("default_scopes", ["repo"])
        repos = ConfigLoader.load_input(input_path, gh_org, default_scopes)
        if not repos:
            console.print(f"[red]No repos found in {input_path}[/red]")
        return repos

    if waves:
        repos = [r for w in waves for r in w.repos]
        if repos:
            return repos

    console.print(
        "[yellow]No repos specified. Use --input <file> or add waves to config.[/yellow]"
    )
    return []
