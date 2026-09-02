"""Shared dynamic form field models and enrichment helpers."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class FormFieldOption(BaseModel):
    """One selectable option the orchestrator LLM or context layer recommends."""

    value: str
    label: str = ""
    description: str = ""
    recommended: bool = False

    def normalized(self) -> dict[str, Any]:
        value = str(self.value or "").strip()[:60]
        label = str(self.label or value).strip()[:60]
        out: dict[str, Any] = {"value": value, "label": label}
        desc = str(self.description or "").strip()
        if desc:
            out["description"] = desc[:200]
        if self.recommended:
            out["recommended"] = True
        return out


def normalize_form_option(opt: Any) -> dict[str, Any]:
    """Normalize string or dict options for UI + sanitize_form."""
    if isinstance(opt, FormFieldOption):
        return opt.normalized()
    if isinstance(opt, dict):
        try:
            return FormFieldOption.model_validate(opt).normalized()
        except Exception:
            value = str(opt.get("value") or opt.get("label") or "").strip()
            label = str(opt.get("label") or value).strip()
            out: dict[str, Any] = {"value": value[:60], "label": label[:60]}
            if opt.get("description"):
                out["description"] = str(opt["description"])[:200]
            if opt.get("recommended"):
                out["recommended"] = True
            return out
    text = str(opt).strip()
    return {"value": text[:60], "label": text[:60]}


def field_dict_from_spec(
    spec: Any,
    *,
    planner_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a UI field dict from IntakeFieldSpec / OperatorInputFieldSpec + context hints."""
    name = str(getattr(spec, "name", "field") or "field")
    field: dict[str, Any] = {
        "name": name[:40],
        "label": str(getattr(spec, "label", name) or name)[:60],
        "type": getattr(spec, "field_type", None) or "text",
        "required": bool(getattr(spec, "required", False)),
    }
    desc = str(getattr(spec, "description", "") or "").strip()
    if desc:
        field["description"] = desc[:200]

    hints = ((planner_context or {}).get("field_recommendations") or {}).get(name) or {}
    if not isinstance(hints, dict):
        hints = {}

    placeholder = hints.get("placeholder") or getattr(spec, "placeholder", None)
    if placeholder:
        field["placeholder"] = str(placeholder)[:120]

    recommended_value = hints.get("recommended_value")
    if recommended_value is None:
        recommended_value = getattr(spec, "recommended_value", None)
    if recommended_value is not None and str(recommended_value).strip() != "":
        field["recommended_value"] = str(recommended_value).strip()[:120]

    raw_options = hints.get("options")
    if raw_options is None:
        raw_options = getattr(spec, "options", None)
    if raw_options and isinstance(raw_options, list):
        field["options"] = [normalize_form_option(o) for o in raw_options[:10]]

    return field


def build_field_recommendations(
    session: dict[str, Any],
    *,
    repo_suggestions: list[str] | None = None,
    missing_fields: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Context-derived field hints (not hardcoded form templates)."""
    recs: dict[str, dict[str, Any]] = {}
    missing = set(missing_fields or [])

    if "repository_id" in missing or repo_suggestions:
        suggestions = [str(s).strip() for s in (repo_suggestions or []) if str(s).strip()]
        if suggestions:
            recs["repository_id"] = {
                "placeholder": "Project/RepoName",
                "recommended_value": suggestions[0],
                "options": [
                    {
                        "value": repo,
                        "label": repo,
                        "recommended": idx == 0,
                    }
                    for idx, repo in enumerate(suggestions[:8])
                ],
            }
        else:
            recs["repository_id"] = {
                "placeholder": "Project/RepoName",
                "description": "ADO repository in Project/RepoName format.",
            }

    if "dry_run" in missing:
        prefer_dry = bool(session.get("dry_run", True))
        recs["dry_run"] = {
            "recommended_value": "true" if prefer_dry else "false",
            "options": [
                {
                    "value": "true",
                    "label": "Dry-run",
                    "description": "Simulate migration without applying GitHub writes.",
                    "recommended": prefer_dry,
                },
                {
                    "value": "false",
                    "label": "Live",
                    "description": "Apply migration changes to GitHub (requires approval when enforced).",
                    "recommended": not prefer_dry,
                },
            ],
        }

    if "phase" in missing:
        discovery = session.get("discovery_snapshot") or {}
        phases: list[str] = []
        if isinstance(discovery, dict):
            for row in discovery.get("phase_assignments") or []:
                if isinstance(row, dict) and row.get("phase"):
                    phases.append(str(row["phase"]))
        unique_phases = sorted({p for p in phases if p})
        if unique_phases:
            recs["phase"] = {
                "options": [
                    {"value": p, "label": p, "recommended": idx == 0}
                    for idx, p in enumerate(unique_phases[:8])
                ],
                "recommended_value": unique_phases[0],
            }

    if "cancellation_action" in missing:
        recs["cancellation_action"] = {
            "recommended_value": "stop",
            "options": [
                {
                    "value": "stop",
                    "label": "Stop migration",
                    "description": "End the session without deleting GitHub resources.",
                    "recommended": True,
                },
                {
                    "value": "rollback",
                    "label": "Rollback and stop",
                    "description": "Delete GitHub resources created in this session, then stop.",
                    "recommended": False,
                },
            ],
        }

    if "plan_confirmed" in missing or "confirm_execute" in missing or "plan_notes" in missing:
        recs.setdefault("plan_confirmed", {
            "description": "Check when the plan targets and scope look correct.",
        })
        recs.setdefault("confirm_execute", {
            "description": "Start the migration pipeline immediately after you confirm.",
            "recommended_value": False,
        })
        recs.setdefault("plan_notes", {
            "placeholder": "Describe plan changes or questions (optional if confirming).",
        })

    return recs


def resolution_options_from_context(
    *,
    blockers: list[dict[str, Any]] | None = None,
    probe_failures: bool = False,
) -> list[dict[str, Any]]:
    """Build operator resolution options from blocker context (LLM may override in forms)."""
    blockers = blockers or []
    text = " ".join(str(b.get("blocker") or b.get("specific_failure") or "") for b in blockers).lower()
    options: list[dict[str, Any]] = []

    if probe_failures or "github" in text and ("not exist" in text or "missing" in text):
        options.append({
            "value": "confirm_github_repo_create",
            "label": "Confirm GitHub repo creation",
            "description": "Proceed assuming the target GitHub repository will be created or already exists.",
            "recommended": "not exist" in text,
        })
        options.append({
            "value": "fix_repository_id",
            "label": "Correct repository ID",
            "description": "Update the ADO/GitHub repository mapping before continuing.",
            "recommended": "not found" in text or "verification" in text,
        })
    elif any("inventory" in str(b.get("blocker", "")).lower() for b in blockers):
        options.append({
            "value": "run_pipeline_inventory",
            "label": "Run pipeline inventory",
            "description": "Refresh pipeline discovery before replanning.",
            "recommended": True,
        })

    options.extend([
        {
            "value": "skip_blocked_scope",
            "label": "Skip blocked scope",
            "description": "Continue migration without the blocked scope.",
            "recommended": False,
        },
        {
            "value": "replan",
            "label": "Replan",
            "description": "Send the planner updated context and build a revised plan.",
            "recommended": not options,
        },
    ])

    if not any(o.get("recommended") for o in options):
        options[0]["recommended"] = True
    return options[:8]
