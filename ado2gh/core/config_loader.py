"""Configuration loader — settings YAML, text repo lists, and CSV repo lists.

The config file (migration.yaml) contains ONLY connection settings and tuning.
Repo lists come from separate input files (text or CSV).
"""
from __future__ import annotations

import csv
from pathlib import Path

import yaml

from ado2gh.logging_config import log
from ado2gh.models import RepoConfig, WaveConfig


class ConfigLoader:
    """Load migration settings and repo lists from YAML, text, and CSV sources."""

    @staticmethod
    def load(path: str) -> tuple[dict, list[WaveConfig]]:
        """Load a YAML config file.

        Args:
            path: Path to the migration settings YAML file.

        Returns:
            A tuple of the global settings mapping and the parsed waves. The
            wave list may be empty when the config only carries connection
            settings, which is the intended flow — repos come from input files,
            not from the config.

        Raises:
            FileNotFoundError: If no file exists at the given path.
        """
        cfg_path = Path(path)
        if not cfg_path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(cfg_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)

        return ConfigLoader.parse_config(raw, source_name=cfg_path.name)

    @staticmethod
    def parse_config(raw: object, *, source_name: str = "config") -> tuple[dict, list[WaveConfig]]:
        """Parse a loaded YAML mapping into global settings and waves.

        Args:
            raw: The parsed YAML document. Must be a mapping at the root.
            source_name: Label for the source, used in log messages only.

        Returns:
            A tuple of the global settings mapping and the parsed waves.

        Raises:
            ValueError: If the YAML root is not a mapping.
        """
        if not isinstance(raw, dict):
            raise ValueError(f"Config root must be a mapping, got {type(raw).__name__}")

        global_cfg = raw.get("global", {})
        waves_raw = raw.get("waves", [])

        waves: list[WaveConfig] = []
        for w in waves_raw:
            repos = _parse_repos(w.get("repos", []), global_cfg)
            waves.append(WaveConfig(
                wave_id=w["wave_id"],
                name=w.get("name", f"wave-{w['wave_id']}"),
                description=w.get("description", ""),
                repos=repos,
                parallel=w.get("parallel", global_cfg.get("parallel", 4)),
                retry_max=w.get("retry_max", 3),
                timeout_sec=w.get("timeout_sec", 1800),
                pipeline_parallel=w.get("pipeline_parallel",
                                        global_cfg.get("pipeline_parallel", 8)),
                phase=w.get("phase", ""),
            ))

        total_repos = sum(len(w.repos) for w in waves)
        if waves:
            log.info("Loaded config: %d wave(s), %d repos from %s",
                     len(waves), total_repos, source_name)
        else:
            log.info("Loaded settings from %s (no waves — use --input for repo list)",
                     source_name)

        return global_cfg, waves

    @staticmethod
    def load_yaml_text(text: str, *, source_name: str = "upload") -> tuple[dict, list[WaveConfig]]:
        """Parse YAML config text that was pasted or uploaded rather than read from disk.

        Args:
            text: The raw YAML document text.
            source_name: Label for the source, used in log messages only.

        Returns:
            A tuple of the global settings mapping and the parsed waves.
        """
        raw = yaml.safe_load(text)
        return ConfigLoader.parse_config(raw, source_name=source_name)

    # ── Text input (project/repo per line) ──────────────────────────────────

    @staticmethod
    def load_text_input(path: str, gh_org: str,
                        scopes: list[str] = None) -> list[RepoConfig]:
        """Parse a simple text file into RepoConfig list.

        Format (one per line):
            project/repo
            project/repo::gh_org/gh_repo

        Args:
            path: Path to the text file holding the repo list.
            gh_org: GitHub organisation used for lines that name no target.
            scopes: Migration scopes applied to every repo. Defaults to
                ``["repo"]`` when omitted.

        Returns:
            One repo configuration per line that parsed cleanly; blank lines,
            comments, and malformed lines are skipped.

        Raises:
            FileNotFoundError: If no file exists at the given path.
        """
        scopes = scopes or ["repo"]
        txt_path = Path(path)
        if not txt_path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")
        content = txt_path.read_text(encoding="utf-8")
        return ConfigLoader.load_text_content(content, gh_org, scopes, source_name=txt_path.name)

    @staticmethod
    def load_text_content(
        content: str,
        gh_org: str,
        scopes: list[str] | None = None,
        *,
        source_name: str = "upload",
    ) -> list[RepoConfig]:
        """Parse repo list text (file upload or paste).

        Args:
            content: Raw text of the repo list, one entry per line.
            gh_org: GitHub organisation used for lines that name no target.
            scopes: Migration scopes applied to every repo. Defaults to
                ``["repo"]`` when omitted.
            source_name: Label for the source, used in log messages only.

        Returns:
            One repo configuration per line that parsed cleanly; blank lines,
            comments, and malformed lines are skipped and logged.
        """
        scopes = scopes or ["repo"]
        repos: list[RepoConfig] = []
        for lineno, raw_line in enumerate(content.splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                repo = _parse_text_line(line, gh_org, scopes)
                repos.append(repo)
            except ValueError as exc:
                log.warning("line %d skipped: %s", lineno, exc)

        log.info("Loaded %d repo(s) from %s", len(repos), source_name)
        return repos

    # ── CSV input (with per-repo scopes) ────────────────────────────────────

    @staticmethod
    def load_csv_input(path: str, gh_org: str,
                       default_scopes: list[str] = None) -> list[RepoConfig]:
        """Parse a CSV file with columns: ado_project,ado_repo,gh_org,gh_repo,scopes

        The scopes column uses pipe-separated values: repo|pipelines|work_items

        Args:
            path: Path to the CSV input file.
            gh_org: GitHub organisation used for rows that leave gh_org blank.
            default_scopes: Scopes applied to rows that leave the scopes column
                blank. Defaults to ``["repo"]`` when omitted.

        Returns:
            One repo configuration per row that names both a project and a
            repo. Rows missing either are skipped, and the list is empty when
            the file holds nothing but comments.

        Raises:
            FileNotFoundError: If no file exists at the given path.
        """
        default_scopes = default_scopes or ["repo"]
        csv_path = Path(path)
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV input file not found: {path}")

        repos: list[RepoConfig] = []
        with open(csv_path, "r", encoding="utf-8") as fh:
            # Skip comment lines at the top
            lines = [line for line in fh if not line.strip().startswith("#")]

        if not lines:
            return repos

        # Re-parse with csv.DictReader
        import io
        reader = csv.DictReader(io.StringIO("".join(lines)))
        for row in reader:
            ado_project = row.get("ado_project", "").strip()
            ado_repo = row.get("ado_repo", "").strip()
            if not ado_project or not ado_repo:
                continue

            row_gh_org = row.get("gh_org", "").strip() or gh_org
            row_gh_repo = row.get("gh_repo", "").strip() or ado_repo
            scopes_str = row.get("scopes", "").strip()
            scopes = scopes_str.split("|") if scopes_str else default_scopes

            repos.append(RepoConfig(
                ado_project=ado_project,
                ado_repo=ado_repo,
                gh_org=row_gh_org,
                gh_repo=row_gh_repo,
                scopes=scopes,
                pipeline_filter=row.get("pipeline_filter", "").strip(),
            ))

        log.info("Loaded %d repo(s) from CSV %s", len(repos), csv_path.name)
        return repos

    # ── Auto-detect input format ────────────────────────────────────────────

    @staticmethod
    def load_input(path: str, gh_org: str,
                   default_scopes: list[str] = None) -> list[RepoConfig]:
        """Auto-detect input format (text or CSV) and load repos.

        Args:
            path: Path to the input file. A ``.csv`` suffix selects the CSV
                parser; anything else is read as a plain text repo list.
            gh_org: GitHub organisation used for entries that name no target.
            default_scopes: Scopes applied to entries that specify none.

        Returns:
            The repo configurations described by the file.
        """
        if path.endswith(".csv"):
            return ConfigLoader.load_csv_input(path, gh_org, default_scopes)
        else:
            return ConfigLoader.load_text_input(path, gh_org, default_scopes)


def _parse_repos(repos_raw: list[dict], global_cfg: dict) -> list[RepoConfig]:
    """Build repo configurations from the raw repo entries of one wave.

    Args:
        repos_raw: Raw repo mappings taken from a wave in the YAML config.
        global_cfg: Global settings block, consulted for the GitHub org,
            scope, and pipeline parallelism defaults.

    Returns:
        One repo configuration per raw entry, with any field the entry omits
        filled in from the global defaults.
    """
    repos: list[RepoConfig] = []
    default_gh_org = global_cfg.get("gh_org", "")
    default_scopes = global_cfg.get("default_scopes", ["repo"])

    for r in repos_raw:
        repos.append(RepoConfig(
            ado_project=r["ado_project"],
            ado_repo=r["ado_repo"],
            gh_org=r.get("gh_org", default_gh_org),
            gh_repo=r.get("gh_repo", r["ado_repo"]),
            scopes=r.get("scopes", default_scopes),
            team_mapping=r.get("team_mapping", {}),
            skip_lfs=r.get("skip_lfs", False),
            archive_source=r.get("archive_source", False),
            tags=r.get("tags", []),
            pipeline_parallel=r.get("pipeline_parallel",
                                    global_cfg.get("pipeline_parallel", 8)),
            pipeline_filter=r.get("pipeline_filter", ""),
            risk_score=r.get("risk_score", 0.0),
            phase=r.get("phase", ""),
        ))
    return repos


def _parse_text_line(line: str, default_gh_org: str, scopes: list[str]) -> RepoConfig:
    """Parse one ``project/repo[::gh_org/gh_repo]`` line into a repo config.

    Args:
        line: A single stripped, non-comment line from a repo list file.
        default_gh_org: GitHub organisation used when the line omits the
            ``::gh_org/gh_repo`` target part.
        scopes: Migration scopes to copy onto the resulting configuration.

    Returns:
        The repo configuration the line describes. The GitHub repo name falls
        back to the ADO repo name when no target is given.

    Raises:
        ValueError: If the line is malformed, or if it names no GitHub
            organisation and no default was provided.
    """
    if "::" in line:
        source_part, target_part = line.split("::", 1)
    else:
        source_part = line
        target_part = ""

    if "/" not in source_part:
        raise ValueError(f"expected 'project/repo', got '{source_part}'")

    ado_project, ado_repo = source_part.split("/", 1)
    ado_project = ado_project.strip()
    ado_repo = ado_repo.strip()

    if not ado_project or not ado_repo:
        raise ValueError(f"empty project or repo in '{source_part}'")

    if target_part:
        if "/" not in target_part:
            raise ValueError(f"expected 'gh_org/gh_repo' after '::', got '{target_part}'")
        gh_org, gh_repo = target_part.split("/", 1)
        gh_org = gh_org.strip()
        gh_repo = gh_repo.strip()
    else:
        gh_org = default_gh_org
        gh_repo = ado_repo

    if not gh_org:
        raise ValueError("no gh_org specified and no default provided")

    return RepoConfig(
        ado_project=ado_project,
        ado_repo=ado_repo,
        gh_org=gh_org,
        gh_repo=gh_repo,
        scopes=list(scopes),
    )
