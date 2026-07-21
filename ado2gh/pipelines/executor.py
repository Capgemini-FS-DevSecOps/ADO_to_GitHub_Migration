"""Executor for an already approved pipeline conversion plan."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Optional

from ado2gh.models import PipelineMetadata
from ado2gh.pipelines.approvals import ManualApprovalRecord
from ado2gh.pipelines.llm import redact_text_secrets
from ado2gh.pipelines.pev_types import ConversionPlan, LLMResolution


class PipelineConversionExecutor:
    """Apply checked resolutions and invoke the deterministic rule engine.

    The input metadata is deep-copied so concurrent conversions can safely
    share one ``PipelineTransformer`` instance.
    """

    def __init__(self, transformer: Any) -> None:
        self.transformer = transformer

    def execute(
        self,
        meta: PipelineMetadata,
        plan: ConversionPlan,
        resolutions: Mapping[str, LLMResolution],
        output_dir: Path,
        *,
        manual_approvals: Optional[Mapping[str, ManualApprovalRecord]] = None,
    ) -> dict[str, Any]:
        working = copy.deepcopy(meta)
        redacted_inline_credentials = self._sanitize_inline_credentials(working)
        applied = 0
        approved_mappings_applied = 0
        manual_approvals = manual_approvals or {}
        overlap = set(manual_approvals).intersection(resolutions)
        if overlap:
            raise ValueError(
                "An ambiguity cannot have both an approved mapping and an LLM proposal"
            )
        if resolutions:
            raise PermissionError(
                "LLM executable output is proposal-only. Convert the exact "
                "reviewed step or condition into a content-addressed manual "
                "approval before workflow generation."
            )
        for ambiguity in plan.ambiguities:
            approval = manual_approvals.get(ambiguity.ambiguity_id)
            if approval is None:
                continue
            target = approval.target_mapping
            mapping_type = target.get("type")
            if mapping_type == "runner":
                self._set_path(working, ambiguity.location, target["runner_label"])
                approved_mappings_applied += 1
            elif mapping_type == "repository_checkout":
                source_step = self._get_path(working, ambiguity.location)
                if not isinstance(source_step, Mapping):
                    raise ValueError("Approved repository checkout does not target a step")
                replacement = copy.deepcopy(dict(source_step))
                replacement["checkout"] = target["repository"]
                if target.get("ref"):
                    replacement["ref"] = target["ref"]
                if target.get("token_secret"):
                    replacement["token"] = (
                        "${{ secrets." + str(target["token_secret"]) + " }}"
                    )
                self._set_path(working, ambiguity.location, replacement)
                approved_mappings_applied += 1
            elif mapping_type == "workflow_step":
                self._set_path(
                    working,
                    ambiguity.location,
                    {"__ado2gh_gha_step__": copy.deepcopy(target["step"])},
                )
                approved_mappings_applied += 1
            elif mapping_type == "workflow_condition":
                self._set_path(
                    working,
                    ambiguity.location,
                    str(target["condition"]),
                )
                approved_mappings_applied += 1
        result = self.transformer._transform_deterministic(working, output_dir)
        result.setdefault("stats", {})
        result["stats"].update({
            "planned_deterministic_steps": plan.deterministic_steps,
            "expected_executable_steps": plan.expected_executable_steps,
            "llm_resolutions_applied": applied,
            "approved_target_mappings_applied": approved_mappings_applied,
            "redacted_inline_credentials": redacted_inline_credentials,
        })
        return result

    @classmethod
    def _sanitize_inline_credentials(cls, meta: PipelineMetadata) -> int:
        """Remove credential literals before a workflow artifact is written.

        Secret variables already become GitHub secret references and are left
        intact. Credentials embedded in scripts, task inputs, non-secret
        variables, parameters, or schedules are replaced with a visible marker;
        the validator treats that marker as blocking so the redacted artifact
        can never be reported production-ready.
        """

        count = 0

        def walk(value: Any) -> Any:
            nonlocal count
            if isinstance(value, str):
                safe = redact_text_secrets(value)
                if safe != value:
                    count += 1
                return safe
            if isinstance(value, list):
                for index, item in enumerate(value):
                    value[index] = walk(item)
                return value
            if isinstance(value, dict):
                for key, item in list(value.items()):
                    value[key] = walk(item)
                return value
            return value

        meta.parameters = walk(meta.parameters)
        meta.trigger_schedules = walk(meta.trigger_schedules)
        meta.variable_groups = walk(meta.variable_groups)
        for variable in meta.variables:
            if not variable.is_secret:
                variable.value = walk(variable.value)
        for stage in meta.stages:
            stage.condition = walk(stage.condition)
            stage.jobs = walk(stage.jobs)
            for variable in stage.variables:
                if not variable.is_secret:
                    variable.value = walk(variable.value)
        return count

    @classmethod
    def _get_path(cls, root: Any, path: tuple[Any, ...]) -> Any:
        current = root
        for component in path:
            current = cls._get_component(current, component)
        return current

    @classmethod
    def _set_path(cls, root: Any, path: tuple[Any, ...], value: Any) -> None:
        if not path:
            raise ValueError("Cannot replace the pipeline metadata root")
        current = root
        for component in path[:-1]:
            current = cls._get_component(current, component)
        final = path[-1]
        if isinstance(current, list):
            if not isinstance(final, int):
                raise ValueError(f"Expected list index at path component {final!r}")
            current[final] = value
        elif isinstance(current, dict):
            current[final] = value
        else:
            if not isinstance(final, str) or not hasattr(current, final):
                raise ValueError(f"Invalid metadata replacement path component {final!r}")
            setattr(current, final, value)

    @staticmethod
    def _get_component(current: Any, component: Any) -> Any:
        if isinstance(current, (list, tuple)):
            if not isinstance(component, int):
                raise ValueError(f"Expected list index at path component {component!r}")
            return current[component]
        if isinstance(current, dict):
            if component not in current:
                raise ValueError(f"Missing metadata path component {component!r}")
            return current[component]
        if isinstance(component, str) and hasattr(current, component):
            return getattr(current, component)
        raise ValueError(f"Invalid metadata path component {component!r}")
