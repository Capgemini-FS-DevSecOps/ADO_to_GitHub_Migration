"""Regression check for register entry GAP-027 — "Idempotent — skips completed scopes" was not true of any scope.

Reproduction, in plain English:

``ado2gh run`` advertised itself as idempotent. ``MigrationEngine.migrate_repo``
calls ``handler.migrate(...)`` for every requested scope on every invocation and
never reads the ``MigrationStatus.COMPLETED`` row back as a skip condition, so
the claim held for exactly one of the six handlers. Re-running the mirror
strategy force-pushes over GitHub again, and re-running the work-items scope
creates a second issue for every ADO work item. An operator who believed the
docstring would re-run a failed wave and silently duplicate or clobber.

These tests pin the corrected wording to the behaviour each docstring
describes. They are deliberately keyed on what re-running *does* — force push,
duplicate, skip — rather than on prose, so a docstring can be rewritten freely
as long as it keeps telling the operator the truth. If any of these handlers is
ever made genuinely idempotent, the fix is to change the code and then the
expectation here, in that order.
"""
from __future__ import annotations

import pytest

from ado2gh.cli.main import cli
from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.core.scopes.base import ScopeHandler
from ado2gh.core.scopes.git_scope import GitScopeHandler
from ado2gh.core.scopes.pipelines_scope import PipelinesScopeHandler
from ado2gh.core.scopes.work_items_scope import WorkItemsScopeHandler

# The exact claim the register faulted. No docstring below may make it again.
FALSE_CLAIM = "skips completed scopes"


def _run_command_doc() -> str:
    """Return the help text of ``ado2gh run`` — its docstring, per FR-010a."""
    command = cli.commands["run"]
    return command.help or command.__doc__ or ""


CASES = [
    pytest.param(
        _run_command_doc,
        ("re-run", "duplicate"),
        id="cli-run",
    ),
    pytest.param(
        lambda: MigrationEngine.migrate_repo.__doc__ or "",
        ("not skipped",),
        id="migration-engine",
    ),
    pytest.param(
        lambda: GitScopeHandler.migrate.__doc__ or "",
        ("force-push", "gei"),
        id="git-scope",
    ),
    pytest.param(
        lambda: WorkItemsScopeHandler.migrate.__doc__ or "",
        ("duplicate",),
        id="work-items-scope",
    ),
    pytest.param(
        lambda: PipelinesScopeHandler.migrate.__doc__ or "",
        ("skip",),
        id="pipelines-scope",
    ),
    pytest.param(
        lambda: ScopeHandler.migrate.__doc__ or "",
        ("idempoten",),
        id="scope-handler-protocol",
    ),
]


@pytest.mark.parametrize(("get_doc", "required"), CASES)
def test_docstring_tells_the_truth_about_re_running(get_doc, required):
    """Each docstring drops the false claim and names what re-running really does."""
    doc = get_doc().lower()

    assert doc.strip(), "the docstring is missing entirely"
    assert FALSE_CLAIM not in doc, (
        f"the docstring still claims it {FALSE_CLAIM!r}; "
        f"no handler skips a scope the state DB records as completed"
    )
    for phrase in required:
        assert phrase in doc, (
            f"the docstring never mentions {phrase!r}, so an operator cannot "
            f"tell what a second run of this scope does"
        )
