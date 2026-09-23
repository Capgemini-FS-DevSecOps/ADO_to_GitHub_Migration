"""Contract for ``ExecutionMode``, the replacement for internal ``dry_run`` booleans.

The boundary rule (``contracts/public-contract-freeze.md``): the CLI flag, the HTTP
field and the persisted column all keep the name ``dry_run`` and the boolean shape;
``from_dry_run`` is the single conversion point into the enum.
"""
from ado2gh.models import ExecutionMode


def test_dry_run_value_is_the_persisted_string():
    assert ExecutionMode.DRY_RUN.value == "dry_run"


def test_live_value_is_the_persisted_string():
    assert ExecutionMode.LIVE.value == "live"


def test_lookup_by_value_returns_the_member():
    assert ExecutionMode("live") is ExecutionMode.LIVE
    assert ExecutionMode("dry_run") is ExecutionMode.DRY_RUN


def test_from_dry_run_maps_true_to_dry_run():
    assert ExecutionMode.from_dry_run(dry_run=True) is ExecutionMode.DRY_RUN


def test_from_dry_run_maps_false_to_live():
    assert ExecutionMode.from_dry_run(dry_run=False) is ExecutionMode.LIVE


def test_member_compares_equal_to_its_string_value():
    """A ``str`` mixin keeps existing string comparisons and JSON writes working."""
    assert ExecutionMode.DRY_RUN == "dry_run"
    assert isinstance(ExecutionMode.LIVE, str)
