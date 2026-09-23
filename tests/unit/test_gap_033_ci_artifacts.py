"""Regression check for register entries GAP-033 and GAP-034 — deployment and CI artefacts that ship an unguarded
write path or a guessable default credential.

Both gaps are artefact-shaped, so this module asserts the *shape* of the YAML
that ships, loaded with ``yaml.safe_load`` rather than matched as text:

* **GAP-033** — ``.github/workflows/migrate-repo.yml`` runs on a cron twice a
  month and every run of its job pushes to the destination organisation. With
  no ``environment:`` key on the job there is no GitHub Environment, and so no
  place to hang a required-reviewer protection rule: the unattended run writes
  to the target org with nobody interposed. The test asserts every job in the
  workflow names an environment, and that the unattended trigger it guards is
  still there.
* **GAP-034** — ``docker-compose.prod.yml``, the file named as the production
  topology, started Postgres with the password ``ado2gh``, published 5432 to
  the host, embedded that same password in three ``ADO2GH_DATABASE_URL``
  values, and substituted a ``change-me-in-production`` placeholder for an
  unset secret instead of refusing to start. The tests assert the required
  form ``${VAR:?...}`` for the credential, no host-published database port, no
  literal password in the connection URLs, and no ``${VAR:-default}``
  placeholder standing in for any secret.

The last test is a standing shape guard rather than a reproduction: no compose
service passes a secret through ``build.args`` today, and none should start —
build args land in image history and in ``docker history`` output, so a secret
belongs in the runtime ``environment`` block instead.

Every credential-shaped literal below is an obvious fake.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "migrate-repo.yml"
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
DEV_COMPOSE = REPO_ROOT / "docker-compose.yml"

SECRET_NAME = re.compile(r"password|secret|token|api_?key|_pat\b|^pat$", re.IGNORECASE)
REQUIRED_FORM = re.compile(r"\$\{[A-Z0-9_]+:\?")
DEFAULTED_FORM = re.compile(r"\$\{([A-Z0-9_]+):-([^}]*)\}")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _services(path: Path) -> dict:
    return _load(path).get("services") or {}


@pytest.fixture(scope="module")
def migrate_workflow() -> dict:
    return _load(MIGRATE_WORKFLOW)


def test_every_migration_job_runs_under_a_protected_environment(migrate_workflow):
    """A job that pushes to another org must name a GitHub Environment."""
    jobs = migrate_workflow["jobs"]
    assert jobs, "workflow declares no jobs"
    missing = [name for name, job in jobs.items() if not job.get("environment")]
    assert not missing, (
        f"jobs {missing} push to the destination repository with no environment: key, "
        "so no required-reviewer rule can gate them"
    )


def test_migration_workflow_still_carries_the_unattended_trigger(migrate_workflow):
    """The gate above is only meaningful while the cron path exists."""
    # ``on`` is parsed by YAML 1.1 as the boolean True.
    triggers = migrate_workflow.get("on", migrate_workflow.get(True))
    assert "schedule" in triggers, "expected the scheduled trigger GAP-033 gates"


def test_prod_postgres_password_has_no_shipped_default():
    env = _services(PROD_COMPOSE)["postgres"]["environment"]
    password = str(env["POSTGRES_PASSWORD"])
    assert REQUIRED_FORM.search(password), (
        f"POSTGRES_PASSWORD is {password!r}; expected the required form "
        "${POSTGRES_PASSWORD:?...} so an unset value refuses to start"
    )


def test_prod_compose_does_not_publish_the_database_port():
    postgres = _services(PROD_COMPOSE)["postgres"]
    assert not postgres.get("ports"), (
        "docker-compose.prod.yml publishes the database to the host network; "
        "the other services reach it over the compose network"
    )


def test_prod_database_urls_carry_no_literal_password():
    for name, service in _services(PROD_COMPOSE).items():
        url = (service.get("environment") or {}).get("ADO2GH_DATABASE_URL")
        if url is None:
            continue
        credentials = str(url).split("//", 1)[1].split("@", 1)[0]
        assert "${" in credentials, (
            f"service {name} embeds a literal password in ADO2GH_DATABASE_URL"
        )


@pytest.mark.parametrize("path", [PROD_COMPOSE, DEV_COMPOSE], ids=lambda p: p.name)
def test_no_compose_secret_falls_back_to_a_placeholder(path: Path):
    """``${SECRET:-anything}`` silently substitutes; secrets must fail closed."""
    offenders = []
    for name, service in _services(path).items():
        for key, value in (service.get("environment") or {}).items():
            if not SECRET_NAME.search(str(key)):
                continue
            match = DEFAULTED_FORM.search(str(value))
            if match and match.group(2):
                offenders.append(f"{name}.{key} -> {match.group(2)!r}")
    assert not offenders, f"secret-shaped variables with a shipped default: {offenders}"


@pytest.mark.parametrize("path", [PROD_COMPOSE, DEV_COMPOSE], ids=lambda p: p.name)
def test_no_secret_is_passed_as_a_docker_build_arg(path: Path):
    """Build args are baked into image history; secrets belong in ``environment``."""
    offenders = []
    for name, service in _services(path).items():
        build = service.get("build")
        args = build.get("args") if isinstance(build, dict) else None
        for key, value in (args or {}).items():
            if SECRET_NAME.search(str(key)) or SECRET_NAME.search(str(value)):
                offenders.append(f"{name}.build.args.{key}")
    assert not offenders, f"secret-shaped build args: {offenders}"
