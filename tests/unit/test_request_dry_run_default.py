"""`RunWaveRequest`/`PhaseRunRequest.dry_run` default to true, so an omitted field previews.

Both bodies previously defaulted `dry_run` to `False`. Every shipped internal
caller (the command-line interface, the console's step executor) always
states the field explicitly, so it keeps its own behaviour either way. Three
routes accept a body a caller could send with the field left out — a direct
`POST /v1/migrate` or `POST /v1/phase/run`, for example from a script built
from the OpenAPI schema or a hand-written `curl` body, and a `POST /v1/jobs`
body naming the migrate-repo job type, whose payload dictionary is forwarded
into `RunWaveRequest` unchanged by the background worker
(`ado2gh/core/orchestration/worker.py`) — nothing between that HTTP body and
the request object states `dry_run` on the caller's behalf. Every one of
those three previously ran for real on an omitted field and now previews.
Flipping the default to `True` matches the standing safeguard that a preview
run is the default and a real run needs an explicit opt-in, which holds at
every other execution-mode boundary in this codebase (register cross-reference:
CA-001).

`tests/contract/test_public_surface_snapshot.py` freezes CLI commands, HTTP
routes, environment variables and DB tables — never a Pydantic request field's
default — so this change needs no snapshot update.
"""
from __future__ import annotations

from ado2gh.api.contracts import PhaseRunRequest, RunWaveRequest

REQUESTS = [
    (RunWaveRequest, {"config_path": "migration.yaml"}),
    (PhaseRunRequest, {"config_path": "migration.yaml", "phase": "poc"}),
]


def test_omitting_dry_run_previews() -> None:
    for model, required in REQUESTS:
        assert model(**required).dry_run is True


def test_the_field_default_is_true() -> None:
    for model, _ in REQUESTS:
        assert model.model_fields["dry_run"].default is True


def test_dry_run_false_still_opts_into_live() -> None:
    for model, required in REQUESTS:
        assert model(**required, dry_run=False).dry_run is False
