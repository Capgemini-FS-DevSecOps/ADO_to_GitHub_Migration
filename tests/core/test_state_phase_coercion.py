"""State DB backends accept phase as enum or string."""
from ado2gh.models import PhaseType
from ado2gh.state.postgres_db import _phase_value as postgres_phase_value


def test_postgres_phase_value_accepts_string_or_enum():
    assert postgres_phase_value("poc") == "poc"
    assert postgres_phase_value(PhaseType.POC) == "poc"
