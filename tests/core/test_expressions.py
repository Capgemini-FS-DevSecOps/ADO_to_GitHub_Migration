"""Tests for ADO pipeline expression rewriting into GitHub Actions syntax."""
from __future__ import annotations

from ado2gh.pipelines.transform.expressions import (
    map_condition,
    rewrite_expression_string,
    rewrite_expressions_inplace,
)

# ── map_condition ────────────────────────────────────────────────────────────


def test_map_condition_empty_or_none_returns_empty_string():
    assert map_condition("") == ""
    assert map_condition(None) == ""


def test_map_condition_translates_status_functions():
    assert map_condition("succeeded()") == "success()"
    assert map_condition("failed()") == "failure()"
    assert map_condition("always()") == "always()"
    assert map_condition("canceled()") == "cancelled()"


def test_map_condition_translates_variables_reference():
    assert map_condition("variables['My.Var']") == "env.My.Var"
    assert map_condition('variables["My.Var"]') == "env.My.Var"
    assert map_condition("variables.Foo") == "env.Foo"


def test_map_condition_translates_boolean_operators():
    assert map_condition("eq(1, 2)") == "== (1, 2)"
    assert map_condition("ne(1, 2)") == "!= (1, 2)"
    assert map_condition("and(1, 2)") == "&& (1, 2)"
    assert map_condition("or(1, 2)") == "|| (1, 2)"
    assert map_condition("not(1)") == "! (1)"


def test_map_condition_translates_build_variables_and_strips_whitespace():
    assert map_condition("Build.SourceBranch") == "github.ref"
    assert map_condition("  succeeded()  ") == "success()"


# ── rewrite_expression_string ───────────────────────────────────────────────


def test_rewrite_expression_string_passes_through_non_strings_and_no_dollar():
    assert rewrite_expression_string(123, {"FOO"}) == 123
    assert rewrite_expression_string("no dollar here", {"FOO"}) == "no dollar here"


def test_rewrite_expression_string_rewrites_ado_parameter_syntax():
    result = rewrite_expression_string("${{ parameters.myParam }}", set())
    assert result == "${{ inputs.myParam }}"


def test_rewrite_expression_string_rewrites_known_macro_and_leaves_unknown():
    result = rewrite_expression_string("$(FOO) and $(BAR)", {"FOO"})
    assert result == "${{ env.FOO }} and $(BAR)"


def test_rewrite_expression_string_skips_macro_substitution_without_env_keys():
    assert rewrite_expression_string("$(FOO)", set()) == "$(FOO)"


# ── rewrite_expressions_inplace ─────────────────────────────────────────────


def test_rewrite_expressions_inplace_recurses_through_dicts_and_lists():
    obj = {"a": "$(FOO)", "b": ["$(FOO)", 5, {"c": "$(FOO)"}], "d": 9}
    rewrite_expressions_inplace(obj, {"FOO"})
    assert obj == {
        "a": "${{ env.FOO }}",
        "b": ["${{ env.FOO }}", 5, {"c": "${{ env.FOO }}"}],
        "d": 9,
    }
