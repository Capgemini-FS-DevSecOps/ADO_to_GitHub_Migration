"""Validate generated GitHub Actions workflow YAML.

Prefers ``actionlint`` (the de-facto standard linter) when it is available on
``PATH``. When it is not installed, falls back to a YAML parse plus a set of
structural checks (jobs/steps/runs-on/secrets references). The chosen mode is
reported so callers can surface reduced-validation warnings to operators.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Validation status values (mirror data-model.md ConversionResult.validation_status).
VALID = "valid"
INVALID = "invalid"
WARN = "warn"

# Validation modes.
MODE_ACTIONLINT = "actionlint"
MODE_YAML_FALLBACK = "yaml_fallback"

_SECRET_REF = re.compile(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}")


@dataclass
class ValidationResult:
    """Outcome of validating a single workflow file."""

    validation_status: str
    validation_errors: list[str] = field(default_factory=list)
    validation_mode: str = MODE_YAML_FALLBACK
    secret_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_status": self.validation_status,
            "validation_errors": list(self.validation_errors),
            "validation_mode": self.validation_mode,
            "secret_refs": list(self.secret_refs),
        }


class WorkflowValidator:
    """Validate workflow YAML via actionlint or a structural YAML fallback."""

    def __init__(self, actionlint_path: str | None = None) -> None:
        # Resolve once; ``None`` means actionlint is unavailable -> fallback.
        self._actionlint = actionlint_path or shutil.which("actionlint")

    @property
    def has_actionlint(self) -> bool:
        return bool(self._actionlint)

    @property
    def validation_mode(self) -> str:
        """Mode used for ``validate()`` — actionlint when installed, else YAML fallback."""
        return MODE_ACTIONLINT if self.has_actionlint else MODE_YAML_FALLBACK

    def validate(self, file_path: str | Path) -> ValidationResult:
        """Validate the workflow file at ``file_path``."""
        path = Path(file_path)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            return ValidationResult(
                validation_status=INVALID,
                validation_errors=[f"cannot read {path}: {exc}"],
                validation_mode=MODE_YAML_FALLBACK,
            )
        return self.validate_content(content, file_path=str(path))

    def validate_content(self, content: str, file_path: str = "workflow.yml") -> ValidationResult:
        """Validate raw workflow ``content`` (file_path used for actionlint)."""
        secret_refs = sorted(set(_SECRET_REF.findall(content)))
        if self._actionlint:
            result = self._validate_actionlint(content, file_path)
            result.secret_refs = secret_refs
            return result
        result = self._validate_yaml_fallback(content)
        result.secret_refs = secret_refs
        return result

    def _validate_actionlint(self, content: str, file_path: str) -> ValidationResult:
        try:
            proc = subprocess.run(
                [self._actionlint, "-format", "{{json .}}", "-"],
                input=content,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # actionlint failed to execute — degrade to fallback validation.
            result = self._validate_yaml_fallback(content)
            result.validation_errors.insert(0, f"actionlint unavailable: {exc}")
            return result

        if proc.returncode == 0 and not (proc.stdout or "").strip():
            return ValidationResult(validation_status=VALID, validation_mode=MODE_ACTIONLINT)

        errors = self._parse_actionlint_output(proc.stdout, proc.stderr)
        status = INVALID if errors else VALID
        return ValidationResult(
            validation_status=status,
            validation_errors=errors,
            validation_mode=MODE_ACTIONLINT,
        )

    @staticmethod
    def _parse_actionlint_output(stdout: str, stderr: str) -> list[str]:
        stdout = (stdout or "").strip()
        errors: list[str] = []
        if stdout:
            try:
                parsed = json.loads(stdout)
                for item in parsed:
                    msg = item.get("message", "")
                    line = item.get("line")
                    col = item.get("column")
                    loc = f"{line}:{col}: " if line else ""
                    errors.append(f"{loc}{msg}".strip())
            except (json.JSONDecodeError, AttributeError, TypeError):
                errors.append(stdout)
        elif stderr and stderr.strip():
            errors.append(stderr.strip())
        return errors

    def _validate_yaml_fallback(self, content: str) -> ValidationResult:
        errors: list[str] = []
        try:
            doc = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            return ValidationResult(
                validation_status=INVALID,
                validation_errors=[f"YAML parse error: {exc}"],
                validation_mode=MODE_YAML_FALLBACK,
            )

        if not isinstance(doc, dict):
            return ValidationResult(
                validation_status=INVALID,
                validation_errors=["workflow root must be a mapping"],
                validation_mode=MODE_YAML_FALLBACK,
            )

        # ``on`` is parsed by PyYAML as boolean True (YAML 1.1) — accept either.
        if "on" not in doc and True not in doc:
            errors.append("missing 'on' trigger definition")

        jobs = doc.get("jobs")
        if not isinstance(jobs, dict) or not jobs:
            errors.append("missing or empty 'jobs' mapping")
        else:
            for job_name, job in jobs.items():
                if not isinstance(job, dict):
                    errors.append(f"job '{job_name}' must be a mapping")
                    continue
                if "uses" in job:
                    # Reusable workflow call — runs-on/steps not required.
                    continue
                if "runs-on" not in job:
                    errors.append(f"job '{job_name}' missing 'runs-on'")
                steps = job.get("steps")
                if not isinstance(steps, list) or not steps:
                    errors.append(f"job '{job_name}' missing or empty 'steps'")

        status = INVALID if errors else VALID
        return ValidationResult(
            validation_status=status,
            validation_errors=errors,
            validation_mode=MODE_YAML_FALLBACK,
        )
