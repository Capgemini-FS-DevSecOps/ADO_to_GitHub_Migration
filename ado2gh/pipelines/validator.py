"""Deterministic structural, semantic, and security validation for workflows."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import yaml

from ado2gh.models import PipelineMetadata
from ado2gh.pipelines.approvals import (
    ApprovalManifestError,
    workflow_secret_names,
)
from ado2gh.pipelines.llm import contains_sensitive_literal
from ado2gh.pipelines.pev_types import (
    ConversionPlan,
    FindingSeverity,
    LLMResolution,
    ValidationReport,
)


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.Node, deep: bool = False) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found duplicate key {key!r}", key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class PipelineValidationPolicy:
    require_permissions: bool = True
    require_pinned_action_sha: bool = False
    forbid_remote_script_execution: bool = True
    max_workflow_bytes: int = 1_000_000
    max_jobs: int = 256
    max_steps_per_job: int = 1_000
    # Empty preserves the reusable library validator's compatibility mode.
    # The enterprise factory always supplies a reviewed action catalog.
    allowed_actions: tuple[str, ...] = ()


class PipelineWorkflowValidator:
    """Validate generated YAML without calling GitHub or an LLM."""

    _JOB_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
    _ADO_MACRO = re.compile(r"\$\([A-Za-z_][A-Za-z0-9_.-]*\)")
    _MUTABLE_REF = re.compile(r"(?i)^(?:main|master|latest|dev|develop|head)$")
    _FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
    _SENSITIVE_NAME = re.compile(
        r"(?i)(secret|password|passwd|token|api[_-]?key|private[_-]?key|"
        r"credential|authorization|account[_-]?key|shared[_-]?access[_-]?key|"
        r"shared[_-]?access[_-]?signature|connection[_-]?string|sas)"
    )
    _REMOTE_EXEC = re.compile(
        r"(?is)(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:sh|bash)|"
        r"invoke-webrequest\b[^\n|]*\|\s*(?:iex|invoke-expression)"
    )
    _UNTRUSTED_SCRIPT_EXPR = re.compile(
        r"\$\{\{\s*github\.event\.(?:pull_request\.(?:title|body)|issue\.(?:title|body)|comment\.body)"
    )
    _DESTRUCTIVE_ROOT_COMMAND = re.compile(
        r"(?im)(?:^|[;&|]\s*)(?:sudo\s+)?rm\s+-[^\n]*r[^\n]*f[^\n]*\s(?:/|~|\$HOME)(?:\s|$)|"
        r"remove-item\s+(?:-recurse\s+)?(?:[A-Za-z]:\\|/)(?:\s|$)"
    )
    _SECRET_EXFILTRATION = re.compile(
        r"(?is)(?:echo|printf|curl|wget|invoke-webrequest)[^\n]*\$\{\{\s*secrets\."
    )

    def __init__(self, policy: Optional[PipelineValidationPolicy] = None) -> None:
        self.policy = policy or PipelineValidationPolicy()

    def validate(
        self,
        workflow_file: Path,
        meta: PipelineMetadata,
        plan: ConversionPlan,
        resolutions: Mapping[str, LLMResolution],
        externally_resolved: Iterable[str] = (),
    ) -> ValidationReport:
        report = ValidationReport()
        path = Path(workflow_file)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            report.add("workflow_unreadable", FindingSeverity.ERROR, str(exc), str(path))
            return report

        report.workflow_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        if len(raw) > self.policy.max_workflow_bytes:
            report.add(
                "workflow_too_large", FindingSeverity.ERROR,
                f"Workflow is {len(raw)} bytes; limit is {self.policy.max_workflow_bytes}.",
                str(path),
            )
            return report
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            report.add("workflow_not_utf8", FindingSeverity.ERROR, "Workflow must be UTF-8.", str(path))
            return report
        try:
            workflow = yaml.load(text, Loader=_UniqueKeyLoader)
        except yaml.YAMLError as exc:
            report.add("invalid_yaml", FindingSeverity.ERROR, f"Invalid workflow YAML: {exc}", str(path))
            return report
        if not isinstance(workflow, dict):
            report.add("workflow_not_mapping", FindingSeverity.ERROR, "Workflow root must be a mapping.")
            return report
        report.parsed_workflow = workflow

        self._validate_plan(report, plan, resolutions, set(externally_resolved))
        self._validate_llm_resolutions(report, resolutions)
        self._validate_root(report, workflow)
        self._validate_jobs(report, workflow.get("jobs"))
        self._validate_secret_expression_flows(report, workflow, text)
        self._validate_ado_residue(report, workflow)
        self._validate_secret_literals(report, workflow, meta)
        self._validate_source_coverage(report, workflow, plan)
        self._validate_fidelity_boundaries(report, workflow, meta, plan)
        for notice in plan.notices:
            report.add("conversion_notice", FindingSeverity.WARNING, notice)
        return report

    @staticmethod
    def _validate_plan(
        report: ValidationReport,
        plan: ConversionPlan,
        resolutions: Mapping[str, LLMResolution],
        externally_resolved: set[str],
    ) -> None:
        for ambiguity in plan.ambiguities:
            if (
                not ambiguity.required
                or ambiguity.ambiguity_id in resolutions
                or ambiguity.ambiguity_id in externally_resolved
            ):
                continue
            code = "unresolved_llm_ambiguity" if ambiguity.llm_eligible else "external_requirement"
            report.add(
                code,
                FindingSeverity.MANUAL_REVIEW,
                ambiguity.rationale,
                ".".join(str(p) for p in ambiguity.location),
            )

    def _validate_root(self, report: ValidationReport, workflow: Mapping[str, Any]) -> None:
        if not isinstance(workflow.get("name"), str) or not workflow.get("name", "").strip():
            report.add("missing_name", FindingSeverity.ERROR, "Workflow requires a non-empty name.")
        triggers = workflow.get("on", workflow.get(True))
        if not isinstance(triggers, (dict, list, str)) or not triggers:
            report.add("missing_trigger", FindingSeverity.ERROR, "Workflow requires at least one trigger.", "on")
        if isinstance(triggers, Mapping) and "pull_request_target" in triggers:
            report.add(
                "unsafe_pull_request_target", FindingSeverity.ERROR,
                "pull_request_target can expose privileged tokens to untrusted fork code.", "on.pull_request_target",
            )
        if self.policy.require_permissions and "permissions" not in workflow:
            report.add(
                "missing_permissions", FindingSeverity.MANUAL_REVIEW,
                "Declare least-privilege workflow permissions explicitly.", "permissions",
            )
        permissions = workflow.get("permissions")
        if permissions == "write-all":
            report.add("write_all_permissions", FindingSeverity.ERROR, "write-all permissions are prohibited.")

    def _validate_jobs(self, report: ValidationReport, jobs: Any) -> None:
        if not isinstance(jobs, Mapping) or not jobs:
            report.add("missing_jobs", FindingSeverity.ERROR, "Workflow requires at least one job.", "jobs")
            return
        if len(jobs) > self.policy.max_jobs:
            report.add("too_many_jobs", FindingSeverity.ERROR, f"Workflow exceeds {self.policy.max_jobs} jobs.")

        dependencies: dict[str, list[str]] = {}
        for job_id, raw_job in jobs.items():
            location = f"jobs.{job_id}"
            if not isinstance(job_id, str) or not self._JOB_ID.match(job_id):
                report.add("invalid_job_id", FindingSeverity.ERROR, f"Invalid GitHub job id {job_id!r}.", location)
            if not isinstance(raw_job, Mapping):
                report.add("invalid_job", FindingSeverity.ERROR, "Job must be a mapping.", location)
                continue
            reusable = bool(raw_job.get("uses"))
            if reusable and ("runs-on" in raw_job or "steps" in raw_job):
                report.add(
                    "invalid_reusable_job", FindingSeverity.ERROR,
                    "Reusable-workflow jobs cannot also define runs-on or steps.", location,
                )
            if not reusable and not raw_job.get("runs-on"):
                report.add("missing_runner", FindingSeverity.ERROR, "Job requires runs-on.", location)
            needs = raw_job.get("needs", [])
            if isinstance(needs, str):
                needs = [needs]
            if not isinstance(needs, list) or not all(isinstance(item, str) for item in needs):
                report.add("invalid_needs", FindingSeverity.ERROR, "needs must be a job id or list of ids.", location)
                needs = []
            dependencies[str(job_id)] = list(needs)
            if not reusable:
                self._validate_steps(report, raw_job.get("steps"), location)
            if raw_job.get("permissions") == "write-all":
                report.add("write_all_permissions", FindingSeverity.ERROR, "write-all permissions are prohibited.", location)

        for job_id, needs in dependencies.items():
            for dependency in needs:
                if dependency not in jobs:
                    report.add(
                        "unknown_job_dependency", FindingSeverity.ERROR,
                        f"Job '{job_id}' needs missing job '{dependency}'.", f"jobs.{job_id}.needs",
                    )
        self._validate_dependency_cycles(report, dependencies)

    @staticmethod
    def _validate_secret_expression_flows(
        report: ValidationReport,
        workflow: Mapping[str, Any],
        workflow_text: str,
    ) -> None:
        try:
            workflow_secret_names(workflow_text)
        except ApprovalManifestError as exc:
            report.add(
                "unsafe_secret_expression",
                FindingSeverity.ERROR,
                str(exc),
                "jobs",
            )

        def env_secret_names(value: Any, location: str) -> set[str]:
            if not isinstance(value, Mapping):
                return set()
            result: set[str] = set()
            for item in value.values():
                if not isinstance(item, str):
                    continue
                try:
                    result.update(workflow_secret_names(item))
                except ApprovalManifestError as exc:
                    report.add(
                        "unsafe_secret_expression",
                        FindingSeverity.ERROR,
                        str(exc),
                        location,
                    )
            return result

        inherited = env_secret_names(workflow.get("env"), "env")
        if inherited:
            report.add(
                "secret_in_workflow_env",
                FindingSeverity.ERROR,
                "GitHub secrets cannot be placed in workflow-global env; "
                "that grants every job and third-party action access.",
                "env",
            )
        jobs = workflow.get("jobs")
        if not isinstance(jobs, Mapping):
            return
        for job_id, job in jobs.items():
            if not isinstance(job, Mapping):
                continue
            job_direct_secrets = env_secret_names(
                job.get("env"), f"jobs.{job_id}.env"
            )
            if job_direct_secrets:
                report.add(
                    "secret_in_job_env",
                    FindingSeverity.ERROR,
                    "GitHub secrets cannot be placed in job-global env; pass "
                    "only the required secret to an approved action input.",
                    f"jobs.{job_id}.env",
                )
            job_secrets = inherited | job_direct_secrets
            steps = job.get("steps")
            if not isinstance(steps, list):
                continue
            for index, step in enumerate(steps):
                if not isinstance(step, Mapping):
                    continue
                location = f"jobs.{job_id}.steps.{index}"
                step_secrets = env_secret_names(
                    step.get("env"), f"{location}.env"
                )
                if step_secrets:
                    report.add(
                        "secret_in_step_env",
                        FindingSeverity.ERROR,
                        "GitHub secrets cannot be placed in step env; pass "
                        "only the required secret through a catalog-approved "
                        "action input.",
                        f"{location}.env",
                    )
                if not step.get("run"):
                    continue
                command = str(step["run"])
                try:
                    direct = set(workflow_secret_names(command))
                except ApprovalManifestError as exc:
                    report.add(
                        "unsafe_secret_expression",
                        FindingSeverity.ERROR,
                        str(exc),
                        location,
                    )
                    direct = set()
                secret_env = job_secrets | step_secrets
                if direct or secret_env:
                    report.add(
                        "secret_in_shell",
                        FindingSeverity.ERROR,
                        "Shell steps cannot receive GitHub secrets; use a "
                        "catalog-approved action input so secret values are not "
                        "available to arbitrary source-controlled code.",
                        location,
                    )

    def _validate_steps(self, report: ValidationReport, steps: Any, job_location: str) -> None:
        if not isinstance(steps, list) or not steps:
            report.add("missing_steps", FindingSeverity.ERROR, "Job requires at least one step.", f"{job_location}.steps")
            return
        if len(steps) > self.policy.max_steps_per_job:
            report.add(
                "too_many_steps", FindingSeverity.ERROR,
                f"Job exceeds {self.policy.max_steps_per_job} steps.", f"{job_location}.steps",
            )
        for index, step in enumerate(steps):
            location = f"{job_location}.steps.{index}"
            if not isinstance(step, Mapping):
                report.add("invalid_step", FindingSeverity.ERROR, "Step must be a mapping.", location)
                continue
            has_run = bool(step.get("run"))
            has_uses = bool(step.get("uses"))
            if has_run == has_uses:
                report.add(
                    "invalid_step_execution", FindingSeverity.ERROR,
                    "Step must contain exactly one of run or uses.", location,
                )
            if has_uses:
                self._validate_action_reference(report, str(step["uses"]), location)
                self._validate_action_input_contract(
                    report,
                    str(step["uses"]),
                    step.get("with"),
                    location,
                )
            if has_run:
                command = str(step["run"])
                if self.policy.forbid_remote_script_execution and self._REMOTE_EXEC.search(command):
                    report.add(
                        "remote_script_execution", FindingSeverity.ERROR,
                        "Piping downloaded content directly to a shell is prohibited.", location,
                    )
                if self._UNTRUSTED_SCRIPT_EXPR.search(command):
                    report.add(
                        "untrusted_expression_in_script", FindingSeverity.ERROR,
                        "Untrusted event text is interpolated directly into a shell script.", location,
                    )
                if self._DESTRUCTIVE_ROOT_COMMAND.search(command):
                    report.add(
                        "destructive_root_command", FindingSeverity.ERROR,
                        "Workflow command can recursively delete a filesystem root.", location,
                    )
                if self._SECRET_EXFILTRATION.search(command):
                    report.add(
                        "secret_exfiltration", FindingSeverity.ERROR,
                        "Workflow command may print or transmit a GitHub secret.", location,
                    )
            name = str(step.get("name", ""))
            if name.startswith("TODO: migrate") or "needs manual migration" in str(step.get("run", "")):
                report.add(
                    "manual_todo_step", FindingSeverity.MANUAL_REVIEW,
                    "Generated TODO step does not preserve source behavior.", location,
                )

    def _validate_llm_resolutions(
        self,
        report: ValidationReport,
        resolutions: Mapping[str, LLMResolution],
    ) -> None:
        for ambiguity_id, resolution in resolutions.items():
            replacement = resolution.replacement
            if isinstance(replacement, Mapping) and replacement.get("run"):
                code = "llm_shell_requires_content_approval"
                message = (
                    "LLM-generated shell is a proposal, not executable authorization; "
                    "approve the exact step with a content-addressed manual manifest."
                )
            elif isinstance(replacement, Mapping) and replacement.get("uses"):
                code = "llm_action_requires_content_approval"
                message = (
                    "A pinned, catalog-approved action is supply-chain safe but its "
                    "semantic equivalence still requires exact content-addressed approval."
                )
            else:
                code = "llm_condition_requires_content_approval"
                message = (
                    "An LLM-translated condition cannot prove source semantic parity; "
                    "approve the exact condition with a content-addressed manifest."
                )
            report.add(
                code,
                FindingSeverity.MANUAL_REVIEW,
                message,
                ambiguity_id,
            )
            if not isinstance(replacement, Mapping) or not replacement.get("run"):
                continue
            command = str(replacement["run"])
            if self._DESTRUCTIVE_ROOT_COMMAND.search(command):
                report.add(
                    "llm_destructive_root_command", FindingSeverity.ERROR,
                    "LLM-generated command can recursively delete a filesystem root.",
                    ambiguity_id,
                )
            if self._SECRET_EXFILTRATION.search(command):
                report.add(
                    "llm_secret_exfiltration", FindingSeverity.ERROR,
                    "LLM-generated command may print or transmit a GitHub secret.",
                    ambiguity_id,
                )

    def _validate_action_reference(
        self,
        report: ValidationReport,
        uses: str,
        location: str,
    ) -> None:
        if uses.startswith("./"):
            return
        if uses.startswith("docker://"):
            if self.policy.allowed_actions:
                report.add(
                    "container_action_not_approved",
                    FindingSeverity.ERROR,
                    "Docker URL actions are outside the approved action catalog.",
                    location,
                )
            return
        if "@" not in uses:
            report.add("unversioned_action", FindingSeverity.ERROR, "Action reference must include @ref.", location)
            return
        action_path, ref = uses.rsplit("@", 1)
        if self.policy.allowed_actions:
            allowed = {item.casefold() for item in self.policy.allowed_actions}
            parts = action_path.split("/")
            repository = "/".join(parts[:2]) if len(parts) >= 2 else action_path
            if action_path.casefold() not in allowed and repository.casefold() not in allowed:
                report.add(
                    "action_not_approved",
                    FindingSeverity.ERROR,
                    f"Action '{action_path}' is outside the approved action catalog.",
                    location,
                )
        if self._MUTABLE_REF.match(ref):
            report.add("mutable_action_ref", FindingSeverity.ERROR, f"Mutable action ref '@{ref}' is prohibited.", location)
        elif self.policy.require_pinned_action_sha and not self._FULL_SHA.match(ref):
            report.add(
                "action_not_sha_pinned", FindingSeverity.MANUAL_REVIEW,
                f"Action '{uses}' is not pinned to a full commit SHA.", location,
            )
        elif not self._FULL_SHA.match(ref):
            report.add(
                "action_tag_pin", FindingSeverity.WARNING,
                f"Action '{uses}' uses a tag; enterprise policy may require a commit SHA.", location,
            )

    @staticmethod
    def _validate_action_input_contract(
        report: ValidationReport,
        uses: str,
        with_block: Any,
        location: str,
    ) -> None:
        """Enforce action-specific inputs needed for deterministic behavior."""
        action_path = uses.rsplit("@", 1)[0].casefold()
        repository = "/".join(action_path.split("/")[:2])
        values = with_block if isinstance(with_block, Mapping) else {}

        required_by_action = {
            "actions/setup-node": ("node-version",),
            "actions/setup-python": ("python-version", "architecture"),
            "actions/setup-dotnet": ("dotnet-version",),
            "actions/setup-go": ("go-version",),
        }
        for key in required_by_action.get(repository, ()):
            if key not in values or values.get(key) in (None, ""):
                report.add(
                    "missing_action_input_contract",
                    FindingSeverity.ERROR,
                    f"{repository} requires explicit '{key}' for deterministic conversion.",
                    f"{location}.with.{key}",
                )

        if repository == "actions/setup-python" and values.get("architecture") != "x64":
            report.add(
                "python_architecture_contract",
                FindingSeverity.ERROR,
                "UsePythonVersion@0 deterministic conversion requires explicit x64 architecture.",
                f"{location}.with.architecture",
            )
        if repository == "actions/setup-java":
            if not values.get("distribution"):
                report.add(
                    "missing_java_distribution",
                    FindingSeverity.ERROR,
                    "setup-java requires an explicitly approved distribution.",
                    f"{location}.with.distribution",
                )
            selectors = sum(
                bool(values.get(key))
                for key in ("java-version", "java-version-file")
            )
            if selectors != 1:
                report.add(
                    "invalid_java_version_contract",
                    FindingSeverity.ERROR,
                    "setup-java requires exactly one explicit Java version selector.",
                    f"{location}.with",
                )
        if repository == "actions/checkout":
            persist = values.get("persist-credentials")
            if persist is not False and str(persist).casefold() != "false":
                report.add(
                    "checkout_credentials_persisted",
                    FindingSeverity.ERROR,
                    "Checkout must explicitly disable persisted GitHub credentials.",
                    f"{location}.with.persist-credentials",
                )

    @staticmethod
    def _validate_dependency_cycles(
        report: ValidationReport,
        dependencies: Mapping[str, list[str]],
    ) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(job: str) -> bool:
            if job in visiting:
                return True
            if job in visited:
                return False
            visiting.add(job)
            if any(dep in dependencies and visit(dep) for dep in dependencies.get(job, [])):
                return True
            visiting.remove(job)
            visited.add(job)
            return False

        if any(visit(job) for job in dependencies if job not in visited):
            report.add("job_dependency_cycle", FindingSeverity.ERROR, "Job needs graph contains a cycle.", "jobs")

    def _validate_ado_residue(self, report: ValidationReport, workflow: Mapping[str, Any]) -> None:
        # PyYAML 1.1 may parse an unquoted ``on`` key as boolean True; avoid
        # sorting heterogeneous keys while still validating such workflows.
        serialized = json.dumps(workflow, sort_keys=False, default=str)
        residue_patterns = {
            "ado_parameter_expression": r"\$\{\{\s*parameters(?:\.|\[)",
            "ado_variable_expression": r"\bvariables(?:\.|\[)",
            "ado_runtime_expression": r"\$\[",
            "ado_logging_command": r"##vso\[",
        }
        for code, pattern in residue_patterns.items():
            if re.search(pattern, serialized):
                report.add(code, FindingSeverity.ERROR, "ADO-only syntax remains in generated workflow.")
        if self._ADO_MACRO.search(serialized):
            report.add(
                "ado_macro_expression", FindingSeverity.MANUAL_REVIEW,
                "An ADO $(VARIABLE) macro remains; verify it is not a shell command substitution.",
            )

    def _validate_secret_literals(
        self,
        report: ValidationReport,
        workflow: Mapping[str, Any],
        meta: PipelineMetadata,
    ) -> None:
        serialized = json.dumps(workflow, sort_keys=False, default=str)
        for variable in meta.variables:
            if variable.is_secret and variable.value and len(variable.value) >= 4 and variable.value in serialized:
                report.add(
                    "secret_value_leak", FindingSeverity.ERROR,
                    f"Literal value of secret variable '{variable.name}' appears in workflow.",
                )

        def walk(value: Any, location: str = "") -> Iterable[tuple[str, Any]]:
            if isinstance(value, Mapping):
                for key, item in value.items():
                    child = f"{location}.{key}" if location else str(key)
                    yield child, item
                    yield from walk(item, child)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    yield from walk(item, f"{location}.{index}")

        for location, value in walk(workflow):
            key = location.rsplit(".", 1)[-1]
            if isinstance(value, (dict, list)):
                continue
            if (
                key.casefold() == "persist-credentials"
                and (value is False or str(value).casefold() == "false")
            ):
                # This is a negative security control, not a credential.
                continue
            text = str(value)
            if "<redacted>" in text.casefold() or "[redacted]" in text.casefold():
                report.add(
                    "redacted_credential_placeholder",
                    FindingSeverity.ERROR,
                    "An inline credential was removed before artifact creation; "
                    "replace it with an approved GitHub secret reference.",
                    location,
                )
                continue
            if contains_sensitive_literal(text):
                report.add(
                    "inline_credential_literal",
                    FindingSeverity.ERROR,
                    "Credential-like literal is prohibited in workflow content.",
                    location,
                )
                continue
            if not self._SENSITIVE_NAME.search(key):
                continue
            if text and not re.match(r"^\$\{\{\s*(?:secrets|vars)\.", text):
                report.add(
                    "literal_sensitive_value", FindingSeverity.ERROR,
                    f"Sensitive-looking field '{key}' contains a literal value.", location,
                )

    @staticmethod
    def _validate_source_coverage(
        report: ValidationReport,
        workflow: Mapping[str, Any],
        plan: ConversionPlan,
    ) -> None:
        generated_steps = 0
        for job in (workflow.get("jobs") or {}).values():
            if isinstance(job, Mapping) and isinstance(job.get("steps"), list):
                generated_steps += len(job["steps"])
        if generated_steps < plan.expected_executable_steps:
            report.add(
                "source_step_loss", FindingSeverity.ERROR,
                f"Generated {generated_steps} steps for {plan.expected_executable_steps} executable source steps.",
                "jobs",
            )

    @staticmethod
    def _validate_fidelity_boundaries(
        report: ValidationReport,
        workflow: Mapping[str, Any],
        meta: PipelineMetadata,
        plan: ConversionPlan,
    ) -> None:
        """Verify high-impact source effects and their approval boundaries.

        This is intentionally independent of the transformer. A future rule
        regression must not be able to both lose a side effect and declare its
        own output production-ready.
        """
        required_ambiguities = {
            (ambiguity.kind, tuple(ambiguity.location))
            for ambiguity in plan.ambiguities
            if ambiguity.required
        }

        source_build_and_push: list[tuple[Any, ...]] = []
        for stage_index, stage in enumerate(meta.stages):
            for job_index, job in enumerate(stage.jobs):
                if not isinstance(job, Mapping):
                    continue
                raw_steps = job.get("steps")
                if raw_steps is None and PipelineWorkflowValidator._looks_like_source_step(job):
                    raw_steps = [job]
                    base = ("stages", stage_index, "jobs", job_index)
                else:
                    base = ("stages", stage_index, "jobs", job_index, "steps")
                if not isinstance(raw_steps, list):
                    continue
                for step_index, step in enumerate(raw_steps):
                    if not isinstance(step, Mapping) or step.get("enabled", True) is False:
                        continue
                    task_name = str(step.get("task", step.get("taskName", "")))
                    if task_name != "Docker@2":
                        continue
                    inputs = step.get("inputs", {})
                    inputs = inputs if isinstance(inputs, Mapping) else {}
                    command = str(
                        inputs.get("command", "buildAndPush")
                    ).strip().casefold()
                    if command not in {"", "buildandpush"}:
                        continue
                    location = (
                        base if len(base) == 4 else base + (step_index,)
                    )
                    source_build_and_push.append(location)
                    if (
                        "docker_publish_configuration", location
                    ) not in required_ambiguities:
                        report.add(
                            "docker_publish_review_boundary_missing",
                            FindingSeverity.ERROR,
                            "Docker@2 buildAndPush lacks a required registry/auth "
                            "approval boundary in the conversion plan.",
                            ".".join(str(item) for item in location),
                        )

        generated_pushes = 0
        raw_jobs = workflow.get("jobs")
        for job in raw_jobs.values() if isinstance(raw_jobs, Mapping) else ():
            if not isinstance(job, Mapping):
                continue
            for step in job.get("steps", []) or []:
                if not isinstance(step, Mapping):
                    continue
                if not str(step.get("uses", "")).startswith(
                    "docker/build-push-action@"
                ):
                    continue
                with_block = step.get("with", {})
                push = with_block.get("push") if isinstance(with_block, Mapping) else None
                if push is True or str(push).strip().casefold() == "true":
                    generated_pushes += 1
        if generated_pushes < len(source_build_and_push):
            report.add(
                "docker_push_semantics_lost",
                FindingSeverity.ERROR,
                f"Generated workflow preserves {generated_pushes} push operation(s) "
                f"for {len(source_build_and_push)} Docker@2 buildAndPush step(s).",
                "jobs",
            )

        for index, schedule in enumerate(meta.trigger_schedules):
            location = ("trigger_schedules", index)
            unsupported = not isinstance(schedule, Mapping)
            if isinstance(schedule, Mapping):
                unsupported = bool(
                    schedule.get("branch_filters", schedule.get("branch", []))
                    or schedule.get("excluded_branches", [])
                    or schedule.get("always") is not True
                )
            if unsupported and (
                "source_semantics_review", location
            ) not in required_ambiguities:
                report.add(
                    "schedule_review_boundary_missing",
                    FindingSeverity.ERROR,
                    "ADO schedule contains branch or changes-only semantics but "
                    "the conversion plan has no required parity-review boundary.",
                    ".".join(str(item) for item in location),
                )

    @staticmethod
    def _looks_like_source_step(value: Mapping[str, Any]) -> bool:
        return any(
            key in value
            for key in (
                "task", "taskName", "script", "bash", "powershell",
                "checkout", "publish", "download",
            )
        )
