from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ado2gh.models import (
    PipelineMetadata,
    PipelineStage,
    PipelineType,
    PipelineVariable,
)
from ado2gh.pipelines.extractor import PipelineMetadataExtractor
from ado2gh.pipelines.inventory import (
    PipelineInventoryBuilder,
    PipelineInventoryError,
    _pick_best_yaml,
)
from ado2gh.pipelines.llm import (
    CallablePipelineLLMClient,
    OpenAIResponsesPipelineLLMClient,
    redact_for_llm,
)
from ado2gh.pipelines.pev_types import (
    ConversionMode,
    ConversionPlan,
    LLMResolution,
    PipelineValidationError,
    PlanAmbiguity,
)
from ado2gh.pipelines.planner import PipelineConversionPlanner
from ado2gh.pipelines.transformer import PipelineTransformer
from ado2gh.pipelines.validator import (
    PipelineValidationPolicy,
    PipelineWorkflowValidator,
)


def deterministic_meta(secret: bool = False) -> PipelineMetadata:
    variables = []
    if secret:
        variables.append(PipelineVariable("API_TOKEN", "super-secret-value", True))
    return PipelineMetadata(
        pipeline_id=7,
        pipeline_name="Build API",
        pipeline_type=PipelineType.YAML,
        project="Payments",
        repo_name="api",
        trigger_branches=["main"],
        variables=variables,
        stages=[PipelineStage(
            name="build",
            jobs=[{
                "job": "build",
                "steps": [{"bash": "python -m unittest", "displayName": "Test"}],
            }],
        )],
    )


def ambiguous_meta() -> PipelineMetadata:
    return PipelineMetadata(
        pipeline_id=8,
        pipeline_name="Ambiguous Build",
        pipeline_type=PipelineType.YAML,
        project="Payments",
        repo_name="api",
        stages=[PipelineStage(
            name="build",
            jobs=[{
                "job": "build",
                "steps": [{
                    "task": "ContosoCompiler@9",
                    "displayName": "Compile",
                    "inputs": {"target": "release", "password": "must-not-leak"},
                }],
            }],
        )],
    )


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def openai_body(output):
    return {
        "status": "completed",
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": output}],
        }],
    }


class PipelinePEVFlowTests(unittest.TestCase):
    def test_deterministic_pipeline_never_calls_provider(self):
        def should_not_run(_ambiguity, _context):
            self.fail("deterministic plan must not invoke an LLM")

        client = CallablePipelineLLMClient(should_not_run)
        with tempfile.TemporaryDirectory() as directory:
            result = PipelineTransformer(llm_client=client).transform(
                deterministic_meta(), Path(directory)
            )

        self.assertEqual("deterministic", result["plan"]["mode"])
        self.assertFalse(result["llm_used"])
        self.assertTrue(result["production_ready"])
        self.assertEqual("passed", result["validation"]["status"])

    def test_required_provider_blocks_ambiguous_conversion_and_writes_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PipelineValidationError) as raised:
                PipelineTransformer(
                    require_llm_for_ambiguity=True,
                    require_production_ready=True,
                ).transform(ambiguous_meta(), Path(directory))
            evidence_path = Path(directory) / "ambiguous_build_conversion_evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

        codes = {f.code for f in raised.exception.report.findings}
        self.assertIn("required_llm_provider_missing", codes)
        self.assertFalse(evidence["validation"]["production_ready"])
        self.assertEqual("llm_provider_unavailable", evidence["llm"]["failures"][0]["code"])

    def test_checked_high_confidence_action_is_audited_but_requires_approval(self):
        seen = []

        def resolve(ambiguity, _context):
            seen.append(ambiguity.source)
            return {
                "replacement": {
                    "name": "Compile",
                    "uses": "contoso/compiler@0123456789abcdef0123456789abcdef01234567",
                    "with": {"target": "release"},
                },
                "confidence": 0.96,
                "rationale": "The source task and target input are explicit.",
            }

        with tempfile.TemporaryDirectory() as directory:
            result = PipelineTransformer(
                llm_client=CallablePipelineLLMClient(resolve, model="replay-v1"),
                require_llm_for_ambiguity=True,
                require_production_ready=False,
            ).transform(ambiguous_meta(), Path(directory))
            workflow = result["workflow_file"].read_text(encoding="utf-8")

        self.assertFalse(result["production_ready"])
        self.assertTrue(result["llm_used"])
        self.assertNotIn(
            "contoso/compiler@0123456789abcdef0123456789abcdef01234567",
            workflow,
        )
        self.assertEqual(result["stats"]["llm_proposals"], 1)
        self.assertNotIn("must-not-leak", json.dumps(seen))
        self.assertIn(
            "llm_action_requires_content_approval",
            {finding["code"] for finding in result["validation"]["findings"]},
        )

    def test_llm_generated_shell_requires_content_addressed_approval(self):
        client = CallablePipelineLLMClient(lambda *_: {
            "replacement": {"name": "Compile", "run": "make release"},
            "confidence": 0.99,
        })
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PipelineValidationError) as raised:
                PipelineTransformer(
                    llm_client=client,
                    require_production_ready=True,
                ).transform(ambiguous_meta(), Path(directory))

        self.assertIn(
            "llm_shell_requires_content_approval",
            {item.code for item in raised.exception.report.findings},
        )

    def test_llm_translated_condition_requires_content_addressed_approval(self):
        meta = deterministic_meta()
        meta.stages[0].condition = "customAdoPredicate()"
        client = CallablePipelineLLMClient(lambda *_: {
            "replacement": "github.ref == 'refs/heads/main'",
            "confidence": 0.99,
        })

        with tempfile.TemporaryDirectory() as directory:
            result = PipelineTransformer(
                llm_client=client,
                require_production_ready=False,
            ).transform(meta, Path(directory))

        self.assertFalse(result["production_ready"])
        self.assertIn(
            "llm_condition_requires_content_approval",
            {finding["code"] for finding in result["validation"]["findings"]},
        )

    def test_malformed_and_low_confidence_outputs_fail_closed(self):
        clients = [
            CallablePipelineLLMClient(lambda *_: {
                "replacement": {"run": "make", "uses": "owner/action@v1"},
                "confidence": 0.99,
            }),
            CallablePipelineLLMClient(lambda *_: {
                "replacement": {"run": "make"},
                "confidence": 0.10,
            }),
            CallablePipelineLLMClient(lambda *_: None),
        ]
        expected = ["llm_resolution_error", "llm_low_confidence", "llm_requested_manual_review"]
        for client, expected_code in zip(clients, expected):
            with self.subTest(expected_code=expected_code), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(PipelineValidationError):
                    PipelineTransformer(
                        llm_client=client,
                        require_production_ready=True,
                    ).transform(ambiguous_meta(), Path(directory))
                evidence = json.loads(
                    (Path(directory) / "ambiguous_build_conversion_evidence.json").read_text()
                )
                self.assertEqual(expected_code, evidence["llm"]["failures"][0]["code"])

    def test_typed_callable_resolution_cannot_bypass_local_schema(self):
        client = CallablePipelineLLMClient(lambda ambiguity, _context: LLMResolution(
            ambiguity_id=ambiguity.ambiguity_id,
            replacement={"uses": "attacker/action@main"},
            confidence=0.99,
        ))
        ambiguity = PipelineConversionPlanner().plan(ambiguous_meta()).llm_ambiguities[0]

        with self.assertRaises(ValueError):
            client.resolve(ambiguity, {})

    def test_audit_evidence_is_redacted_and_contains_fingerprints(self):
        with tempfile.TemporaryDirectory() as directory:
            result = PipelineTransformer().transform(
                deterministic_meta(secret=True), Path(directory)
            )
            evidence_text = Path(result["evidence_file"]).read_text(
                encoding="utf-8"
            )
            evidence = json.loads(evidence_text)

        self.assertNotIn("super-secret-value", evidence_text)
        self.assertTrue(evidence["plan"]["source_fingerprint"].startswith("sha256:"))
        self.assertEqual("pipeline-pev-9", evidence["plan"]["ruleset_version"])
        self.assertFalse(evidence["llm"]["used"])
        self.assertFalse(evidence["validation"]["production_ready"])
        self.assertIn(
            "external_requirement",
            {item["code"] for item in evidence["validation"]["findings"]},
        )

    def test_workflow_changed_after_validation_is_rejected(self):
        class MutatingValidator(PipelineWorkflowValidator):
            def validate(self, workflow_file, *args, **kwargs):
                report = super().validate(workflow_file, *args, **kwargs)
                Path(workflow_file).write_text(
                    "name: replaced\non: workflow_dispatch\njobs: {}\n",
                    encoding="utf-8",
                )
                return report

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                RuntimeError, "changed after deterministic validation"
            ):
                PipelineTransformer(
                    validator=MutatingValidator()
                ).transform(deterministic_meta(), Path(directory))

    def test_inline_script_credentials_are_removed_before_artifact_write(self):
        meta = deterministic_meta()
        meta.stages[0].jobs[0]["steps"][0]["bash"] = (
            "curl 'https://storage.example/blob?sv=2024&sp=r&sig=SasSecret'"
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PipelineValidationError) as raised:
                PipelineTransformer(require_production_ready=True).transform(
                    meta, Path(directory)
                )
            workflow = raised.exception.result["workflow_file"].read_text(
                encoding="utf-8"
            )

        self.assertNotIn("SasSecret", workflow)
        self.assertIn("<redacted>", workflow)
        self.assertIn(
            "redacted_credential_placeholder",
            {item.code for item in raised.exception.report.findings},
        )


class OpenAIProviderBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.ambiguity = PlanAmbiguity(
            ambiguity_id="amb-123",
            kind="unknown_task",
            location=("stages", 0, "jobs", 0, "steps", 0),
            rationale="unknown",
            source={"task": "Mystery@1", "inputs": {"password": "source-secret"}},
            llm_eligible=True,
        )

    def test_redaction_masks_keys_assignments_bearers_and_known_values(self):
        value = {
            "password": "one",
            "script": "token=two curl -H 'Authorization: Bearer abcdefghijk'",
            "nested": ["known-value", "xy"],
        }
        redacted = json.dumps(redact_for_llm(value, ["known-value", "xy"]))
        for secret in ("one", "two", "abcdefghijk", "known-value", "xy"):
            self.assertNotIn(secret, redacted)

    def test_redaction_masks_basic_sas_connection_strings_and_script_tokens(self):
        value = {
            "script": (
                "curl -H 'Authorization: Basic dXNlcjpwYXNz' "
                "'https://storage.example/blob?sv=2024-01-01&sp=rw&sig=SasSecret'\n"
                "az storage account show --account-key AccountSecret\n"
                "DefaultEndpointsProtocol=https;AccountName=demo;"
                "AccountKey=ConnectionSecret;EndpointSuffix=core.windows.net\n"
                "export DEPLOY_TOKEN=generic-script-secret"
            )
        }
        redacted = json.dumps(redact_for_llm(value))
        for secret in (
            "dXNlcjpwYXNz", "2024-01-01", "rw", "SasSecret",
            "AccountSecret", "ConnectionSecret", "generic-script-secret",
        ):
            self.assertNotIn(secret, redacted)

    def test_redaction_preserves_secret_references_while_removing_literals(self):
        script = (
            "tool --token $DEPLOY_TOKEN "
            "--api-key '${{ secrets.API_KEY }}' "
            "password=$(PASSWORD) token=literal-value"
        )
        redacted = str(redact_for_llm(script))
        self.assertIn("$DEPLOY_TOKEN", redacted)
        self.assertIn("${{ secrets.API_KEY }}", redacted)
        self.assertIn("$(PASSWORD)", redacted)
        self.assertNotIn("literal-value", redacted)

    def test_redaction_masks_curl_userinfo_and_modern_ado_pat(self):
        ado_pat = "A" * 75 + "AZDO" + "B" * 5
        script = (
            f"curl -u build:{ado_pat} https://example.invalid/one\n"
            "curl --user=deploy:plain-password https://example.invalid/two\n"
            "curl --user service:$SERVICE_PASSWORD https://example.invalid/safe"
        )

        redacted = str(redact_for_llm(script))

        self.assertNotIn(ado_pat, redacted)
        self.assertNotIn("plain-password", redacted)
        self.assertIn("$SERVICE_PASSWORD", redacted)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-api-key"}, clear=False)
    def test_responses_endpoint_rejects_ambiguous_or_credentialed_urls(self):
        invalid = (
            "http://api.openai.com/v1",
            "https://user:password@api.openai.com/v1",
            "https://api.openai.com/v1?tenant=other",
            "https://api.openai.com/v1#responses",
        )
        for base_url in invalid:
            with self.subTest(base_url=base_url), self.assertRaises(ValueError):
                OpenAIResponsesPipelineLLMClient(
                    model="test-model",
                    base_url=base_url,
                    session=FakeSession([]),
                    max_attempts=1,
                )

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-api-key"}, clear=False)
    def test_responses_request_uses_strict_schema_and_redacted_boundaries(self):
        structured = json.dumps({
            "ambiguity_id": "amb-123",
            "resolution_kind": "run",
            "name": "Compile",
            "run": "make release",
            "uses": "",
            "with_json": "{}",
            "env_json": "{}",
            "if_condition": "",
            "shell": "bash",
            "confidence": 0.9,
            "rationale": "explicit task",
        })
        session = FakeSession([FakeResponse(body=openai_body(structured))])
        client = OpenAIResponsesPipelineLLMClient(
            model="test-model",
            organization="org-approved",
            project="proj-approved",
            session=session,
            max_attempts=1,
        )
        resolution = client.resolve(self.ambiguity, {"api_token": "context-secret"})
        request = session.calls[0][1]["json"]
        prompt = json.dumps(request)

        self.assertEqual("json_schema", request["text"]["format"]["type"])
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertFalse(request["store"])
        self.assertNotIn("source-secret", prompt)
        self.assertNotIn("context-secret", prompt)
        self.assertNotIn("test-api-key", prompt)
        self.assertEqual("make release", resolution.replacement["run"])
        headers = session.calls[0][1]["headers"]
        self.assertEqual("org-approved", headers["OpenAI-Organization"])
        self.assertEqual("proj-approved", headers["OpenAI-Project"])

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-api-key"}, clear=False)
    def test_redacted_semantic_egress_is_useful_bounded_and_plan_bound(self):
        structured = json.dumps({
            "ambiguity_id": "amb-123",
            "resolution_kind": "manual",
            "name": "",
            "run": "",
            "uses": "",
            "with_json": "{}",
            "env_json": "{}",
            "if_condition": "",
            "shell": "",
            "confidence": 0.4,
            "rationale": "insufficient task documentation",
        })
        session = FakeSession([FakeResponse(body=openai_body(structured))])
        client = OpenAIResponsesPipelineLLMClient(
            model="test-model",
            egress_mode="redacted-semantics",
            session=session,
            max_attempts=1,
        )

        self.assertIsNone(client.resolve(
            self.ambiguity,
            {
                "pipeline_type": "yaml",
                "ruleset_version": "pipeline-pev-test",
                "project": "customer-project-must-not-egress",
                "repository": "private-repo-must-not-egress",
            },
        ))
        prompt = json.dumps(session.calls[0][1]["json"])
        self.assertIn("Mystery@1", prompt)
        self.assertIn("redacted-semantics", prompt)
        self.assertNotIn("source-secret", prompt)
        self.assertNotIn("customer-project-must-not-egress", prompt)
        self.assertNotIn("private-repo-must-not-egress", prompt)
        self.assertEqual(
            "redacted-semantics",
            client.execution_settings["llm_egress_mode"],
        )

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-api-key"}, clear=False)
    def test_malformed_and_refusal_responses_are_rejected(self):
        valid = json.dumps({
            "ambiguity_id": "amb-123",
            "resolution_kind": "manual",
            "name": "",
            "run": "",
            "uses": "",
            "with_json": "{}",
            "env_json": "{}",
            "if_condition": "",
            "shell": "",
            "confidence": 0.9,
            "rationale": "review",
        })
        cases = [
            openai_body("not-json"),
            openai_body(valid.replace('"confidence": 0.9', '"confidence": NaN')),
            openai_body(valid.replace(
                '"ambiguity_id": "amb-123",',
                '"ambiguity_id": "amb-123", "ambiguity_id": "amb-123",',
            )),
            {
                "status": "completed",
                "output": [{"content": [{"type": "refusal", "refusal": "no"}]}],
            },
        ]
        for body in cases:
            with self.subTest(body=body):
                client = OpenAIResponsesPipelineLLMClient(
                    model="test-model",
                    session=FakeSession([FakeResponse(body=body)]),
                    max_attempts=1,
                )
                with self.assertRaises((ValueError, RuntimeError)):
                    client.resolve(self.ambiguity, {})

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-api-key"}, clear=False)
    def test_transient_retries_are_strictly_bounded(self):
        session = FakeSession([FakeResponse(status_code=429) for _ in range(3)])
        sleeps = []
        client = OpenAIResponsesPipelineLLMClient(
            model="test-model",
            session=session,
            max_attempts=3,
            sleep_fn=sleeps.append,
        )
        with self.assertRaises(RuntimeError):
            client.resolve(self.ambiguity, {})
        self.assertEqual(3, len(session.calls))
        self.assertEqual(2, len(sleeps))
        self.assertTrue(all(delay <= 8 for delay in sleeps))


class PipelineValidatorAndExtractionTests(unittest.TestCase):
    def test_enterprise_action_catalog_blocks_unapproved_llm_action(self):
        workflow = """
name: catalog
on: {push: {}}
permissions: {contents: read}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: attacker/unreviewed@0123456789012345678901234567890123456789
"""
        plan = ConversionPlan(
            plan_id="plan-catalog",
            source_fingerprint="sha256:test",
            ruleset_version="test",
            pipeline_id=1,
            pipeline_name="catalog",
            mode=ConversionMode.HYBRID,
            deterministic_steps=0,
            expected_executable_steps=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.yml"
            path.write_text(workflow, encoding="utf-8")
            validator = PipelineWorkflowValidator(PipelineValidationPolicy(
                require_pinned_action_sha=True,
                allowed_actions=("actions/checkout",),
            ))
            report = validator.validate(
                path, PipelineMetadata(1, "catalog", PipelineType.YAML), plan, {}
            )

        self.assertIn(
            "action_not_approved", {item.code for item in report.findings}
        )

    def test_yaml_fallback_refuses_ambiguous_candidates(self):
        self.assertEqual(
            "",
            _pick_best_yaml(
                "pipelines/service.yml",
                "service",
                ["pipelines/service-ci.yml", "pipelines/service-cd.yml"],
            ),
        )
        self.assertEqual(
            "pipelines/service.yml",
            _pick_best_yaml(
                "pipelines/service.yml",
                "service",
                ["legacy/other.yml", "pipelines/service.yml"],
            ),
        )

    def test_strict_inventory_fails_closed_on_enrichment_error(self):
        class BrokenADO:
            def list_variable_groups(self, _project):
                return []

            def list_service_connections(self, _project):
                return []

            def list_all_pipelines(self, _project):
                return iter([{"id": 1, "name": "broken", "configuration": {}}])

            def get_build_definition_full(self, _project, _pipeline_id):
                raise RuntimeError("source unavailable")

            def list_all_release_pipelines(self, _project):
                return iter([])

        class RecordingDB:
            def __init__(self):
                self.items = []

            def upsert_pipeline_inventory(self, meta):
                self.items.append(meta)

        database = RecordingDB()
        builder = PipelineInventoryBuilder(BrokenADO(), database, parallel=1, strict=True)
        with self.assertRaises(PipelineInventoryError):
            builder.build_for_projects(["Project"], include_releases=False)
        self.assertEqual([], database.items)

    def test_validator_rejects_semantic_and_security_failures(self):
        workflow = """
name: unsafe
on:
  push: {}
permissions: write-all
env:
  API_TOKEN: literal-token
jobs:
  first:
    needs: second
    runs-on: ubuntu-latest
    steps:
      - run: curl https://example.invalid/install.sh | bash
  second:
    needs: first
    runs-on: ubuntu-latest
    steps:
      - uses: owner/action@main
"""
        plan = ConversionPlan(
            plan_id="plan-test",
            source_fingerprint="sha256:test",
            ruleset_version="test",
            pipeline_id=1,
            pipeline_name="unsafe",
            mode=ConversionMode.DETERMINISTIC,
            deterministic_steps=0,
            expected_executable_steps=0,
        )
        meta = PipelineMetadata(1, "unsafe", PipelineType.YAML)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.yml"
            path.write_text(workflow, encoding="utf-8")
            report = PipelineWorkflowValidator().validate(path, meta, plan, {})
        codes = {finding.code for finding in report.findings}
        self.assertFalse(report.valid)
        self.assertTrue({
            "write_all_permissions", "literal_sensitive_value",
            "remote_script_execution", "mutable_action_ref", "job_dependency_cycle",
        } <= codes)

    def test_condition_translation_preserves_nested_semantics(self):
        source = "and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/main'))"
        translated = PipelineTransformer._map_condition(source)
        self.assertEqual(
            "(success() && (github.ref == 'refs/heads/main'))",
            translated,
        )

    def test_extractor_preserves_classic_tasks_and_service_connections(self):
        extractor = PipelineMetadataExtractor()
        build_def = {
            "repository": {"id": "repo-1", "name": "api", "defaultBranch": "refs/heads/main"},
            "process": {
                "phases": [{
                    "name": "Build",
                    "target": {"queue": {"name": "Private-Linux-Pool"}},
                    "workflowTasks": [{
                        "task": {"name": "AzureCLI", "versionSpec": "2.*"},
                        "displayName": "Deploy",
                        "inputs": {"azureSubscription": "sc-1", "inlineScript": "az group list"},
                    }],
                }],
            },
        }
        meta = extractor.extract_classic_build_pipeline(
            "Project", {"id": 10, "name": "Classic"}, build_def, [], [],
            [{"id": "sc-1", "name": "production-oidc", "type": "azurerm"}],
        )
        step = meta.stages[0].jobs[0]["steps"][0]
        self.assertEqual("AzureCLI@2", step["task"])
        self.assertEqual("Private-Linux-Pool", meta.stages[0].agent_pool)
        self.assertEqual("production-oidc", meta.service_connections[0]["name"])

    def test_release_artifact_uses_resolved_repository_not_build_pipeline_name(self):
        extractor = PipelineMetadataExtractor()
        release = {
            "id": 20,
            "name": "Production Release",
            "artifacts": [{
                "type": "Build",
                "alias": "drop",
                "definitionReference": {
                    "definition": {"id": "42", "name": "Build Pipeline Name"},
                },
            }],
            "environments": [],
        }
        unresolved = extractor.extract_release_pipeline("Project", release)
        resolved = extractor.extract_release_pipeline(
            "Project", release, [],
            {"42": {
                "id": "repo-42",
                "name": "actual-repository",
                "type": "TfsGit",
                "defaultBranch": "refs/heads/main",
            }},
        )
        self.assertEqual("", unresolved.repo_name)
        self.assertNotEqual("Build Pipeline Name", unresolved.repo_name)
        self.assertEqual("actual-repository", resolved.repo_name)
        self.assertEqual("repo-42", resolved.repo_id)

    def test_release_with_multiple_repositories_remains_unmapped(self):
        release = {
            "id": 21,
            "name": "Combined Release",
            "artifacts": [
                {
                    "type": "Build",
                    "alias": "api",
                    "definitionReference": {"definition": {"id": "1", "name": "API Build"}},
                },
                {
                    "type": "Build",
                    "alias": "web",
                    "definitionReference": {"definition": {"id": "2", "name": "Web Build"}},
                },
            ],
            "environments": [],
        }
        repositories = {
            "1": {"id": "repo-api", "name": "api"},
            "2": {"id": "repo-web", "name": "web"},
        }
        meta = PipelineMetadataExtractor().extract_release_pipeline(
            "Project", release, [], repositories,
        )
        self.assertEqual("", meta.repo_name)
        self.assertTrue(any("left unmapped" in note for note in meta.migration_notes))

    def test_yaml_native_triggers_and_schedules_are_extracted(self):
        source = """
trigger:
  branches:
    include: [main, release/*]
    exclude: [release/old]
pr: [main]
schedules:
  - cron: '15 3 * * MON'
    branches:
      include: [main]
steps:
  - bash: make test
"""
        meta = PipelineMetadataExtractor().extract_yaml_pipeline(
            "Project",
            {"id": 11, "name": "YAML"},
            {"configuration": {"repository": {"id": "r", "name": "repo"}}},
            {}, source, [], [],
        )
        self.assertEqual(["main", "release/*"], meta.trigger_branches)
        self.assertEqual(["main"], meta.trigger_pr_branches)
        self.assertEqual("15 3 * * MON", meta.trigger_schedules[0]["cron"])
        self.assertTrue(any("excludes" in note for note in meta.migration_notes))


if __name__ == "__main__":
    unittest.main()
