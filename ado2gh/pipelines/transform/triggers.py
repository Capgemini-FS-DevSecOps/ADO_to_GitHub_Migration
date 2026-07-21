"""Trigger and schedule conversion for GitHub Actions workflows."""
from __future__ import annotations

from typing import Any, Optional

from ado2gh.models import PipelineMetadata

_DOW_NAMES = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
_DOW_BITS = [1, 2, 4, 8, 16, 32, 64]


def build_triggers(meta: PipelineMetadata, warnings: list[str]) -> dict:
    """Build the ``on:`` block for a GHA workflow."""
    on: dict[str, Any] = {}

    if meta.trigger_branches:
        on["push"] = {"branches": list(meta.trigger_branches)}

    if meta.trigger_pr_branches:
        on["pull_request"] = {"branches": list(meta.trigger_pr_branches)}

    if meta.trigger_schedules:
        crons: list[dict[str, str]] = []
        for sched in meta.trigger_schedules:
            cron_expr = ado_schedule_to_cron(sched, warnings)
            if cron_expr:
                crons.append({"cron": cron_expr})
        if crons:
            on["schedule"] = crons

    wd: dict = {}
    if getattr(meta, "parameters", None):
        inputs: dict = {}
        for p in meta.parameters:
            name = p.get("name")
            if not name:
                continue
            p_type = (p.get("type") or "string").lower()
            values = p.get("values")
            gha_input: dict = {"required": p.get("default") is None}
            if isinstance(values, list) and values:
                gha_input["type"] = "choice"
                gha_input["options"] = [str(v) for v in values]
            elif p_type in ("boolean", "bool"):
                gha_input["type"] = "boolean"
            elif p_type == "number":
                gha_input["type"] = "number"
            else:
                gha_input["type"] = "string"
            if p.get("default") is not None:
                gha_input["default"] = (
                    str(p["default"]) if gha_input["type"] != "boolean"
                    else bool(p["default"])
                )
            inputs[name] = gha_input
        if inputs:
            wd["inputs"] = inputs
    on["workflow_dispatch"] = wd

    if not on:
        on["workflow_dispatch"] = wd

    return {"on": on}


def ado_schedule_to_cron(sched: dict, warnings: list[str]) -> Optional[str]:
    minute = sched.get("minute", sched.get("minutes", 0))
    hour = sched.get("hour", sched.get("hours", 0))
    days_raw = sched.get("daysToRun", sched.get("days_to_build", 0))

    if isinstance(days_raw, int):
        if days_raw == 0:
            dow = "*"
        else:
            dow_parts = []
            for bit, name in zip(_DOW_BITS, _DOW_NAMES):
                if days_raw & bit:
                    dow_parts.append(name)
            dow = ",".join(dow_parts) if dow_parts else "*"
    elif isinstance(days_raw, list):
        dow = ",".join(str(d).upper()[:3] for d in days_raw) if days_raw else "*"
    else:
        dow = "*"
        warnings.append(
            f"Unrecognised schedule day format: {days_raw!r} – defaulting to '*'."
        )

    branch = sched.get("branch", "")
    if branch:
        warnings.append(
            f"ADO schedule targets branch '{branch}' – GHA schedule "
            f"triggers always run on the default branch."
        )

    return f"{minute} {hour} * * {dow}"
