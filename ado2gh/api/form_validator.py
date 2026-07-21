"""Pre-migration form validation (feature 008).

Validates submitted pre-migration form data against required fields
and dependency analysis results.
"""
from __future__ import annotations

from typing import Any, Optional

from ado2gh.api.models import PreMigrationForm, FormStatus


class FormValidationError(Exception):
    """Raised when pre-migration form validation fails."""

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class FormValidator:
    """Validates pre-migration form submissions."""

    REQUIRED_FIELDS = {
        "target_github_org": {"type": str, "description": "Target GitHub organization"},
        "team_mapping": {"type": dict, "description": "Team/permission mapping configuration"},
        "pipeline_config": {"type": dict, "description": "Pipeline configuration"},
    }

    OPTIONAL_FIELDS = {
        "repo_description": {"type": str, "description": "Repository description"},
        "topics": {"type": list, "description": "Repository topics"},
        "labels": {"type": list, "description": "Repository labels"},
    }

    def validate(self, form: PreMigrationForm) -> list[str]:
        """Validate a pre-migration form. Returns list of error messages (empty if valid)."""
        errors: list[str] = []

        if not form.target_github_org or not form.target_github_org.strip():
            errors.append("target_github_org is required")

        if not form.team_mapping:
            errors.append("team_mapping is required")
        elif not isinstance(form.team_mapping, dict):
            errors.append("team_mapping must be a JSON object")

        if not form.pipeline_config:
            errors.append("pipeline_config is required")
        elif not isinstance(form.pipeline_config, dict):
            errors.append("pipeline_config must be a JSON object")

        if form.topics and not isinstance(form.topics, list):
            errors.append("topics must be an array of strings")

        if form.labels and not isinstance(form.labels, list):
            errors.append("labels must be an array of strings")

        return errors

    def validate_submission(self, data: dict[str, Any]) -> list[str]:
        """Validate raw submission data before creating a PreMigrationForm."""
        errors: list[str] = []

        for field, spec in self.REQUIRED_FIELDS.items():
            value = data.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append(f"{field} is required")
            elif not isinstance(value, spec["type"]):
                errors.append(f"{field} must be of type {spec['type'].__name__}")

        for field, spec in self.OPTIONAL_FIELDS.items():
            value = data.get(field)
            if value is not None and not isinstance(value, spec["type"]):
                errors.append(f"{field} must be of type {spec['type'].__name__}")

        return errors

    def is_valid(self, form: PreMigrationForm) -> bool:
        """Return True if the form passes validation."""
        return len(self.validate(form)) == 0
