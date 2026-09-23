"""Freeze the externally observable surface (spec 013 FR-006, contract
``specs/013-clean-code-arch-remediation/contracts/public-contract-freeze.md``).

Four surfaces are enumerated from the live code at test time and compared to
``public_surface_snapshot.json``:

* ``cli_commands`` — the Click tree under ``ado2gh.cli.main:cli``: every command
  path plus one line per parameter carrying its flags, type, default and
  required/flag/multiple properties.
* ``http_routes`` — ``app`` of ``services.accelerator_api.main`` and
  ``services.agent.main``, one ``<app> <METHOD> <path>`` line per method.
* ``env_vars`` — environment variable names read by ``ado2gh/``, ``services/``
  and ``apps/migration-ui/src``.
* ``db_tables`` — table names in ``CREATE TABLE`` statements across ``ado2gh/``
  and ``services/`` (the state backends *and* the agent session store).

Regenerating is deliberately awkward: ``UPDATE_SURFACE_SNAPSHOT=1`` is the only
path that writes the file, and per the contract the rewrite is only legitimate
in a commit that references an approved ``GAP-NNN`` with ``contract_change:
true``, is listed in ``plan.md`` § Approved contract changes, and carries a
migration note.

    UPDATE_SURFACE_SNAPSHOT=1 python -m pytest tests/contract/test_public_surface_snapshot.py

Not frozen here (see the contract): agent ``StructuredTool`` names/schemas, the
importable Python API, and payload/response *shapes* — the latter are covered by
the other ``tests/contract/`` modules.

``cli_commands`` never renders Click's own help text; it records option names,
flags, types and defaults picked off the ``click.Parameter`` object directly, so
a Click upgrade that only changes wording cannot fail this test. One value still
moved across a Click patch release: whether a bare boolean flag's default
(``--version``, which takes no explicit ``default=``) is eagerly stamped as
``False`` at decoration time or left as Click's internal ``UNSET`` sentinel
until first use. Both mean the same thing on the command line — the flag is off
unless passed — so ``_default_repr`` normalizes an unresolved sentinel on a flag
to ``False`` rather than freezing which Click patch release happened to resolve
it eagerly.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = Path(__file__).with_name("public_surface_snapshot.json")
UPDATE_ENV_VAR = "UPDATE_SURFACE_SNAPSHOT"

# Minimum plausible size per surface. A collector that silently degrades (an app
# that fails to import, a glob that matches nothing) must fail here rather than
# quietly shrink the snapshot and disarm the guard for every later increment.
MINIMUM_ENTRIES = {
    "cli_commands": 20,
    "db_tables": 10,
    "env_vars": 20,
    "http_routes": 40,
}


# --------------------------------------------------------------------------- #
# CLI tree
# --------------------------------------------------------------------------- #


def _default_repr(default: object, *, is_bool_flag: bool = False) -> str:
    """Stable text for a Click default.

    Click 8.3 uses an ``UNSET`` sentinel whose ``repr`` is stable, but several
    options default to a lambda whose ``repr`` embeds a memory address. Both are
    collapsed to fixed markers so two runs agree byte for byte.

    A plain boolean flag with no explicit ``default=`` keyword resolves to
    ``False`` when omitted on the command line — that is true in every Click
    version. Which Click release actually stamps ``False`` onto the option
    object at decoration time versus leaving the ``UNSET`` sentinel in place
    has moved between patch releases (see the module docstring). Recording the
    sentinel verbatim would freeze that internal timing instead of the
    observable default, so a flag's unresolved sentinel is normalized to
    ``False`` here; every other option keeps recording ``<unset>`` verbatim
    since a non-flag option without a default has no such fallback.
    """
    if default is None:
        return "None"
    if callable(default):
        return "<callable>"
    if type(default).__name__ == "Sentinel":  # click.core.UNSET
        return "False" if is_bool_flag else "<unset>"
    if isinstance(default, (str, bool, int, float)):
        return repr(default)
    if isinstance(default, (list, tuple)):
        return repr(tuple(default))
    return f"<{type(default).__name__}>"


def collect_cli_commands() -> list[str]:
    import click

    from ado2gh.cli.main import cli

    lines: list[str] = []

    def walk(command: click.Command, path: str) -> None:
        kind = "group" if isinstance(command, click.Group) else "command"
        lines.append(f"{path} :: {kind}")
        for param in command.params:
            opts = "/".join(sorted(param.opts) + sorted(param.secondary_opts))
            is_flag = bool(getattr(param, "is_flag", False))
            is_bool_flag = bool(getattr(param, "is_bool_flag", False))
            lines.append(
                f"{path} :: param {param.name} :: opts={opts} "
                f":: type={param.type.name} "
                f":: default={_default_repr(param.default, is_bool_flag=is_bool_flag)} "
                f":: required={bool(param.required)} "
                f":: is_flag={is_flag} "
                f":: multiple={bool(getattr(param, 'multiple', False))}"
            )
        if isinstance(command, click.Group):
            for name, sub in command.commands.items():
                walk(sub, f"{path} {name}")

    walk(cli, "ado2gh")
    return lines


# --------------------------------------------------------------------------- #
# HTTP routes
# --------------------------------------------------------------------------- #


def _expand_routes(routes: object, out: list) -> None:
    """Flatten a Starlette/FastAPI route list into concrete routes.

    FastAPI >= 0.116 defers ``include_router`` into lazy ``_IncludedRouter``
    placeholders that expose ``effective_candidates()``; mounts expose
    ``routes``. Both are duck-typed rather than imported so a FastAPI upgrade
    that renames the private class does not silently collect nothing.
    """
    for route in routes:  # type: ignore[union-attr]
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):
            _expand_routes(candidates(), out)
            continue
        nested = getattr(route, "routes", None)
        if nested:
            _expand_routes(nested, out)
            continue
        out.append(route)


def collect_http_routes() -> list[str]:
    from services.accelerator_api.main import app as accelerator_app
    from services.agent.main import app as agent_app

    lines: list[str] = []
    for name, app in (("accelerator", accelerator_app), ("agent", agent_app)):
        concrete: list = []
        _expand_routes(app.routes, concrete)
        for route in concrete:
            path = getattr(route, "path", None)
            if path is None:
                continue
            methods = getattr(route, "methods", None)
            for method in sorted(methods) if methods else ["WEBSOCKET"]:
                lines.append(f"{name} {method} {path}")
    return lines


# --------------------------------------------------------------------------- #
# Environment variables
# --------------------------------------------------------------------------- #

_PY_ENV_RE = re.compile(
    r"""(?:os\.)?(?:environ|getenv)\s*(?:\.\s*(?:get|setdefault|pop)\s*\(|\[|\()\s*"""
    r"""["']([A-Z][A-Z0-9_]*)["']"""
)
_TS_ENV_RE = re.compile(
    r"""process\.env(?:\.([A-Z][A-Z0-9_]*)|\s*\[\s*["']([A-Z][A-Z0-9_]*)["'])"""
)

_ENV_SOURCES: tuple[tuple[str, tuple[str, ...], re.Pattern[str]], ...] = (
    ("ado2gh", ("*.py",), _PY_ENV_RE),
    ("services", ("*.py",), _PY_ENV_RE),
    ("apps/migration-ui/src", ("*.ts", "*.tsx", "*.js", "*.jsx"), _TS_ENV_RE),
)

_SKIP_DIRS = {"__pycache__", "node_modules", ".next", "dist", "build"}


def _source_files(directory: Path, patterns: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        for path in directory.rglob(pattern):
            if _SKIP_DIRS.isdisjoint(path.parts):
                found.append(path)
    return sorted(found)


def collect_env_vars() -> list[str]:
    names: set[str] = set()
    for relative, patterns, regex in _ENV_SOURCES:
        for path in _source_files(REPO_ROOT / relative, patterns):
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in regex.finditer(text):
                names.add(next(group for group in match.groups() if group))
    return sorted(names)


# --------------------------------------------------------------------------- #
# Persisted tables
# --------------------------------------------------------------------------- #

_TABLE_RE = re.compile(
    r"""CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["'`\[]?([A-Za-z_][A-Za-z0-9_]*)""",
    re.IGNORECASE,
)


def collect_db_tables() -> list[str]:
    """Table names only — not the file they live in.

    The contract freezes the persisted schema, not module layout; this feature
    explicitly moves modules (FR-013), and recording paths would make the
    snapshot fail on a move that changes no schema.
    """
    names: set[str] = set()
    for relative in ("ado2gh", "services"):
        for path in _source_files(REPO_ROOT / relative, ("*.py",)):
            text = path.read_text(encoding="utf-8", errors="replace")
            names.update(match.group(1) for match in _TABLE_RE.finditer(text))
    return sorted(names)


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #

COLLECTORS = {
    "cli_commands": collect_cli_commands,
    "db_tables": collect_db_tables,
    "env_vars": collect_env_vars,
    "http_routes": collect_http_routes,
}


def build_surface() -> dict[str, list[str]]:
    """Collect every frozen surface, sorted and de-duplicated."""
    surface: dict[str, list[str]] = {}
    for name, collector in sorted(COLLECTORS.items()):
        entries = sorted(set(collector()))
        floor = MINIMUM_ENTRIES[name]
        assert len(entries) >= floor, (
            f"surface {name!r} collected only {len(entries)} entries "
            f"(expected at least {floor}). The collector is broken — do not "
            f"regenerate the snapshot from this run."
        )
        surface[name] = entries
    return surface


def render_surface(surface: dict[str, list[str]]) -> str:
    """Canonical text for a surface: sorted keys, sorted de-duplicated values."""
    canonical = {key: sorted(set(values)) for key, values in surface.items()}
    return json.dumps(canonical, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_public_surface_matches_snapshot() -> None:
    surface = build_surface()
    rendered = render_surface(surface)

    if os.environ.get(UPDATE_ENV_VAR) == "1":
        SNAPSHOT_PATH.write_text(rendered, encoding="utf-8", newline="\n")
        pytest.fail(
            f"{UPDATE_ENV_VAR}=1: rewrote {SNAPSHOT_PATH.name}. Review the diff, "
            f"confirm it is an approved contract change (GAP-NNN with "
            f"contract_change: true, listed in plan.md § Approved contract "
            f"changes, with a migration note), then re-run without "
            f"{UPDATE_ENV_VAR}."
        )

    assert SNAPSHOT_PATH.is_file(), (
        f"{SNAPSHOT_PATH} is missing. It is a committed contract artefact; "
        f"restore it from git rather than regenerating."
    )
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert sorted(expected) == sorted(surface), (
        "snapshot surface keys changed: "
        f"expected {sorted(expected)}, collected {sorted(surface)}"
    )

    diff = "\n".join(
        difflib.unified_diff(
            render_surface(expected).splitlines(),
            rendered.splitlines(),
            fromfile="public_surface_snapshot.json (committed)",
            tofile="collected from the working tree",
            lineterm="",
        )
    )
    if diff:
        changed = {
            key: {
                "added": sorted(set(surface[key]) - set(expected[key])),
                "removed": sorted(set(expected[key]) - set(surface[key])),
            }
            for key in sorted(surface)
            if set(surface[key]) != set(expected[key])
        }
        pytest.fail(
            "The public surface changed (spec 013 FR-006 freezes it).\n"
            f"Surfaces affected: {json.dumps(changed, indent=2)}\n\n"
            f"{diff}\n\n"
            f"If this is an approved contract change, regenerate with "
            f"{UPDATE_ENV_VAR}=1 and cite the GAP-NNN in the same commit."
        )


def test_surface_snapshot_is_deterministic() -> None:
    """Two collections in one process must be byte-identical."""
    assert render_surface(build_surface()) == render_surface(build_surface())


def test_snapshot_file_is_canonical() -> None:
    """The committed file must be exactly what ``render_surface`` produces.

    Guards against a hand-edited snapshot: a reordered, re-indented or
    duplicate-carrying file would still compare equal as a set, so the diff test
    above would pass while the artefact stopped being reproducible.
    """
    raw = SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert raw == render_surface(json.loads(raw)), (
        f"{SNAPSHOT_PATH.name} is not in canonical form (sorted keys, sorted and "
        f"de-duplicated values, 2-space indent, trailing newline). Regenerate it "
        f"with {UPDATE_ENV_VAR}=1 instead of editing it by hand."
    )
