"""Guards for the 013 function-inventory generator (T014, FR-001a/002a/003b/004/013).

The generator lives outside the package tree (``specs/013-clean-code-arch-remediation/
scripts/function_inventory.py``) because it is a feature artefact, not shipped code, so
it is loaded here with ``importlib.util.spec_from_file_location``.

Every case runs against a throwaway fixture package in ``tmp_path`` with ``--no-ruff
--no-vulture``: the behaviours under test (granularity, ``param_count``, keyword-only
booleans, determinism, judgment decisions, the protected list, module proposals,
exclusions) all come from the script's own ``ast`` pass, so the external linters would
only add runtime.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "013-clean-code-arch-remediation"
    / "scripts"
    / "function_inventory.py"
)


def _load_script():
    """Import the generator by path; skip nothing — a missing file is a failure."""
    assert SCRIPT.exists(), f"generator not written yet: {SCRIPT}"
    spec = importlib.util.spec_from_file_location("function_inventory_t014", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CORE_PY = '''\
"""Fixture module."""


def get_value(a, b):
    """Return a value.

    Args:
        a: first
        b: second
    """

    def nested_helper(x):
        return x

    doubler = lambda y: y * 2
    return nested_helper(a) + doubler(b)


def toggle(*, verbose: bool = False):
    """Do a thing.

    Args:
        verbose: chatty
    """
    return verbose


class Widget:
    """A widget."""

    def render(self, a, b, c, d, e, f):
        """Render it.

        Args:
            a: one
            zz: not a parameter
        """
        return (a, b, c, d, e, f)

    @classmethod
    def build(cls, a, b):
        """Build one."""
        return cls()

    @staticmethod
    def helper(a, b):
        """Help."""
        return a + b
'''

CLI_PY = '''\
"""Fixture CLI module."""

import click


@click.command()
def frobnicate():
    """Frobnicate."""
    return 1


def entrypoint():
    """Run standalone."""
    return 2


if __name__ == "__main__":
    entrypoint()
'''

GENERATED_PY = '''\
"""Fixture generated module."""


def alpha():
    """Alpha."""
    return 1


def beta():
    """Beta."""
    return 2
'''

EXCLUDED_TXT = """\
# fixture exclusions
*_generated.py
"""


@pytest.fixture()
def fixture_tree(tmp_path, monkeypatch):
    """Build a tiny package + artefact dir and chdir into it (the script uses CWD as repo root)."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('"""Fixture package."""\n', encoding="utf-8")
    (pkg / "core.py").write_text(CORE_PY, encoding="utf-8")
    (pkg / "cli_mod.py").write_text(CLI_PY, encoding="utf-8")
    (pkg / "thing_generated.py").write_text(GENERATED_PY, encoding="utf-8")

    out = tmp_path / "artefacts"
    out.mkdir()
    (out / "excluded-paths.txt").write_text(EXCLUDED_TXT, encoding="utf-8")
    (out / "protected-entry-points.manual.txt").write_text("# none\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    return tmp_path, out


@pytest.fixture()
def run(fixture_tree):
    """Return ``(rc_callable, out_dir)`` for invoking the generator on the fixture tree."""
    _tmp_path, out = fixture_tree
    module = _load_script()

    def _run(*extra):
        argv = [
            "--roots",
            "pkg",
            "--out",
            str(out),
            "--excluded",
            str(out / "excluded-paths.txt"),
            "--no-ruff",
            "--no-vulture",
            *extra,
        ]
        return module.main(argv)

    return _run


def _rows(out):
    return json.loads((out / "inventory.json").read_text(encoding="utf-8"))


def _by_id(out):
    return {row["id"]: row for row in _rows(out)}


def test_nested_defs_and_lambdas_get_no_row(run, fixture_tree):
    """Only module-level and class-level named defs are rows (FR-001a)."""
    _tmp, out = fixture_tree
    assert run() == 0
    ids = set(_by_id(out))
    assert "pkg/core.py::get_value" in ids
    assert not [i for i in ids if "nested_helper" in i or "doubler" in i]
    assert "pkg/core.py::Widget.render" in ids


def test_param_count_excludes_receiver(run, fixture_tree):
    """data-model: receiver excluded; variadic and keyword-only count one each."""
    _tmp, out = fixture_tree
    assert run() == 0
    rows = _by_id(out)
    assert rows["pkg/core.py::Widget.render"]["param_count"] == 6
    assert rows["pkg/core.py::Widget.build"]["param_count"] == 2
    assert rows["pkg/core.py::Widget.helper"]["param_count"] == 2
    assert rows["pkg/core.py::get_value"]["param_count"] == 2
    assert rows["pkg/core.py::Widget.render"]["tags"].count("gt5_params") == 1


def test_keyword_only_bool_is_a_bool_flag_proposal(run, fixture_tree):
    """R1 item 3: FBT sees neither keyword-only nor unannotated booleans."""
    _tmp, out = fixture_tree
    assert run() == 0
    row = _by_id(out)["pkg/core.py::toggle"]
    assert "bool_flag" in row["proposed_tags"]
    assert "bool_flag" not in row["tags"]


def test_stale_args_section_is_a_proposal(run, fixture_tree):
    """Judgment tags never land in ``tags`` unconfirmed (FR-002a)."""
    _tmp, out = fixture_tree
    assert run() == 0
    row = _by_id(out)["pkg/core.py::Widget.render"]
    assert "stale_docstring" in row["proposed_tags"]
    assert "stale_docstring" not in row["tags"]


def test_two_runs_are_byte_identical(run, fixture_tree):
    """Two runs of the generator produce byte-identical output (FR-004)."""
    _tmp, out = fixture_tree
    assert run() == 0
    first = (out / "inventory.json").read_bytes()
    assert run() == 0
    assert (out / "inventory.json").read_bytes() == first


def test_matching_decision_is_applied_and_stale_one_is_dropped(run, fixture_tree):
    """A decision keyed by a live ``state_hash`` is reused; a stale one re-proposes."""
    _tmp, out = fixture_tree
    assert run() == 0
    target = _by_id(out)["pkg/core.py::toggle"]

    decisions = out / "tag-decisions.json"
    decisions.write_text(
        json.dumps(
            [
                {
                    "id": target["id"],
                    "tag": "bool_flag",
                    "decision": "confirm",
                    "rationale": "switches behaviour",
                    "state_hash": target["state_hash"],
                    "decided_by": "review-agent",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    assert run() == 0
    row = _by_id(out)["pkg/core.py::toggle"]
    assert "bool_flag" in row["tags"]
    assert row["proposed_tags"] == []

    stale = json.loads(decisions.read_text(encoding="utf-8"))
    stale[0]["state_hash"] = "0" * 40
    decisions.write_text(json.dumps(stale, indent=2), encoding="utf-8")
    assert run() == 0
    row = _by_id(out)["pkg/core.py::toggle"]
    assert "bool_flag" in row["proposed_tags"]
    assert "bool_flag" not in row["tags"]
    assert json.loads(decisions.read_text(encoding="utf-8")) == [], "stale decision must be deleted"


def test_rejected_bool_flag_records_bool_data(run, fixture_tree):
    """data-model § JudgmentTagDecision: rejecting ``bool_flag`` records ``bool_data``."""
    _tmp, out = fixture_tree
    assert run() == 0
    target = _by_id(out)["pkg/core.py::toggle"]
    assert run("--reject", f"{target['id']}:bool_flag", "--rationale", "data", "--decided-by", "review-agent") == 0
    row = _by_id(out)["pkg/core.py::toggle"]
    assert "bool_data" in row["tags"]
    assert row["proposed_tags"] == []


def test_click_and_main_guard_functions_are_protected(run, fixture_tree):
    """R5 protected entry points: ``cli_command`` and ``main_guard``."""
    _tmp, out = fixture_tree
    assert run() == 0
    protected = json.loads((out / "protected-entry-points.json").read_text(encoding="utf-8"))
    by_id = {}
    for entry in protected:
        by_id.setdefault(entry["id"], set()).add(entry["reason"])
    assert "cli_command" in by_id.get("pkg/cli_mod.py::frobnicate", set())
    assert "main_guard" in by_id.get("pkg/cli_mod.py::entrypoint", set())

    rows = _by_id(out)
    assert rows["pkg/cli_mod.py::frobnicate"]["protected"] is True
    assert rows["pkg/cli_mod.py::entrypoint"]["protected"] is True
    assert rows["pkg/core.py::get_value"]["protected"] is False


def test_one_module_name_review_proposal_per_module_and_decisions_are_reused(run, fixture_tree, capsys):
    """One proposal per module; a recorded decision removes it from ``--pending`` (FR-013)."""
    _tmp, out = fixture_tree
    assert run() == 0
    assert run("--pending") == 1
    pending = capsys.readouterr().out
    module_lines = sorted(
        line.split(":")[0] for line in pending.splitlines() if line.endswith(":module_name_review")
    )
    assert module_lines == ["pkg/", "pkg/cli_mod.py", "pkg/core.py"], pending

    assert run("--confirm", "pkg/core.py:module_name_review", "--rationale", "vague", "--decided-by", "review-agent") == 0
    decisions = json.loads((out / "tag-decisions.json").read_text(encoding="utf-8"))
    assert [(d["id"], d["tag"], d["decision"]) for d in decisions] == [
        ("pkg/core.py", "module_name_review", "confirm")
    ]

    assert run("--pending") == 1
    pending = capsys.readouterr().out
    assert "pkg/core.py:module_name_review" not in pending
    assert "pkg/cli_mod.py:module_name_review" in pending


def test_excluded_file_yields_no_rows_but_is_counted(run, fixture_tree):
    """Exclusions never shrink the denominator silently (FR-003b)."""
    _tmp, out = fixture_tree
    assert run() == 0
    assert not [i for i in _by_id(out) if "thing_generated" in i]

    history = [json.loads(line) for line in (out / "inventory-history.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert history[-1]["excluded"] == {"*_generated.py": 2}

    summary = (out / "inventory-summary.md").read_text(encoding="utf-8")
    assert "*_generated.py" in summary
    assert "Excluded" in summary


def test_pending_exits_1_while_proposals_exist(run, fixture_tree, capsys):
    """``--pending`` is the pending-work exit-status gate (SC-001)."""
    _tmp, out = fixture_tree
    assert run() == 0
    assert run("--pending") == 1
    assert "pkg/core.py::toggle:bool_flag" in capsys.readouterr().out


SUB_PY = '''\
"""Fixture sub-package module."""


def gamma(*, loud: bool = False):
    """Gamma."""
    return loud
'''


def test_package_filtered_run_keeps_decisions_it_did_not_walk(run, fixture_tree):
    """A ``--package`` run only prunes decisions belonging to the packages it walked.

    The generator used to rebuild the whole decision file from the Python rows of the
    current run, so a run scoped to one package deleted every decision outside it —
    including the TypeScript ones written by ``function_inventory_ts.mjs``. Only a
    decision whose own package was walked and whose target is gone or has moved on may
    be dropped.
    """
    tmp_path, out = fixture_tree
    sub = tmp_path / "pkg" / "sub"
    sub.mkdir()
    (sub / "other.py").write_text(SUB_PY, encoding="utf-8")
    assert run() == 0

    live = _by_id(out)["pkg/core.py::toggle"]
    assert _by_id(out)["pkg/sub/other.py::gamma"]["package"] == "pkg/sub"

    def decision(target_id, state_hash):
        return {
            "id": target_id,
            "tag": "bool_flag",
            "decision": "confirm",
            "rationale": "fixture",
            "state_hash": state_hash,
            "decided_by": "review-agent",
        }

    decisions = out / "tag-decisions.json"
    decisions.write_text(
        json.dumps(
            [
                decision("pkg/core.py::toggle", live["state_hash"]),
                decision("pkg/core.py::stale_gone", "0" * 40),
                decision("pkg/sub/other.py::gamma", "1" * 40),
                decision("apps/migration-ui/src/lib/thing.ts::helper", "2" * 40),
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    assert run("--package", "pkg") == 0
    kept = {d["id"] for d in json.loads(decisions.read_text(encoding="utf-8"))}
    assert "pkg/core.py::toggle" in kept, "a live decision in the walked package survives"
    assert "pkg/sub/other.py::gamma" in kept, "the filter left this package unwalked"
    assert "apps/migration-ui/src/lib/thing.ts::helper" in kept, "TypeScript decisions survive"
    assert "pkg/core.py::stale_gone" not in kept, "stale in a walked package is still pruned"

    assert "bool_flag" in _by_id(out)["pkg/core.py::toggle"]["tags"]
