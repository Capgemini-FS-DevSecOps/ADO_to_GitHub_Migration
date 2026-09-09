# Review pre-check — judgment-tag verdicts for increments 6–14

**Feature**: 013-clean-code-arch-remediation · **Written**: 2026-09-08
**Inventory git HEAD** (last line of `inventory-history.jsonl`): `463cc352ebdaa17b4220de3235b0080463be4ede`
**Inventory generated_at**: 2026-09-09T03:17:41+00:00
**Deciding agent** (FR-002b): `caveman:cavecrew-reviewer`, 14 batched handoffs using the verbatim instruction from `contracts/artifact-schemas.md:120-123`.

> **Instruction to increment agents**: Apply a verdict only if the row's `state_hash` still matches after your regeneration; otherwise re-run the reviewer for that row.

Scope: every open `name_review`, `bool_flag` and `module_name_review` proposal in `ado2gh/reporting`, `ado2gh/core`, `ado2gh/auth`, `ado2gh/api`, `ado2gh/agents`, `ado2gh/cli`, `services/accelerator_api`, `services/agent`, `apps/migration-ui`. `ado2gh/pipelines` is deliberately excluded — increment 5 is reviewing it.

`module_name_review` rows come from `--pending` (they are not stored in `inventory.json`); their `state_hash` is `sha1(<module id>)` and is therefore stable unless the module is moved or renamed.

Nothing below contains a credential value; CA-003 rows (`ado2gh/auth/`, `ado2gh/api/llm/`, `ado2gh/api/credentials/`, `ado2gh/agents/`) carry names and signatures only.

## Summary

| Package | Proposals | CONFIRM | REJECT | ESCALATE |
|---------|-----------|---------|--------|----------|
| `ado2gh/reporting` | 14 | 1 | 13 | 0 |
| `ado2gh/core` | 45 | 17 | 28 | 0 |
| `ado2gh/auth` | 15 | 0 | 15 | 0 |
| `ado2gh/api` | 157 | 13 | 143 | 1 |
| `ado2gh/agents` | 224 | 15 | 206 | 3 |
| `ado2gh/cli` | 9 | 1 | 8 | 0 |
| `services/accelerator_api` | 77 | 7 | 70 | 0 |
| `services/agent` | 16 | 0 | 16 | 0 |
| `apps/migration-ui` | 11 | 3 | 8 | 0 |
| **total** | **568** | **57** | **507** | **4** |

## ESCALATE — operator decision required

These stay `pending`. Neither the increment agent nor the reviewer may resolve them (FR-002b).

| id | tag | package | state_hash | reviewer rationale |
|----|-----|---------|------------|--------------------|
| `ado2gh/agents/migration_agent/guardrails.py::evaluate_guardrail` | `bool_flag` | `ado2gh/agents` | `dcb98e52a7c65acd0ff0dc3b530170213f15cc22` | The `plan_approved` parameter gates authorization logic and `dry_run` from session controls dry-run vs live execution (CA-001); remediation risks altering safety-critical semantics. |
| `ado2gh/agents/migration_agent/policies.py::can_access_agent_session` | `bool_flag` | `ado2gh/agents` | `2b9758dbcbee9925cc5c3501c157b678635b1ead` | The `write` parameter gates authorization logic for session access; remediation risks changing access control semantics. |
| `ado2gh/agents/migration_agent/policies.py::enforce_live_mode_request` | `bool_flag` | `ado2gh/agents` | `85dc69411fc183ff50263c86bb5d73fb763414d6` | The `dry_run` parameter gates safety checks (CA-001); this controls approval flow and authentication, remediation risks changing access control semantics. |
| `ado2gh/api/migration_work_plan.py::_scope_status` | `bool_flag` | `ado2gh/api` | `e7f3c46f713211357e3c52d7010fe89b9213c368` | `enabled: bool` parameter represents data state (whether scope is enabled) rather than a behavior-mode toggle; function computes status conditional on this boolean being true, but boolean is a data input not a switch. |

Verbatim reviewer lines:

```
ado2gh/agents/migration_agent/guardrails.py::evaluate_guardrail:bool_flag ESCALATE — The `plan_approved` parameter gates authorization logic and `dry_run` from session controls dry-run vs live execution (CA-001); remediation risks altering safety-critical semantics.
ado2gh/agents/migration_agent/policies.py::can_access_agent_session:bool_flag ESCALATE — The `write` parameter gates authorization logic for session access; remediation risks changing access control semantics.
ado2gh/agents/migration_agent/policies.py::enforce_live_mode_request:bool_flag ESCALATE — The `dry_run` parameter gates safety checks (CA-001); this controls approval flow and authentication, remediation risks changing access control semantics.
ado2gh/api/migration_work_plan.py::_scope_status:bool_flag ESCALATE — `enabled: bool` parameter represents data state (whether scope is enabled) rather than a behavior-mode toggle; function computes status conditional on this boolean being true, but boolean is a data input not a switch.
```

## Verdicts by package

### `ado2gh/reporting`

14 proposals (8 function/export, 6 module) — CONFIRM 1, REJECT 13, ESCALATE 0. Reviewer batches: B01.

#### Function / export proposals

| id | tag | state_hash | decision | rationale | protected as | at |
|----|-----|------------|----------|-----------|--------------|----|
| `ado2gh/reporting/pipeline_readiness.py::PipelineReadinessReport._assess_pipeline` | `name_review` | `976f92fd7b045a6612d377da6921bf35ec4703d8` | **REJECT** | False positive; `_assess` is a verb, not a noun. | — | ado2gh/reporting/pipeline_readiness.py:167 |
| `ado2gh/reporting/pipeline_readiness.py::workflow_push_readiness` | `name_review` | `e35adaa6dadc80f4f1bc326cc7843ae2c74b8553` | **REJECT** | Factory function returning computed readiness verdict; noun-based names are appropriate for query/factory functions. | — | ado2gh/reporting/pipeline_readiness.py:21 |
| `ado2gh/reporting/reporter.py::_colour` | `name_review` | `f920fd446fc911e82a66d7b966bc027d54364ebe` | **CONFIRM** | Utility function uses noun form when verb form (`_colorize` or `_apply_color`) would align with Python naming conventions for formatters/transformers. | — | ado2gh/reporting/reporter.py:31 |
| `ado2gh/reporting/reporter.py::_fmt_ts` | `name_review` | `821191c844823aa7da5cf34cf3d171aed8c05d85` | **REJECT** | Idiomatic formatter abbreviation pattern; `_fmt_*` is a recognized utility convention in Python. | — | ado2gh/reporting/reporter.py:275 |
| `ado2gh/reporting/reporter.py::_html_complexity_badge` | `name_review` | `3e8906946460cc85ebdcad8d5afb76d6874a89d2` | **REJECT** | Factory/generator function producing HTML output; noun-based names are appropriate for producers. | — | ado2gh/reporting/reporter.py:307 |
| `ado2gh/reporting/reporter.py::_html_pipeline_rows` | `name_review` | `6f6b639231eb4b9a5f5ef87bd2b6176a2e8d946d` | **REJECT** | Factory/generator function producing HTML table rows; noun-based names are appropriate for producers. | — | ado2gh/reporting/reporter.py:334 |
| `ado2gh/reporting/reporter.py::_html_repo_rows` | `name_review` | `ffb845b847328a800beea3733b1b1f79232f1ea5` | **REJECT** | Factory/generator function producing HTML table rows; noun-based names are appropriate for producers. | — | ado2gh/reporting/reporter.py:314 |
| `ado2gh/reporting/reporter.py::_html_status_badge` | `name_review` | `13c34a33552bf70b957d4cf852aebb9f65a3341f` | **REJECT** | Factory/generator function producing HTML output; noun-based names are appropriate for producers. | — | ado2gh/reporting/reporter.py:296 |

#### Module proposals (`module_name_review`)

| module id | state_hash | decision | rationale |
|-----------|------------|----------|-----------|
| `ado2gh/reporting/` | `e60a7f34638d4ba31ef8ba89dc329996864c4209` | **REJECT** | Module name accurately describes its responsibility as container for reporting, validation, export, and readiness utilities. |
| `ado2gh/reporting/csv_exporter.py` | `32ac5cc7097c09b4d6b26a37a83f458036b98f84` | **REJECT** | Module name accurately describes its responsibility (CSV export functionality). |
| `ado2gh/reporting/pipeline_readiness.py` | `0b84cb459806430ba46bab1d9f58cdb8c8eca55d` | **REJECT** | Module name accurately describes its responsibility (pipeline readiness assessment). |
| `ado2gh/reporting/post_migration_validator.py` | `b739c3d9286a5d29eab1f38b99364b1088ddfe47` | **REJECT** | Module name accurately describes its responsibility (post-migration validation). |
| `ado2gh/reporting/reporter.py` | `1acf59dba69886ab0f1edb589e9af7106375ed94` | **REJECT** | Module name accurately describes its responsibility (console and HTML reporting). |
| `ado2gh/reporting/service_connection_manifest.py` | `122459f6fd5c1a87089ccf7c31e77609038f7c17` | **REJECT** | Module name accurately describes its responsibility (service connection manifest generation). |

### `ado2gh/core`

45 proposals (22 function/export, 23 module) — CONFIRM 17, REJECT 28, ESCALATE 0. Reviewer batches: B02.

#### Function / export proposals

| id | tag | state_hash | decision | rationale | protected as | at |
|----|-----|------------|----------|-----------|--------------|----|
| `ado2gh/core/ado_cleanup.py::ADOCleanup.__init__` | `bool_flag` | `6605ddaaf5a87c100647bb1c2f95a3cf27933f54` | **CONFIRM** | dry_run parameter selects between dry-run logging and live ADO operations. | — | ado2gh/core/ado_cleanup.py:22 |
| `ado2gh/core/ado_cleanup.py::ADOCleanup._cleanup_one` | `bool_flag` | `743162111cdf86d45ba7440cee9a27d900b2c828` | **CONFIRM** | three boolean parameters each branch to execute or skip a cleanup action. | — | ado2gh/core/ado_cleanup.py:74 |
| `ado2gh/core/ado_cleanup.py::ADOCleanup.cleanup_repos` | `bool_flag` | `6e5b7492ed91c00d62d64fd54e86ab9b0527e978` | **CONFIRM** | disable_pipelines, add_redirect, and archive_repo each select whether to execute a specific cleanup action. | — | ado2gh/core/ado_cleanup.py:26 |
| `ado2gh/core/concurrency.py::ConcurrencyManager.git_slot` | `name_review` | `b752f8d5e1fe34a86ec092756b3ac2ce94182648` | **REJECT** | context manager name adequately describes the resource it provides (a git concurrency slot). | — | ado2gh/core/concurrency.py:57 |
| `ado2gh/core/concurrency.py::ConcurrencyManager.pipeline_slot` | `name_review` | `165a5581917d30232280f79bef4229e013c31ed7` | **REJECT** | context manager name adequately describes the resource it provides (a pipeline concurrency slot). | — | ado2gh/core/concurrency.py:65 |
| `ado2gh/core/gei_runtime.py::gei_subprocess_env` | `name_review` | `8eb2e7ee643fc61eb369225d2ac271455d4dc382` | **CONFIRM** | name describes output (environment dict) not action; should be verb form like build_gei_subprocess_env. | — | ado2gh/core/gei_runtime.py:14 |
| `ado2gh/core/migration_engine.py::MigrationEngine.__init__` | `bool_flag` | `353e0baa42887fcf612f4e0364b1457748e831d1` | **CONFIRM** | dry_run parameter branches between dry-run and live migration execution. | — | ado2gh/core/migration_engine.py:22 |
| `ado2gh/core/migration_engine.py::MigrationEngine._in_progress_block_reason` | `name_review` | `ecea0feaeb5109212b31942eb3c5d6118321d30c` | **CONFIRM** | name describes output (reason) not action; should be verb like get_in_progress_block_reason. | — | ado2gh/core/migration_engine.py:45 |
| `ado2gh/core/migration_fr036.py::other_run_holds_repo` | `name_review` | `968d113c3180f0597139ecb48beba1a6e8f42af0` | **REJECT** | boolean predicate name follows is_/has_ pattern and adequately describes the condition it tests. | — | ado2gh/core/migration_fr036.py:45 |
| `ado2gh/core/migration_fr036.py::repo_conflict_reason` | `name_review` | `a1ca03f6722fe79912a8302abc2a4228efbe5058` | **CONFIRM** | name describes output (reason) not action; should be verb like get_repo_conflict_reason. | — | ado2gh/core/migration_fr036.py:12 |
| `ado2gh/core/redis_queue.py::RedisJobQueue.length` | `name_review` | `18016bd2eb7ca2ba3e9143f3f32817eb5af98eda` | **REJECT** | query method with noun name is idiomatic (similar to .length property) for simple getters. | — | ado2gh/core/redis_queue.py:32 |
| `ado2gh/core/rollback.py::RollbackHandler._rollback_branch_protection` | `bool_flag` | `9fa74f4f54a223f519de917853792d9c31551881` | **CONFIRM** | dry_run parameter branches between log-only and live protection removal. | — | ado2gh/core/rollback.py:156 |
| `ado2gh/core/rollback.py::RollbackHandler._rollback_pipelines` | `bool_flag` | `48134868dbde13f6b780eefbbb55bbd543fc2729` | **CONFIRM** | dry_run parameter branches between state-only and live ADO pipeline re-enablement. | — | ado2gh/core/rollback.py:176 |
| `ado2gh/core/rollback.py::RollbackHandler._rollback_repo` | `bool_flag` | `794706635193e6b12da653debc088ecce770e55d` | **CONFIRM** | dry_run parameter branches between log-only and live repository deletion. | — | ado2gh/core/rollback.py:142 |
| `ado2gh/core/rollback.py::RollbackHandler.rollback_repos` | `bool_flag` | `62524c9388c27c054fed010bc1d231ab2617dc75` | **CONFIRM** | dry_run parameter branches between rollback simulation and live execution. | — | ado2gh/core/rollback.py:105 |
| `ado2gh/core/rollback.py::RollbackHandler.rollback_wave` | `bool_flag` | `8880c143d5fcc9f41703aa12d54f4d8a21a620a9` | **CONFIRM** | dry_run parameter branches between rollback simulation and live execution. | — | ado2gh/core/rollback.py:28 |
| `ado2gh/core/scopes/git_scope.py::GitScopeHandler._analyze_feasibility` | `name_review` | `6971c6838475ff0d784845eaca30577e1725b5a0` | **REJECT** | analyze is a verb that clearly describes the action the function performs. | — | ado2gh/core/scopes/git_scope.py:75 |
| `ado2gh/core/scopes/git_scope.py::GitScopeHandler._source_default_branch` | `name_review` | `868cd831db05b14769d5862fef28a0ea51b76619` | **CONFIRM** | name describes output (branch name) not action; should be verb like get_source_default_branch. | — | ado2gh/core/scopes/git_scope.py:336 |
| `ado2gh/core/scopes/git_scope.py::_ado_git_env` | `name_review` | `5be4a9083209176b4a6d75415c513a21c4218575` | **CONFIRM** | name describes output (git environment dict) not action; should be verb form like build_ado_git_env. | — | ado2gh/core/scopes/git_scope.py:60 |
| `ado2gh/core/scopes/pipelines_scope.py::_workflow_branch` | `name_review` | `3bd507b1de8739c6a38f97aaf78016a1b395bd7c` | **CONFIRM** | name describes output (workflow branch string) not action; should be verb like get_workflow_branch. | — | ado2gh/core/scopes/pipelines_scope.py:20 |
| `ado2gh/core/scopes/secrets_scope.py::SecretsScopeHandler._suggest` | `name_review` | `30c3b77b57afd1e0062d736822537ce2cd32642e` | **REJECT** | suggest is a verb that describes the action the function performs. | — | ado2gh/core/scopes/secrets_scope.py:74 |
| `ado2gh/core/wave_runner.py::WaveRunner.run_wave` | `bool_flag` | `6bfa78f851cc28a780f41720b84ebd9538370161` | **CONFIRM** | dry_run parameter branches between simulation and live wave execution. | — | ado2gh/core/wave_runner.py:22 |

#### Module proposals (`module_name_review`)

| module id | state_hash | decision | rationale |
|-----------|------------|----------|-----------|
| `ado2gh/core/` | `39ad74ee957fa6c43559877816086436a8eb326b` | **REJECT** | name describes the package's responsibility as the core migration engine. |
| `ado2gh/core/ado_cleanup.py` | `a93fc2ca8f142ba36fe03ac62deced6a43d9bf3c` | **REJECT** | name clearly describes post-migration ADO cleanup responsibility. |
| `ado2gh/core/concurrency.py` | `1cd8a77bf874771bda1b90b606b5776c5055b668` | **REJECT** | name clearly describes concurrency management responsibility. |
| `ado2gh/core/config_loader.py` | `d75e7bf72248b2addcdb12b1ba6e60d5c902b369` | **REJECT** | name clearly describes configuration loading responsibility. |
| `ado2gh/core/discovery.py` | `392026d3bca8fc42cb7c879b9f3b674b5c2594a8` | **REJECT** | name clearly describes ADO org discovery/scanning responsibility. |
| `ado2gh/core/gei_runtime.py` | `e73c06f3f7d9426693a9cdb324c969de2ad7ba28` | **REJECT** | name clearly describes GEI environment setup responsibility. |
| `ado2gh/core/migration_engine.py` | `fdac9052c3332868508a550317ed34a080229516` | **REJECT** | name clearly describes per-repo migration orchestration responsibility. |
| `ado2gh/core/migration_fr036.py` | `f49e81f1248e4bf69ff75b341b44c8a230c78937` | **CONFIRM** | name references a feature requirement but does not describe the actual responsibility (concurrent-run conflict detection); should be conflict_detection.py or repo_concurrency_guard.py. |
| `ado2gh/core/orchestration/` | `b21ebc3d5c74acb6ffff30c09d6d947895cb09ce` | **REJECT** | name clearly describes batch orchestration responsibility. |
| `ado2gh/core/orchestration/worker.py` | `a105506855728862107ea0303819da8da8d0c116` | **REJECT** | name clearly describes worker task orchestration responsibility. |
| `ado2gh/core/redis_queue.py` | `4b592db3e00e27cbcb847547740e02bccbaf1784` | **REJECT** | name clearly describes Redis-backed job queue responsibility. |
| `ado2gh/core/rollback.py` | `e4be1f89c03d94306d2c19581624474bdc2675a1` | **REJECT** | name clearly describes scope-targeted rollback responsibility. |
| `ado2gh/core/scopes/` | `ae7599306df01be6ef08a3fc9dc2260b8a8af00b` | **REJECT** | name clearly describes the scopes package responsibility. |
| `ado2gh/core/scopes/base.py` | `26de7c842591a18fa9fdc181d4eeb163b01349c2` | **REJECT** | name clearly describes base scope classes responsibility. |
| `ado2gh/core/scopes/branch_policies_scope.py` | `9e5ad53b6e010a86426b55617dba0dcc06673c0f` | **REJECT** | name clearly describes branch policies migration scope responsibility. |
| `ado2gh/core/scopes/git_scope.py` | `9d1b351e7ea68e9397b953347f0a81db5f6dfa8d` | **REJECT** | name clearly describes git/mirror migration scope responsibility. |
| `ado2gh/core/scopes/pipelines_scope.py` | `67fae74e374f244f7dd685ffd9dd2334dbc1fc89` | **REJECT** | name clearly describes workflow/pipeline migration scope responsibility. |
| `ado2gh/core/scopes/registry.py` | `f0239cddfdd1f5ff35ba13c8e7b3530272eff204` | **REJECT** | name clearly describes scope registry responsibility. |
| `ado2gh/core/scopes/secrets_scope.py` | `f44e8b56ab256069bde164270ffcc80163e77f12` | **REJECT** | name clearly describes secrets mapping scope responsibility. |
| `ado2gh/core/scopes/wiki_scope.py` | `522b895f37de6e8722c0f099ba5fc563f14035ef` | **REJECT** | name clearly describes wiki migration scope responsibility. |
| `ado2gh/core/scopes/work_items_scope.py` | `fe5977f909850db788f3e6207a0a805a695c05e8` | **REJECT** | name clearly describes work items migration scope responsibility. |
| `ado2gh/core/sessions.py` | `9e5cd5b3f4e082e38df44061c1eb53bdb5a47720` | **REJECT** | name clearly describes per-thread HTTP session factory responsibility. |
| `ado2gh/core/wave_runner.py` | `f5505b51d6f9b076c28e546eae5972ecc9ed4b45` | **REJECT** | name clearly describes wave-level migration orchestration responsibility. |

### `ado2gh/auth`

15 proposals (11 function/export, 4 module) — CONFIRM 0, REJECT 15, ESCALATE 0. Reviewer batches: B03.

#### Function / export proposals

| id | tag | state_hash | decision | rationale | protected as | at |
|----|-----|------------|----------|-----------|--------------|----|
| `ado2gh/auth/service.py::AuthService.approve_user` | `name_review` | `abd250b3d4226f63125128c1838a2c57e41209a1` | **REJECT** | Imperative action verb clearly describing the user-approval operation. | — | ado2gh/auth/service.py:272 |
| `ado2gh/auth/service.py::AuthService.login` | `name_review` | `bf66011e1241c0e6dbdc915ce3e7a59e42265b01` | **REJECT** | Imperative action verb clearly describing the login operation. | — | ado2gh/auth/service.py:154 |
| `ado2gh/auth/service.py::AuthService.logout` | `name_review` | `a02f64845129eeb1e6adfe0e1c28fb64aaeb7d4c` | **REJECT** | Imperative action verb clearly describing the logout operation. | — | ado2gh/auth/service.py:185 |
| `ado2gh/auth/service.py::AuthService.needs_bootstrap` | `name_review` | `fd694e3ad6a39a5a6f768b9ce63e8deedd1f9fd2` | **REJECT** | Standard predicate pattern (like has_* or needs_*) clearly querying whether bootstrap is required. | — | ado2gh/auth/service.py:99 |
| `ado2gh/auth/service.py::_audit_auth` | `name_review` | `2ecfdd4c39a61dd0aeee42e0fea99ed02f8d05f2` | **REJECT** | Private audit function where "audit" is used as a verb; name clearly describes the auditing operation. | — | ado2gh/auth/service.py:79 |
| `ado2gh/auth/service.py::_db` | `name_review` | `a2f23382db28a352524189dd256987a312cc3e80` | **REJECT** | Private accessor/factory whose name clearly indicates it returns the database connection instance. | — | ado2gh/auth/service.py:25 |
| `ado2gh/auth/service.py::_user_from_row` | `name_review` | `bc941341b79d2d3d3a9615bc1ad2e1e524775e43` | **REJECT** | Standard factory-pattern name clearly describing conversion from database row to PlatformUser object. | — | ado2gh/auth/service.py:29 |
| `ado2gh/auth/service.py::_user_public` | `name_review` | `9aad109f7aa13a1773ac2514d88a8dfcf846e208` | **REJECT** | Name clearly describes transformation from internal row to public user representation. | — | ado2gh/auth/service.py:46 |
| `ado2gh/auth/service.py::_user_status` | `name_review` | `8571a5460f8ef9980a549d068471c0f8b48b9e2a` | **REJECT** | Private extractor whose name clearly describes deriving PlatformUserStatus from a row. | — | ado2gh/auth/service.py:38 |
| `ado2gh/auth/service.py::auth_enabled` | `name_review` | `02001e825d2abbbe8ffeecbb244d23fff09a07c2` | **REJECT** | Predicate query function (like is_* or has_*) whose name clearly describes the state check it performs. | — | ado2gh/auth/service.py:21 |
| `ado2gh/auth/service.py::permissions_for` | `name_review` | `965ebfc4835f200a9bd4d05e59324ac15ce68665` | **REJECT** | Query function whose name clearly describes returning permissions dict for a role. | — | ado2gh/auth/service.py:65 |

#### Module proposals (`module_name_review`)

| module id | state_hash | decision | rationale |
|-----------|------------|----------|-----------|
| `ado2gh/auth/` | `705128befb1b150de89324cc526100ea16932de1` | **REJECT** | Module name "auth" clearly describes its responsibility for platform authentication, sessions, and user management. |
| `ado2gh/auth/models.py` | `79d9eb742956702219bb89de45325c4a0fb70eb3` | **REJECT** | Standard convention name for data model definitions clearly describing enums and dataclasses it contains. |
| `ado2gh/auth/password.py` | `188b68187c920e8c92b37eea1c7c240ced565530` | **REJECT** | Module name clearly describes its responsibility for password hashing and validation utilities. |
| `ado2gh/auth/service.py` | `dce06ea0be29132eb2f99e34bf1df42b1e9b9b80` | **REJECT** | Standard service-layer name clearly describing business-logic orchestration for AuthService. |

### `ado2gh/api`

157 proposals (114 function/export, 43 module) — CONFIRM 13, REJECT 143, ESCALATE 1. Reviewer batches: B04, B05, B06.

#### Function / export proposals

| id | tag | state_hash | decision | rationale | protected as | at |
|----|-----|------------|----------|-----------|--------------|----|
| `ado2gh/api/accelerator.py::Accelerator.inventory` | `name_review` | `12c85b40eee804924310d025e109834972afb162` | **REJECT** | Query method returning inventory data; acceptable name for data accessors. | — | ado2gh/api/accelerator.py:153 |
| `ado2gh/api/accelerator.py::Accelerator.status` | `name_review` | `6951398302ca88d877fdf57988010b827836fe84` | **REJECT** | Query method returning status snapshot; noun-based name is acceptable for return-value descriptors. | — | ado2gh/api/accelerator.py:146 |
| `ado2gh/api/agent_models.py::_provider_label` | `name_review` | `8a96373a8c0d1eb300dfa83dfec00bb5c6520f64` | **REJECT** | Factory/transformer deriving a label string; acceptable helper naming. | — | ado2gh/api/agent_models.py:11 |
| `ado2gh/api/agentic_routes.py::_audit_search_filters` | `name_review` | `c9fa2811618f01ca65471628ec8083671c6df022` | **REJECT** | Constructs filter object from parameters; acceptable factory naming. | — | ado2gh/api/agentic_routes.py:21 |
| `ado2gh/api/agentic_routes.py::_db_path` | `name_review` | `50a74a52b995efb2da9d94aa31370d23758f21f3` | **REJECT** | Configuration getter returning derived database path; acceptable for value accessors. | — | ado2gh/api/agentic_routes.py:17 |
| `ado2gh/api/agentic_routes.py::history_event_types` | `name_review` | `00419d7081d7716c208cf1d9b9c06aa4a9cd3027` | **REJECT** | Framework-fixed signature (FastAPI route handler at line 72). | http_route | ado2gh/api/agentic_routes.py:73 |
| `ado2gh/api/agentic_routes.py::history_sessions` | `name_review` | `a07aa2e86cbf12f72da37f449e6af15e03b6e3be` | **REJECT** | Framework-fixed signature (FastAPI route handler at line 39). | http_route | ado2gh/api/agentic_routes.py:40 |
| `ado2gh/api/connectivity_store.py::_connectivity_sensitive_changed` | `name_review` | `a3ad0a3a2221968ae624b02a25f34fe31cf745ff` | **REJECT** | Predicate function correctly describing what changed, not an imperative verb needed. | orphan_allowlist | ado2gh/api/connectivity_store.py:115 |
| `ado2gh/api/connectivity_store.py::_path` | `name_review` | `54f29a7f47cd791025d1493ac3328d087adb8d95` | **REJECT** | Factory function correctly named as a resource constructor, not an action. | orphan_allowlist | ado2gh/api/connectivity_store.py:12 |
| `ado2gh/api/credentials/cloud_credential_detector.py::_gce_metadata_reachable` | `name_review` | `6ddc7722bdeb537019b6b7399a99772c1ad26998` | **REJECT** | Predicate function checking GCE metadata server reachability; naming convention is consistent and descriptive. | — | ado2gh/api/credentials/cloud_credential_detector.py:31 |
| `ado2gh/api/credentials/cloud_credential_detector.py::_imds_reachable` | `name_review` | `4085416d454576e43333a94a0ad178b1c1ced580` | **REJECT** | Predicate function that checks if IMDS is reachable; naming pattern `_<service>_reachable()` appropriately describes what it tests. | — | ado2gh/api/credentials/cloud_credential_detector.py:18 |
| `ado2gh/api/credentials/cloud_credential_probe.py::_classify_probe_error` | `name_review` | `c8cce2b15bbbb23c213c68a26e37e7edecf6d13d` | **REJECT** | Function name starts with verb "classify"; appropriately describes exception categorization logic. | — | ado2gh/api/credentials/cloud_credential_probe.py:165 |
| `ado2gh/api/credentials/cloud_credentials_store.py::CloudCredentialsStore.approve` | `name_review` | `b8b6856d184236f0065c169d99b03db2f2eb8064` | **REJECT** | Method name is a verb; appropriately describes approval flow including probe execution and timestamp recording. | orphan_allowlist | ado2gh/api/credentials/cloud_credentials_store.py:309 |
| `ado2gh/api/credentials/cloud_credentials_store.py::CloudCredentialsStore.revoke` | `name_review` | `368b6ab9ffbfe9345a87b323ad46710a172788d7` | **REJECT** | Method name is a verb; appropriately describes revocation of approved credentials despite unused actor parameter. | orphan_allowlist | ado2gh/api/credentials/cloud_credentials_store.py:363 |
| `ado2gh/api/credentials/cloud_credentials_store.py::_now_iso` | `name_review` | `b63fc03c6d67379147be5f3709c4d6897e7a1367` | **REJECT** | Factory function returning current time in ISO format; simple getter naming is appropriate. | orphan_allowlist | ado2gh/api/credentials/cloud_credentials_store.py:42 |
| `ado2gh/api/credentials/cloud_credentials_store.py::_path` | `name_review` | `54f29a7f47cd791025d1493ac3328d087adb8d95` | **REJECT** | Factory function returning data file path; factory noun naming is acceptable for path getters. | orphan_allowlist | ado2gh/api/credentials/cloud_credentials_store.py:37 |
| `ado2gh/api/credentials/cloud_credentials_store.py::_snapshot` | `name_review` | `0853ed4da994b754c0314d1dba66cb964955ef66` | **REJECT** | Creates and returns dict snapshot of credential source state; naming describes the action. | orphan_allowlist | ado2gh/api/credentials/cloud_credentials_store.py:46 |
| `ado2gh/api/live_approval_store.py::LiveApprovalStore._context` | `name_review` | `9d4462baed6f050dc76d4c8e22105bc865bbda44` | **REJECT** | Property-like method correctly describing what it returns. | — | ado2gh/api/live_approval_store.py:215 |
| `ado2gh/api/live_approval_store.py::LiveApprovalStore._stamp_pipeline_approval` | `name_review` | `948baabe27938406d17f22e6808ceec9ee83d5dc` | **REJECT** | Verb phrase correctly describing the action. | — | ado2gh/api/live_approval_store.py:299 |
| `ado2gh/api/live_approval_store.py::LiveApprovalStore.approve` | `name_review` | `2d1c42e411fdd83ce231e472473e667659eb714c` | **REJECT** | Verb method correctly named; existing tag is missing_docstring. | — | ado2gh/api/live_approval_store.py:132 |
| `ado2gh/api/live_approval_store.py::_db_path` | `name_review` | `50a74a52b995efb2da9d94aa31370d23758f21f3` | **REJECT** | Factory function correctly named as a resource constructor. | — | ado2gh/api/live_approval_store.py:49 |
| `ado2gh/api/live_approval_store.py::_internal_headers` | `name_review` | `59485e9ad20f24cc5d515b75b345cc22534fe0b4` | **REJECT** | Factory function correctly named as a resource constructor. | — | ado2gh/api/live_approval_store.py:27 |
| `ado2gh/api/live_approval_store.py::_public_row` | `name_review` | `ddf252826984d0c5caa83b46be08a015252c3c22` | **REJECT** | Converter function correctly named for what it returns. | — | ado2gh/api/live_approval_store.py:53 |
| `ado2gh/api/llm/http_llm.py::_proxy_url` | `name_review` | `e955004c7ee61d199abb734c38a68dd47f5803d8` | **REJECT** | Factory function returning proxy URL string; getter naming is appropriate for constructed values. | — | ado2gh/api/llm/http_llm.py:22 |
| `ado2gh/api/llm/http_llm.py::build_llm_http_client` | `bool_flag` | `0236d7769767421fc60604083f84a188d4535d31` | **CONFIRM** | `for_cloud` parameter controls two distinct code paths: returning bare client without proxy/verify vs configured client with proxy and custom CA, making it a behaviour switch. | — | ado2gh/api/llm/http_llm.py:32 |
| `ado2gh/api/llm/llm_model_store.py::_ambient_approved` | `name_review` | `be5c164952f78a97ee90d5c0b20407ffb261993f` | **REJECT** | Predicate checking if ambient credentials are approved; naming convention `_<adjective>()` for predicates is acceptable. | orphan_allowlist | ado2gh/api/llm/llm_model_store.py:117 |
| `ado2gh/api/llm/llm_model_store.py::_path` | `name_review` | `54f29a7f47cd791025d1493ac3328d087adb8d95` | **REJECT** | Factory function returning models file path; consistent with similar path getter pattern. | orphan_allowlist | ado2gh/api/llm/llm_model_store.py:18 |
| `ado2gh/api/llm/llm_model_store.py::_sensitive_fields_changed` | `name_review` | `3ead665ad70fa201aff236b84600e87d745c6b1b` | **REJECT** | Predicate checking if API key, provider, model_id, base_url, or catalog_source changed; name accurately describes the comparison logic. | orphan_allowlist | ado2gh/api/llm/llm_model_store.py:102 |
| `ado2gh/api/llm/llm_provider_registry.py::LLMProviderSpec.runtime_headers` | `name_review` | `3d110015cf8f448aada8017fc1b8795ffb80c005` | **REJECT** | Method returns computed headers dict with provider-specific modifications; noun phrase naming is appropriate for data-returning methods. | — | ado2gh/api/llm/llm_provider_registry.py:40 |
| `ado2gh/api/llm/llm_provider_registry.py::list_provider_specs` | `bool_flag` | `7381904a11fb3e1644dbe12251a89f8c7dca20e2` | **CONFIRM** | `include_internal` parameter controls whether "offline" provider is included in returned list, switching the filter set between `{"offline"}` and empty. | — | ado2gh/api/llm/llm_provider_registry.py:168 |
| `ado2gh/api/llm/model_catalog.py::_catalog_key` | `name_review` | `b5d466a615dc44510ee9ccaa2ad5bf239762d864` | **REJECT** | Creates cache key from provider, api_key, base_url; factory noun naming is standard for key-generation functions. | — | ado2gh/api/llm/model_catalog.py:40 |
| `ado2gh/api/llm/model_catalog.py::_catalog_result` | `name_review` | `b9e3deb3f59c4bf2e76dbbbd51ea6e574db1b32e` | **REJECT** | Dispatches to provider-specific catalog fetchers and returns normalized result; noun phrase naming is appropriate. | — | ado2gh/api/llm/model_catalog.py:243 |
| `ado2gh/api/llm/model_catalog.py::_live_with_preset_fallback` | `name_review` | `5eeff7640325badccc3dda55704ccaab12e8ffaa` | **REJECT** | Function attempts live catalog fetch and falls back to presets; name accurately describes fallback strategy despite untyped fetcher parameter. | — | ado2gh/api/llm/model_catalog.py:163 |
| `ado2gh/api/llm/model_catalog.py::_ollama_catalog` | `name_review` | `457dea61a0356bc5cfc74be026bdf280db867b9e` | **REJECT** | Fetches Ollama catalog with error handling; noun phrase naming is appropriate for catalog-returning functions. | — | ado2gh/api/llm/model_catalog.py:225 |
| `ado2gh/api/llm/model_catalog.py::_ollama_error_payload` | `name_review` | `44258c5e5969f7a968bb621684581aeee55b1928` | **REJECT** | Constructs error payload dict with discovery hints; noun phrase naming describes the returned structure. | — | ado2gh/api/llm/model_catalog.py:213 |
| `ado2gh/api/llm/model_catalog.py::_ollama_headers` | `name_review` | `3670f2dd4fe5c27c954d4219aacdcf79ea3caa8f` | **REJECT** | Factory function returning optional Authorization header dict; getter naming is appropriate. | — | ado2gh/api/llm/model_catalog.py:184 |
| `ado2gh/api/llm/model_catalog.py::_preset_entries` | `name_review` | `cd899818bc5d28f29570ab48d8c363794b7922de` | **REJECT** | Factory function constructing and returning preset catalog entries; noun phrase naming is appropriate. | — | ado2gh/api/llm/model_catalog.py:24 |
| `ado2gh/api/llm/model_validation.py::_anthropic_error_category` | `name_review` | `777d121ee9bd673f33e52ef526d71e8407188e46` | **REJECT** | Extracts error category from Anthropic response; noun phrase describes the parsed value. | — | ado2gh/api/llm/model_validation.py:93 |
| `ado2gh/api/llm/model_validation.py::_anthropic_payload_has_tool_use` | `name_review` | `8d5a5a85793a103642f8860f05df093573dca3d5` | **REJECT** | Predicate checking for tool_use blocks in Anthropic response; `has_` prefix is standard. | — | ado2gh/api/llm/model_validation.py:204 |
| `ado2gh/api/llm/model_validation.py::_classify_error` | `name_review` | `9428c983920ed6406ef923604459b8e36be1d381` | **REJECT** | Classifies exception to category/message tuple; verb "classify" appropriately describes the categorization. | — | ado2gh/api/llm/model_validation.py:108 |
| `ado2gh/api/llm/model_validation.py::_draft_key` | `name_review` | `77afa0369e93c60426e704967f32ac96f5fa8694` | **REJECT** | Creates validation cache key from model config; factory noun naming is appropriate for key generators. | — | ado2gh/api/llm/model_validation.py:82 |
| `ado2gh/api/llm/model_validation.py::_gemini_payload_has_function_call` | `name_review` | `9169b1bd8e03a82d64c1e86a1aaaafe0af4ec4e7` | **REJECT** | Predicate checking for functionCall in Gemini payload; `has_` prefix is standard. | — | ado2gh/api/llm/model_validation.py:211 |
| `ado2gh/api/llm/model_validation.py::_now_iso` | `name_review` | `b63fc03c6d67379147be5f3709c4d6897e7a1367` | **REJECT** | Factory function returning current UTC time as ISO string; simple getter naming is appropriate. | — | ado2gh/api/llm/model_validation.py:60 |
| `ado2gh/api/llm/model_validation.py::_openai_payload_has_tool_calls` | `name_review` | `a1ff980d7a152b66a5d94b153c01e749d9345596` | **REJECT** | Predicate checking for tool_calls in OpenAI response; `has_` prefix is standard for boolean checking functions. | — | ado2gh/api/llm/model_validation.py:194 |
| `ado2gh/api/llm/model_validation.py::_platform_agent_capabilities` | `name_review` | `3f40130c7398b0e342ab7dfae46d627240cba7e0` | **REJECT** | Factory function returning fixed platform agent capabilities dict; noun phrase naming is appropriate. | — | ado2gh/api/llm/model_validation.py:285 |
| `ado2gh/api/llm/model_validation.py::_result` | `name_review` | `5b0b748cec47574dd51c25ae0ae35dcee76b2d45` | **REJECT** | Constructs validation result dict with status, category, message; factory noun naming is standard for dict constructors. | — | ado2gh/api/llm/model_validation.py:64 |
| `ado2gh/api/llm/model_validation.py::_single_flight_validate` | `name_review` | `33f5b75fe2b9f8b946ff0769bcc0dd4fbce895de` | **REJECT** | Implements single-flight validation pattern to prevent concurrent duplicate requests; noun phrase naming appropriately describes the concurrency pattern. | — | ado2gh/api/llm/model_validation.py:601 |
| `ado2gh/api/llm/model_validation.py::_stub_agent_capabilities` | `name_review` | `ef9e9cc84126d988a0b511bef28cd14a55f11eb9` | **REJECT** | Factory function returning fixed stub agent capabilities dict; noun phrase naming is appropriate. | — | ado2gh/api/llm/model_validation.py:277 |
| `ado2gh/api/llm/model_validation.py::_validate_openai_compatible` | `bool_flag` | `13421aebc0fd0e0cd5c47168e613977cdc03f231` | **CONFIRM** | `for_cloud` parameter switches between building HTTP client with proxy/verify settings (True) or bare client (False), controlling request behavior. | — | ado2gh/api/llm/model_validation.py:293 |
| `ado2gh/api/llm/model_validation.py::ssl_error_type` | `name_review` | `a1d5792602a2146182b1030f584f0f4d8541a446` | **REJECT** | Returns ssl.SSLError class type; noun phrase naming is appropriate for type retrievers. | — | ado2gh/api/llm/model_validation.py:141 |
| `ado2gh/api/llm/platform_managed_model.py::platform_model_payload` | `name_review` | `330a4920f9c7e7e6a721c8301a1ee12465d8fb10` | **REJECT** | Factory function returning platform model dict payload or None; noun phrase naming is appropriate. | — | ado2gh/api/llm/platform_managed_model.py:104 |
| `ado2gh/api/llm/platform_managed_model.py::platform_model_status` | `name_review` | `4f6975bd5a0fdb0643b98c40f5742521d2e12878` | **REJECT** | Factory function returning platform model status dict or None; noun phrase naming is appropriate. | — | ado2gh/api/llm/platform_managed_model.py:126 |
| `ado2gh/api/local_hosts.py::ollama_discovery_hint` | `name_review` | `a6914423b736e0239d226971bb64cfd3b99c3510` | **REJECT** | Noun correctly describing what is returned (hint). | — | ado2gh/api/local_hosts.py:33 |
| `ado2gh/api/migration_scan.py::_stub_pipeline` | `name_review` | `f55626e64cd7a905ce640d39b6ec85097dbe67d4` | **REJECT** | "stub" is a verb describing the action of creating a stub object. | — | ado2gh/api/migration_scan.py:91 |
| `ado2gh/api/migration_scan.py::pack_scan_summary_json` | `name_review` | `9f3615b756cb39e583c1d3e47008cf6fb2ed5ab9` | **REJECT** | "pack" is a verb describing the action of transforming scan data. | — | ado2gh/api/migration_scan.py:44 |
| `ado2gh/api/migration_scan.py::persist_scan_results` | `bool_flag` | `5b8a05b46ee324afede8d768197ce3852cf33791` | **CONFIRM** | `preserve_manual_assignments: bool = True` switches between save (line 437) and replace (line 439) database methods. | — | ado2gh/api/migration_scan.py:434 |
| `ado2gh/api/migration_scan.py::scan_with_credentials` | `bool_flag` | `dbd712caa812b1d5225cb7c608d478a7f4fb046f` | **CONFIRM** | `run_inventory: bool = True` controls whether to execute pipeline inventory scan (line 406 branches on this). | — | ado2gh/api/migration_scan.py:400 |
| `ado2gh/api/migration_status_report.py::_pipeline_run_repo_outcomes` | `name_review` | `5d43b96a64145ec1471c6a89fa29dcf53b39dcc1` | **CONFIRM** | All nouns (pipeline_run_repo_outcomes) with no action verb; should be `extract_outcomes_from_pipeline_runs` or similar. | — | ado2gh/api/migration_status_report.py:58 |
| `ado2gh/api/migration_status_report.py::_rollup_repo_status` | `name_review` | `528f701e42b9e29e44b05dba2d8540efbbdeada0` | **REJECT** | "rollup" is a verb describing aggregation of scope statuses. | — | ado2gh/api/migration_status_report.py:40 |
| `ado2gh/api/migration_work_plan.py::_pipeline_blockers_for_repo` | `name_review` | `e3efac4d07ba6b47a53b02dc3b0a853ecea38202` | **REJECT** | Query function describing what it retrieves; acceptable for accessor methods. | — | ado2gh/api/migration_work_plan.py:159 |
| `ado2gh/api/migration_work_plan.py::_scope_status` | `bool_flag` | `e7f3c46f713211357e3c52d7010fe89b9213c368` | **ESCALATE** | `enabled: bool` parameter represents data state (whether scope is enabled) rather than a behavior-mode toggle; function computes status conditional on this boolean being true, but boolean is a data input not a switch. | — | ado2gh/api/migration_work_plan.py:210 |
| `ado2gh/api/migration_work_plan.py::_scope_status` | `name_review` | `e7f3c46f713211357e3c52d7010fe89b9213c368` | **REJECT** | Returns tuple describing scope status; noun-based name acceptable for return-value functions. | — | ado2gh/api/migration_work_plan.py:210 |
| `ado2gh/api/migration_work_plan.py::_secrets_blockers` | `name_review` | `dd91becb8e309fd273cf6ae7e84651c6eadee328` | **REJECT** | Query function describing what it retrieves; acceptable for accessor methods. | — | ado2gh/api/migration_work_plan.py:178 |
| `ado2gh/api/migration_work_plan.py::_work_item_id` | `name_review` | `d3ca83a98adf89b1d7cc1d39a941cf37a5993779` | **REJECT** | Simple ID-builder utility; acceptable naming pattern for derived identifier functions. | — | ado2gh/api/migration_work_plan.py:154 |
| `ado2gh/api/migration_work_plan.py::executable_work_items` | `name_review` | `9b18b6fe96943d67130e908c0e421dcd66bb7e05` | **CONFIRM** | Filters work items by status (lines 414-421); should use action verb like `filter_executable_work_items`. | — | ado2gh/api/migration_work_plan.py:411 |
| `ado2gh/api/migration_work_plan.py::plan_narrative_from_work_items` | `bool_flag` | `fbf880fdc728c735996c815f2005d4d6f955be75` | **CONFIRM** | `dry_run: bool` switches between dry-run (line 449) and live execution modes. | — | ado2gh/api/migration_work_plan.py:440 |
| `ado2gh/api/migration_work_plan.py::work_items_summary` | `name_review` | `5e60706cef0bf7e0923739f725fd35dc25ccd89b` | **REJECT** | Aggregates counts from work items; acceptable name for aggregation functions. | — | ado2gh/api/migration_work_plan.py:296 |
| `ado2gh/api/phase_definitions.py::default_phase_definitions` | `name_review` | `b9bbe37d8f35f743e73cf1aded5ad6fc24e5ae70` | **REJECT** | Factory function correctly named. | — | ado2gh/api/phase_definitions.py:29 |
| `ado2gh/api/phase_definitions.py::phase_rationale` | `name_review` | `ad0b3aa9ab1735026f148b528737ca2595e0ad67` | **REJECT** | Noun correctly describing what is returned (rationale). | — | ado2gh/api/phase_definitions.py:209 |
| `ado2gh/api/phase_definitions.py::phase_risk_bands` | `name_review` | `902e53b91f9b5d68ce63662790416c59689c567e` | **REJECT** | Noun correctly describing what is returned (bands). | — | ado2gh/api/phase_definitions.py:46 |
| `ado2gh/api/phase_definitions.py::slugify_phase_id` | `name_review` | `db7864cabd24ed23d2326ad6ec2778d657391537` | **REJECT** | Verb correctly describing the action. | — | ado2gh/api/phase_definitions.py:179 |
| `ado2gh/api/phase_definitions.py::span_phases_to_scan` | `name_review` | `b639cfb078e3dfbcf82f5ae09dd01386d68779eb` | **REJECT** | Verb phrase correctly describing the action. | — | ado2gh/api/phase_definitions.py:146 |
| `ado2gh/api/pipeline_models.py::PipelineRun.current_step_label` | `name_review` | `a35ca04fc527e6afffe17a44cb9c200114225548` | **REJECT** | Property-like method correctly describing what it returns. | — | ado2gh/api/pipeline_models.py:117 |
| `ado2gh/api/pipeline_models.py::enrich_pipeline_run_dict` | `name_review` | `66211b9545ea1ddb6903816922337e15dc19da22` | **REJECT** | Verb correctly describing the action. | — | ado2gh/api/pipeline_models.py:151 |
| `ado2gh/api/pipeline_runner.py::PipelineRunner._cancelled` | `name_review` | `5c0ff22f92ef95ab6bdad3a1d649a68a3853b8d7` | **REJECT** | Predicate correctly checking a state. | — | ado2gh/api/pipeline_runner.py:61 |
| `ado2gh/api/pipeline_steps.py::PipelineStepsMixin._dep_note` | `name_review` | `014a16c6a785b50ae15850be9c9d1e1d4d58467f` | **REJECT** | Noun correctly describing what is returned (note). | — | ado2gh/api/pipeline_steps.py:376 |
| `ado2gh/api/pipeline_steps.py::PipelineStepsMixin._pipeline_conversion_dry_run_warnings` | `name_review` | `39141ca93863ba93ce65d68a75385c82c152e317` | **REJECT** | Noun correctly describing what is returned (warnings). | — | ado2gh/api/pipeline_steps.py:516 |
| `ado2gh/api/pipeline_steps.py::PipelineStepsMixin._repo_migration_dry_run_warnings` | `name_review` | `33a1c9f1b2e33662185efd2d96b23ff72a1f52cf` | **REJECT** | Noun correctly describing what is returned (warnings). | — | ado2gh/api/pipeline_steps.py:395 |
| `ado2gh/api/pipeline_steps.py::PipelineStepsMixin._sc_note` | `name_review` | `b81937effce6d4316ad0bc1f8d47b1a7e4d4d8dd` | **REJECT** | Noun correctly describing what is returned (note). | — | ado2gh/api/pipeline_steps.py:382 |
| `ado2gh/api/pipeline_steps.py::PipelineStepsMixin._step_analyze_deps` | `name_review` | `5d5059e3d2c87c2ef12d1a972d93dd46c845188c` | **REJECT** | Verb phrase correctly describing step execution. | — | ado2gh/api/pipeline_steps.py:79 |
| `ado2gh/api/pipeline_steps.py::PipelineStepsMixin._step_assign` | `name_review` | `6639dcffec4afe8afc76f62167ff5d5acdd7f83e` | **REJECT** | Verb correctly describing the action. | — | ado2gh/api/pipeline_steps.py:354 |
| `ado2gh/api/pipeline_steps.py::PipelineStepsMixin._step_connect` | `name_review` | `a913fccf756003deee8cf44b711161d73aad2e15` | **REJECT** | Verb phrase correctly describing step execution. | — | ado2gh/api/pipeline_steps.py:15 |
| `ado2gh/api/pipeline_steps.py::PipelineStepsMixin._step_discover` | `name_review` | `0aab6150e0a22ee2d2d85e72bf98bd399a51c42b` | **REJECT** | Verb phrase correctly describing step execution. | — | ado2gh/api/pipeline_steps.py:284 |
| `ado2gh/api/pipeline_steps.py::PipelineStepsMixin._step_inventory` | `name_review` | `752882961cd06b8b896b3fe65c9e9739f1960832` | **REJECT** | Verb phrase correctly describing step execution. | — | ado2gh/api/pipeline_steps.py:295 |
| `ado2gh/api/pipeline_steps.py::PipelineStepsMixin._step_readiness` | `name_review` | `7cabec0e24c5f02794b705cdef30e2a24c576414` | **REJECT** | Noun correctly describing the step outcome. | — | ado2gh/api/pipeline_steps.py:339 |
| `ado2gh/api/pipeline_steps.py::PipelineStepsMixin._step_validate` | `name_review` | `5a31afc0945ddf431418eec6b2d40471dba41355` | **REJECT** | Verb correctly describing step execution. | — | ado2gh/api/pipeline_steps.py:920 |
| `ado2gh/api/pipeline_steps.py::_phase_config_exists` | `name_review` | `59207d622c19622728bc8e9a79947a635dedf956` | **REJECT** | Predicate correctly asking if config exists. | — | ado2gh/api/pipeline_steps.py:1039 |
| `ado2gh/api/pipeline_store.py::PipelineRunStore.create` | `bool_flag` | `c97c377f45e0944cf7f3e626bbc2d88175a1c7a2` | **REJECT** | dry_run is data stored on the object, not a behavior switch within create(). | — | ado2gh/api/pipeline_store.py:86 |
| `ado2gh/api/pipeline_store.py::PipelineRunStore.summary` | `name_review` | `848a23e0fbadb65b41df65f34a05e881f6064a4f` | **REJECT** | Noun correctly describing what is returned (summary). | — | ado2gh/api/pipeline_store.py:65 |
| `ado2gh/api/platform_rbac.py::operator_requires_live_approval` | `bool_flag` | `cd15368399c683b118ab94397cd44a815c1aeefe` | **CONFIRM** | dry_run parameter controls function return value via conditional at line 88. | — | ado2gh/api/platform_rbac.py:77 |
| `ado2gh/api/platform_rbac.py::operator_requires_live_approval` | `name_review` | `cd15368399c683b118ab94397cd44a815c1aeefe` | **REJECT** | Predicate (requires_*) correctly asks if approval is needed. | — | ado2gh/api/platform_rbac.py:77 |
| `ado2gh/api/platform_rbac.py::platform_user` | `name_review` | `920614de2bab8b5085403b8b9767de27222cf3ae` | **REJECT** | Factory function correctly extracting user from request state. | — | ado2gh/api/platform_rbac.py:24 |
| `ado2gh/api/profile_discovery.py::manual_phase_overrides` | `name_review` | `e9ac71d0b888a50ff872162da660cdb4d8097864` | **REJECT** | Noun correctly describing what is returned (overrides). | — | ado2gh/api/profile_discovery.py:67 |
| `ado2gh/api/profile_discovery.py::repo_configs_for_phase` | `name_review` | `324858035f35b82c88555c3546a06d6d6cba8a1b` | **REJECT** | Noun phrase correctly describing what is returned (configs). | — | ado2gh/api/profile_discovery.py:211 |
| `ado2gh/api/profile_discovery.py::risk_score_from_repo_dict` | `name_review` | `038d9bda1bda8e39dddb37fb490541d2b07656bd` | **REJECT** | Factory function (from_*) correctly named for constructor. | — | ado2gh/api/profile_discovery.py:53 |
| `ado2gh/api/profile_governance.py::needs_profile_setup` | `name_review` | `72b2b794061f7ffec98d9c09ad0905e158a096f8` | **REJECT** | Predicate (needs_*) correctly asking if setup required. | — | ado2gh/api/profile_governance.py:32 |
| `ado2gh/api/profile_governance.py::onboarding_status_payload` | `name_review` | `44ce49a6d80ce0b5d7a60a18dbae71d97b6dd236` | **REJECT** | Noun correctly describing what is returned (payload). | — | ado2gh/api/profile_governance.py:105 |
| `ado2gh/api/repo_lock.py::RepoLockManager.acquire` | `name_review` | `56ae0407b62fdbd0ff17fb1eb75be9ab39fc3df9` | **REJECT** | Verb correctly describing the action. | — | ado2gh/api/repo_lock.py:39 |
| `ado2gh/api/repo_lock.py::RepoLockManager.holder` | `name_review` | `0d68c74bf57b1636579ec8864d26b1171477c4f6` | **REJECT** | Noun correctly describing what is returned (holder). | — | ado2gh/api/repo_lock.py:81 |
| `ado2gh/api/run_reporting.py::_scope_detail_message` | `name_review` | `85bfc515ca5ec33a0186ad1789782612605c9265` | **CONFIRM** | Constructs detail message from scope data (lines 107-119); should use verb like `build_scope_detail_message`. | — | ado2gh/api/run_reporting.py:107 |
| `ado2gh/api/run_reporting.py::_stringify` | `name_review` | `17df04ad4828fb7767c59d2fae3120e4e7cb1c30` | **REJECT** | "stringify" is a verb meaning to convert to string representation. | — | ado2gh/api/run_reporting.py:122 |
| `ado2gh/api/run_reporting.py::validation_message` | `name_review` | `bfce3c13d9377b4656a062de687b154f82bfccda` | **CONFIRM** | Builds a formatted message (lines 93-104); should use verb like `build_validation_message`. | — | ado2gh/api/run_reporting.py:93 |
| `ado2gh/api/run_reporting.py::validation_repo_detail` | `name_review` | `593d232120f1c88133bb1b5e66cdc8b5ac900704` | **REJECT** | Normalizes validation row into detail structure; acceptable factory/transformer naming. | — | ado2gh/api/run_reporting.py:57 |
| `ado2gh/api/settings_models.py::_settings_path` | `name_review` | `ca1b69cdf7ab4931ff24e79776055fa4fea190e3` | **REJECT** | Factory function correctly named as resource constructor. | — | ado2gh/api/settings_models.py:13 |
| `ado2gh/api/settings_profiles.py::ProfileMixin.appeal_profile` | `name_review` | `83d28ad8ba3e243486d2500ca62a236b060eb667` | **REJECT** | Verb correctly describing the action. | — | ado2gh/api/settings_profiles.py:249 |
| `ado2gh/api/settings_profiles.py::ProfileMixin.approve_profile` | `name_review` | `15790b0b8e76d17690aa98c6097a0da9363a12d5` | **REJECT** | Verb correctly describing the action. | — | ado2gh/api/settings_profiles.py:203 |
| `ado2gh/api/settings_profiles.py::ProfileMixin.deactivate_profile` | `name_review` | `764516ccc3261717cf4bf8d1950735f9cc99c5b5` | **REJECT** | Verb correctly describing the action. | — | ado2gh/api/settings_profiles.py:160 |
| `ado2gh/api/settings_scan.py::ScanMixin._rescan_profile_after_phase_change` | `name_review` | `1878877d5bb852b35d9b9918c4fdf59cb1327292` | **REJECT** | Verb phrase correctly describing the action. | — | ado2gh/api/settings_scan.py:160 |
| `ado2gh/api/settings_scan.py::ScanMixin.profile_rescan_status` | `name_review` | `2da99e85c1c413a4eea1ee3ff7b9e0a0147133b3` | **REJECT** | Noun correctly describing what is returned (status). | — | ado2gh/api/settings_scan.py:17 |
| `ado2gh/api/settings_store.py::SettingsStore.phases_payload` | `name_review` | `a411494fd44e07e8aebf6dc0b92178113dc69364` | **REJECT** | Noun correctly describing what is returned (payload). | — | ado2gh/api/settings_store.py:319 |
| `ado2gh/api/settings_store.py::SettingsStore.update_phases` | `bool_flag` | `b7cb40352818d75246b68951fafeb9b983d7f299` | **CONFIRM** | span_to_scan controls conditional execution path at line 357. | — | ado2gh/api/settings_store.py:344 |
| `ado2gh/api/step_prerequisites.py::StepPrerequisiteChecker._label` | `name_review` | `30d92113508a7b0fd2b8d4ee0541663043a82b67` | **REJECT** | Noun correctly describing what is returned (label). | — | ado2gh/api/step_prerequisites.py:72 |
| `ado2gh/api/step_prerequisites.py::StepPrerequisiteChecker.failure_message` | `name_review` | `32c60bbe49a63345db56a0bf30d7df960f3a7df1` | **REJECT** | Noun correctly describing what is returned (message). | — | ado2gh/api/step_prerequisites.py:84 |
| `ado2gh/api/validation_run.py::_global_cfg_from_profile` | `name_review` | `c53da10e58ede90c5fe56104f9de942dff531503` | **REJECT** | Derives config from profile; acceptable helper naming (related public function `build_global_cfg` at line 18 has verb). | — | ado2gh/api/validation_run.py:45 |

#### Module proposals (`module_name_review`)

| module id | state_hash | decision | rationale |
|-----------|------------|----------|-----------|
| `ado2gh/api/` | `5f1b6abff9257d2d5ea9c638b3119841a4b7e35e` | **CONFIRM** | "api" is overly generic; directory aggregates multiple responsibilities (SDK, routes, scans, planning, validation) and needs domain-specific naming. |
| `ado2gh/api/accelerator.py` | `474c2720262bef45a947d0e14a1d284fc36a12c0` | **REJECT** | Clearly describes SDK facade responsibility. |
| `ado2gh/api/agent_models.py` | `9c012eb8ec324fd2f59403a07c6a94fd40aad781` | **REJECT** | Describes agent-facing LLM model listings. |
| `ado2gh/api/agentic_routes.py` | `d5f89391afa8d6fc37ce91d2d63bda1975e20750` | **REJECT** | Clearly describes FastAPI agentic subsystem routes. |
| `ado2gh/api/audit_access.py` | `493c4e9997a66cd8501cc2ad32e1bbdb2e08040e` | **REJECT** | Clearly describes audit access control logic. |
| `ado2gh/api/connectivity_store.py` | `12ff6ac54a5ce701206893966a0e0c5bf88b9aff` | **REJECT** | Module name clearly describes responsibility (connectivity profiles). |
| `ado2gh/api/contracts.py` | `f028c3c2fbe74d02031ba23a809d3536006e491b` | **REJECT** | Clearly describes data contracts and schemas. |
| `ado2gh/api/credentials/` | `9cc87701d73d9a21e93edd89200fe84512a66e32` | **REJECT** | Package name "credentials" describes the responsibility of managing ambient cloud credential detection, storage, and validation. |
| `ado2gh/api/credentials/cloud_credential_detector.py` | `e44ff89a33bb5050f39ad836ad5a9bcdb8c25a7a` | **REJECT** | Module name describes detection of ambient cloud credentials without live API calls. |
| `ado2gh/api/credentials/cloud_credential_probe.py` | `ded2fb4202da91c6a859fb96b46ef460363bfde5` | **REJECT** | Module name describes live probing and validation of cloud credentials. |
| `ado2gh/api/credentials/cloud_credentials_store.py` | `d05593f9e499dba7df0f8f0df690ecc58cfa7f0d` | **REJECT** | Module name describes storage and state management of approved cloud credential sources. |
| `ado2gh/api/credentials/credential_validation.py` | `25aef3964a237f43728a5731a192af7c14d95d8d` | **REJECT** | Module name describes validation of platform credentials (GitHub PAT and ADO PAT) for settings UI. |
| `ado2gh/api/errors.py` | `9009518b3df4159f91d8ebd61518846c21845269` | **REJECT** | Clearly describes custom error definitions. |
| `ado2gh/api/live_approval_store.py` | `cf8212948282380944f2c65bb883d72347e5d447` | **REJECT** | Module name clearly describes responsibility (live execution approvals). |
| `ado2gh/api/llm/` | `34c2bf8790af0e688e9951e3009dd33b393443cc` | **REJECT** | Package name "llm" describes management of LLM provider registry, model storage, catalog discovery, and validation. |
| `ado2gh/api/llm/http_llm.py` | `67b4bda65df565a1210ed05233329b3af0ab9e07` | **REJECT** | Module name describes httpx client factory for cloud LLM API calls with proxy and CA configuration. |
| `ado2gh/api/llm/llm_model_store.py` | `f0d2584f9d8c460da745c160b8f0ee0f29b95db5` | **REJECT** | Module name describes storage and state management of LLM model configurations with secret separation. |
| `ado2gh/api/llm/llm_provider_registry.py` | `443c163c5c4ea4b638d45adc1471356d3f8691b0` | **REJECT** | Module name describes registry of supported LLM provider specifications and metadata. |
| `ado2gh/api/llm/model_catalog.py` | `f4c3f4c575dde34b6976643fcbb62601b17e11d1` | **REJECT** | Module name describes live and preset LLM model catalog discovery with provider-specific fetchers. |
| `ado2gh/api/llm/model_validation.py` | `88bf1efc2ab153df06be050dc4170a0fed95c7ba` | **REJECT** | Module name describes LLM model connectivity validation with categorized failure analysis and capability probing. |
| `ado2gh/api/llm/platform_managed_model.py` | `fb8e6381b6b15749a825bd8451744811c8b8056c` | **REJECT** | Module name describes sync of platform-supplied LLM model SKUs into the admin model store. |
| `ado2gh/api/local_hosts.py` | `f5f38eae12d4c8aa58203b19d75c6e36449c69a2` | **REJECT** | Module name describes localhost URL resolution responsibility. |
| `ado2gh/api/migration_scan.py` | `c8078f83aa44760161de0ebd66559b2c9f684ce1` | **REJECT** | Clearly describes ADO org scan and discovery. |
| `ado2gh/api/migration_status_report.py` | `f4271b44466b92742c6367b3dc48b561ec4b775d` | **REJECT** | Clearly describes migration progress reporting. |
| `ado2gh/api/migration_work_plan.py` | `4d88f85b9d87dda73142968a1ed1c8a08aca1fbd` | **REJECT** | Clearly describes work item and plan building. |
| `ado2gh/api/phase_definitions.py` | `1155b8512ce9e23bc6634406057f763874edf585` | **REJECT** | Module name clearly describes responsibility (phase definitions). |
| `ado2gh/api/pipeline_models.py` | `a8ac664d675a0503025599b330c2f53089e6beca` | **REJECT** | Module name clearly describes responsibility (pipeline data models). |
| `ado2gh/api/pipeline_runner.py` | `d422a1fe8f6caa8a3820f3681b3a9d1ab82624d5` | **REJECT** | Module name clearly describes responsibility (pipeline orchestration). |
| `ado2gh/api/pipeline_steps.py` | `3605d55382e00e4fd7c1e2dc500e24ae8cedc413` | **REJECT** | Module name clearly describes responsibility (step implementations). |
| `ado2gh/api/pipeline_store.py` | `fcaa27764d7d650ae8a3c3d8f87c29d114df7807` | **REJECT** | Module name clearly describes responsibility (pipeline run registry). |
| `ado2gh/api/platform_rbac.py` | `64bbba032df853c9ea78c51afb34d436b3b7b45c` | **REJECT** | Module name clearly describes responsibility (RBAC guards). |
| `ado2gh/api/profile_discovery.py` | `df06113469573c50f93b20a14dfb3f8ddbf53fd5` | **REJECT** | Module name clearly describes responsibility (discovery and risk scores). |
| `ado2gh/api/profile_governance.py` | `5020e0352a07b05c8b287f9b59e9ff6ebcb2b810` | **REJECT** | Module name clearly describes responsibility (governance rules). |
| `ado2gh/api/repo_lock.py` | `048243e8268487e0ac4365765a749ac8d87f727e` | **REJECT** | Module name clearly describes responsibility (per-repo locking). |
| `ado2gh/api/run_reporting.py` | `2d705b9acc49419bf2fc89651ad6a76685db9750` | **REJECT** | Clearly describes migration and validation run formatting. |
| `ado2gh/api/scan_diagnostics.py` | `cbd3d02c2d77dde45668acd20f9b200e70e19179` | **REJECT** | Clearly describes scan diagnostic utilities. |
| `ado2gh/api/settings_models.py` | `a566c9445747c5989cf1c01d0c3b241024bbae80` | **REJECT** | Module name clearly describes responsibility (settings data models). |
| `ado2gh/api/settings_profiles.py` | `738ae066ae9a5980351b0cc429d8616166717d47` | **REJECT** | Module name clearly describes responsibility (profile management). |
| `ado2gh/api/settings_scan.py` | `ad5ceebe098f8837095fdf1aee70710c4c751f9b` | **REJECT** | Module name clearly describes responsibility (scan management). |
| `ado2gh/api/settings_store.py` | `c0ce55abb8eaf89494c60101712dcca9e79f2fc8` | **REJECT** | Module name clearly describes responsibility (settings persistence). |
| `ado2gh/api/state_db.py` | `2db4b288afcefbaa04cd52f921fd1c7e15fbf709` | **REJECT** | Clearly describes state database access layer. |
| `ado2gh/api/step_prerequisites.py` | `137d86be567441731591805eeb677f2c66248b65` | **REJECT** | Module name clearly describes responsibility (prerequisite checking). |
| `ado2gh/api/validation_run.py` | `79b91aeb9b8ded641de8f3e949b53fea6228e99d` | **REJECT** | Clearly describes validation run management. |

### `ado2gh/agents`

224 proposals (165 function/export, 59 module) — CONFIRM 15, REJECT 206, ESCALATE 3. Reviewer batches: B07, B08, B09.

#### Function / export proposals

| id | tag | state_hash | decision | rationale | protected as | at |
|----|-----|------------|----------|-----------|--------------|----|
| `ado2gh/agents/metrics.py::MetricsCollector.inc_counter` | `name_review` | `7c44d6a69badc7b8472b2cec22fbb25c6c16fe52` | **REJECT** | `inc_counter` is a verb (increment) accurately describing the operation. | orphan_allowlist | ado2gh/agents/metrics.py:21 |
| `ado2gh/agents/metrics.py::MetricsCollector.observe_histogram` | `name_review` | `79875fc02c843e730a4837ea04fbb9cccb9db680` | **REJECT** | `observe_histogram` is a verb accurately describing what the method does. | orphan_allowlist | ado2gh/agents/metrics.py:29 |
| `ado2gh/agents/migration_agent/graph/builder.py::_needs_human_input` | `name_review` | `8648f224222c556369eda7e21a9cc74c41e209b2` | **REJECT** | `_needs_human_input` is a predicate checking a required condition. | — | ado2gh/agents/migration_agent/graph/builder.py:247 |
| `ado2gh/agents/migration_agent/guardrails.py::_now` | `name_review` | `f2bc771bf1c58964238f64c0fda34c161eb275cc` | **REJECT** | `_now()` clearly returns the current timestamp. | — | ado2gh/agents/migration_agent/guardrails.py:82 |
| `ado2gh/agents/migration_agent/guardrails.py::evaluate_guardrail` | `bool_flag` | `dcb98e52a7c65acd0ff0dc3b530170213f15cc22` | **ESCALATE** | The `plan_approved` parameter gates authorization logic and `dry_run` from session controls dry-run vs live execution (CA-001); remediation risks altering safety-critical semantics. | — | ado2gh/agents/migration_agent/guardrails.py:86 |
| `ado2gh/agents/migration_agent/hitl/blockers.py::_blocker_text` | `name_review` | `c9ebe020bb1fe3cb6f226e7730afe462b4ee91b8` | **REJECT** | private helper extracting blocker text; noun names are correct for property/accessor functions. | — | ado2gh/agents/migration_agent/hitl/blockers.py:10 |
| `ado2gh/agents/migration_agent/hitl/blockers.py::blocker_key` | `name_review` | `53a3c5c88f649c26916279ab96d740b1b311a5c9` | **REJECT** | factory function returning a key identifier; descriptive noun is appropriate. | — | ado2gh/agents/migration_agent/hitl/blockers.py:14 |
| `ado2gh/agents/migration_agent/hitl/blockers.py::needs_blocker_resolution` | `name_review` | `1950fb518a06136c18d251637161ce0749f0e2a9` | **REJECT** | predicate function following `needs_*` pattern acknowledged as correct in guidance. | — | ado2gh/agents/migration_agent/hitl/blockers.py:53 |
| `ado2gh/agents/migration_agent/hitl/blockers.py::outstanding_blockers` | `name_review` | `159d75db67ff307dc3702f41184d5d81989f6189` | **REJECT** | predicate-style selector returning filtered list; descriptive name clearly describes what's returned. | — | ado2gh/agents/migration_agent/hitl/blockers.py:31 |
| `ado2gh/agents/migration_agent/hitl/blockers.py::sanitize_plan_for_operator_view` | `name_review` | `3795d2103485e06cf5ba06be046b29bf7e3a198b` | **REJECT** | first token is verb "sanitize"; no issue. | — | ado2gh/agents/migration_agent/hitl/blockers.py:91 |
| `ado2gh/agents/migration_agent/hitl/form_fields.py::FormFieldOption.normalized` | `name_review` | `0a8ac10ab70c2ea9d1e3a2a10de53d0706ae8334` | **REJECT** | method returning normalized dict; noun name appropriate for property-like methods. | — | ado2gh/agents/migration_agent/hitl/form_fields.py:17 |
| `ado2gh/agents/migration_agent/hitl/form_fields.py::field_dict_from_spec` | `name_review` | `34d3d10987a4bdd894fe8def008e3878709321d6` | **REJECT** | factory function building field dict; descriptive noun is correct pattern. | — | ado2gh/agents/migration_agent/hitl/form_fields.py:49 |
| `ado2gh/agents/migration_agent/hitl/form_fields.py::resolution_options_from_context` | `bool_flag` | `191a11002321f9168bd8ce03e583a0ee1afc030d` | **CONFIRM** | `probe_failures` boolean controls which conditional options are added (lines 201-213); clear behaviour switch. | — | ado2gh/agents/migration_agent/hitl/form_fields.py:191 |
| `ado2gh/agents/migration_agent/hitl/form_fields.py::resolution_options_from_context` | `name_review` | `191a11002321f9168bd8ce03e583a0ee1afc030d` | **REJECT** | factory function building options; descriptive noun is appropriate. | — | ado2gh/agents/migration_agent/hitl/form_fields.py:191 |
| `ado2gh/agents/migration_agent/hitl/forms.py::migration_ready_reply` | `name_review` | `0c38023fad8724c4f7406945be9d25f0ffb7215c` | **REJECT** | factory function building assistant message; descriptive noun is correct. | — | ado2gh/agents/migration_agent/hitl/forms.py:88 |
| `ado2gh/agents/migration_agent/hitl/forms.py::sanitize_form` | `name_review` | `38a1d4f04a981eabd9ffb8be0e8a5fb22719e40d` | **REJECT** | first token is verb "sanitize"; no issue. | — | ado2gh/agents/migration_agent/hitl/forms.py:20 |
| `ado2gh/agents/migration_agent/hitl/forms.py::session_requires_live_approval` | `name_review` | `36c304c4d6d079011b1eb8c86be891a4127f0871` | **REJECT** | predicate function checking approval requirement; descriptive name correctly names what's being checked. | — | ado2gh/agents/migration_agent/hitl/forms.py:123 |
| `ado2gh/agents/migration_agent/hitl/intake.py::analysis_from_state` | `name_review` | `cd6b199cc295aca228afbf615dc00e1d1c9fe236` | **REJECT** | factory function loading analysis; descriptive noun is correct. | — | ado2gh/agents/migration_agent/hitl/intake.py:79 |
| `ado2gh/agents/migration_agent/hitl/intake.py::consult_planner_context` | `bool_flag` | `d6a7e94380eb9f7ea4f48945f920091449e61bff` | **CONFIRM** | `requests_new_migration` boolean controls whether to use remaining repos vs all repos (lines 229-234); behaviour switch. | — | ado2gh/agents/migration_agent/hitl/intake.py:201 |
| `ado2gh/agents/migration_agent/hitl/intake.py::consult_planner_context` | `name_review` | `d6a7e94380eb9f7ea4f48945f920091449e61bff` | **REJECT** | first token is verb "consult"; no issue. | — | ado2gh/agents/migration_agent/hitl/intake.py:201 |
| `ado2gh/agents/migration_agent/hitl/intake.py::intake_field_value` | `name_review` | `eaeba237675aee383a7efcc2f228e40b25079775` | **REJECT** | accessor extracting value; noun is appropriate for property-like functions. | — | ado2gh/agents/migration_agent/hitl/intake.py:114 |
| `ado2gh/agents/migration_agent/hitl/intake.py::intake_from_session` | `name_review` | `89e028a9710287717633b795dc49bd745aaaf021` | **REJECT** | factory function hydrating schema; descriptive noun is correct pattern. | — | ado2gh/agents/migration_agent/hitl/intake.py:19 |
| `ado2gh/agents/migration_agent/hitl/intake.py::intake_ready_for_planner` | `name_review` | `591879c63e72b0b396cc7541c6b44c4949b20b0e` | **REJECT** | predicate function returning bool; name clearly describes the condition being checked. | — | ado2gh/agents/migration_agent/hitl/intake.py:156 |
| `ado2gh/agents/migration_agent/hitl/intake.py::missing_intake_fields` | `name_review` | `df1db98a7e5c48921f0ae3356a01161efba26ebf` | **REJECT** | selector returning required missing fields; predicate-style name is descriptive and correct. | — | ado2gh/agents/migration_agent/hitl/intake.py:129 |
| `ado2gh/agents/migration_agent/hitl/intake_guardrails.py::apply_analysis_guardrails` | `bool_flag` | `86e483b0a5baf9016cd5cf5780aad2a08d696518` | **CONFIRM** | `from_form` boolean controls early return (line 17-18) vs re-validation; clear behaviour switch. | — | ado2gh/agents/migration_agent/hitl/intake_guardrails.py:9 |
| `ado2gh/agents/migration_agent/hitl/intake_guardrails.py::repository_confirmed_for_turn` | `name_review` | `109a0a521be25f5b613fcf3b291aab040afc2166` | **REJECT** | predicate function checking confirmation; descriptive name clearly describes what's validated. | — | ado2gh/agents/migration_agent/hitl/intake_guardrails.py:22 |
| `ado2gh/agents/migration_agent/hitl/intake_llm.py::analyze_operator_message` | `name_review` | `fc95e4aabc23f756c7901106d8a564d0f6f20103` | **REJECT** | first token is verb "analyze"; no issue. | — | ado2gh/agents/migration_agent/hitl/intake_llm.py:94 |
| `ado2gh/agents/migration_agent/hitl/interrupt_node.py::human_input_node` | `name_review` | `4dac635a1c3b2754b24101267dac4d2dbcfdd35e` | **REJECT** | framework-fixed LangGraph node signature `(state) -> dict` protected under HITL interrupt node exception. | graph_node | ado2gh/agents/migration_agent/hitl/interrupt_node.py:12 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::_failure_needs_operator` | `name_review` | `53a70b6117a3c5d69cfcd7af1fd4d7295a9cd6fd` | **REJECT** | private predicate checking escalation need; noun name correct for boolean helpers. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:359 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::_field_specs_from_operator_fields` | `name_review` | `f7b79202c30f6482e341913a2ebba57a5e1ad823` | **REJECT** | private converter factory; descriptive noun appropriate. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:32 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::_github_not_found_error` | `name_review` | `0dfa8bdccdf0af4497f8512d8a58f095ce4102ba` | **REJECT** | private predicate checking error text; noun name appropriate for boolean checkers. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:123 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::_slug` | `name_review` | `79c208b789b7c3c829695c423f8bb4fddb166016` | **REJECT** | private utility that transforms string; noun name correct for value-extracting helpers. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:22 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::assess_operator_input_needed` | `name_review` | `a78646ba367effe7ac7795ba424e01bdc3664e63` | **REJECT** | first token is verb "assess"; no issue. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:596 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::blockers_from_baseline_probes` | `name_review` | `305840511bc5bfc64ed74e662f5cb20a2d32017d` | **REJECT** | factory function converting probes to blockers; descriptive noun. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:155 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::blockers_from_validator_baseline_probes` | `name_review` | `c463b76897a7782077b38ad8da329f79cdc39819` | **REJECT** | factory function extracting validator blockers; descriptive noun. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:232 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::failures_require_operator_escalation` | `name_review` | `31b861197502c1c3f2a045bb0ca71535e1c2d8a4` | **REJECT** | predicate function checking escalation need; descriptive name. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:497 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::fr036_operator_message` | `name_review` | `59a8111a364409dd4e9253c3703a87e146abfe87` | **REJECT** | factory function building operator message; descriptive noun. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:426 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::operator_input_from_blockers` | `name_review` | `96f9d1945e2f97a2c964d9b35bd2bb780523fd17` | **REJECT** | factory function; descriptive noun correct. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:66 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::operator_input_from_probe_failures` | `name_review` | `707d0b111f39ef06f906ef7a2594c4cef4c8fde6` | **REJECT** | factory function building operator request; descriptive noun. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:296 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::operator_input_from_validator_failures` | `name_review` | `aa68becdc1a99b06e4dc008f8709a6fd4177690c` | **REJECT** | factory function building request; descriptive noun. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:521 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::operator_input_to_form` | `name_review` | `1429415df46f8af90659f26b18576355b4f68cc5` | **REJECT** | factory function building form; descriptive noun is correct pattern. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:50 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::pending_operator_input` | `name_review` | `f5b7079af145de0df5742bb78b1b47a915e301cc` | **REJECT** | accessor returning stored request; noun name appropriate for property-like functions. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:632 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::repo_lock_operator_message` | `name_review` | `12a0b62dbc8534785d7145d15d54666478313d61` | **REJECT** | factory function building operator message; descriptive noun. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:473 |
| `ado2gh/agents/migration_agent/hitl/operator_input.py::validation_failures_from_baseline_probes` | `name_review` | `0e310a6b81e71bf844f98dd3d47cd0f0a1f77e56` | **REJECT** | factory function building failures list; descriptive noun. | — | ado2gh/agents/migration_agent/hitl/operator_input.py:200 |
| `ado2gh/agents/migration_agent/hitl/schemas.py::FormIntakeSubmission._coerce_dry_run` | `name_review` | `b01dd48936c69254e4eb00abf85ddea443803ad2` | **REJECT** | first token is verb "_coerce"; no issue. | — | ado2gh/agents/migration_agent/hitl/schemas.py:173 |
| `ado2gh/agents/migration_agent/hitl/schemas.py::MigrationIntakeSchema.resolved_repository_id` | `name_review` | `57f1d5f71463e19fb4ec64d361cbd5cae0874a44` | **REJECT** | Pydantic model method returning resolved ID; descriptive noun appropriate for accessor methods. | — | ado2gh/agents/migration_agent/hitl/schemas.py:63 |
| `ado2gh/agents/migration_agent/hitl/schemas.py::OperatorInputRequest.form_id` | `name_review` | `110fb8e697a6bb1cb2c523584773ec02b0e27492` | **REJECT** | Pydantic model method returning form ID; descriptive noun appropriate for accessor. | — | ado2gh/agents/migration_agent/hitl/schemas.py:284 |
| `ado2gh/agents/migration_agent/hitl/schemas.py::OperatorMessageAnalysis.intake_patch` | `name_review` | `3e8be42540f7acdcc6608e317c91b309717a7457` | **REJECT** | Pydantic model method extracting patch dict; descriptive noun correct. | — | ado2gh/agents/migration_agent/hitl/schemas.py:124 |
| `ado2gh/agents/migration_agent/hitl/schemas.py::required_fields_for_phase` | `name_review` | `244943d7ede2314dbfadc31699e0a444464c1e72` | **REJECT** | selector function returning required fields; descriptive name clearly states purpose. | — | ado2gh/agents/migration_agent/hitl/schemas.py:252 |
| `ado2gh/agents/migration_agent/message_format.py::_executed_scopes_from_executor_result` | `name_review` | `6c48d5730f8a44aafe69ec9d1ab1db3014dbd19a` | **REJECT** | The name clearly describes extracting executed scopes from a result dict. | — | ado2gh/agents/migration_agent/message_format.py:588 |
| `ado2gh/agents/migration_agent/message_format.py::_failure_detail_line` | `name_review` | `4eceb1f32e179eeff83be78ec143d06dfc13dbd2` | **REJECT** | The name accurately describes extracting a failure detail line tuple. | — | ado2gh/agents/migration_agent/message_format.py:123 |
| `ado2gh/agents/migration_agent/message_format.py::orchestrator_chat_already_published` | `name_review` | `69bd53953170c8c24f9d6e8aaed43943cb809cfb` | **REJECT** | This predicate checks whether a reply is already in chat, and the name accurately describes that check. | — | ado2gh/agents/migration_agent/message_format.py:56 |
| `ado2gh/agents/migration_agent/message_format.py::structured_context_to_markdown` | `name_review` | `a037108bee1d5193b120ec03995b391a875a35be` | **REJECT** | This follows the `X_to_Y` conversion pattern and clearly describes the transformation. | — | ado2gh/agents/migration_agent/message_format.py:71 |
| `ado2gh/agents/migration_agent/nodes/_common.py::_executor_result_for_repo_lock` | `bool_flag` | `d11813730f60e18dd096648448adfd8cc6bebd5c` | **REJECT** | `dry_run` is stored in the result dict but doesn't control conditional logic within the function; it's data being passed through. | — | ado2gh/agents/migration_agent/nodes/_common.py:58 |
| `ado2gh/agents/migration_agent/nodes/_common.py::_executor_result_for_repo_lock` | `name_review` | `d11813730f60e18dd096648448adfd8cc6bebd5c` | **REJECT** | appropriate helper name for a builder function returning a dict. | — | ado2gh/agents/migration_agent/nodes/_common.py:58 |
| `ado2gh/agents/migration_agent/nodes/_common.py::_present_operator_input` | `name_review` | `3961761f0b53a474a27ac593d9ef96cc0d10fb05` | **REJECT** | "present" is an action verb. | — | ado2gh/agents/migration_agent/nodes/_common.py:292 |
| `ado2gh/agents/migration_agent/nodes/_common.py::_present_pev_escalation_to_operator` | `name_review` | `8876a1a368c987fb162e9f37c0762ebdb1875041` | **REJECT** | "present" is an action verb. | — | ado2gh/agents/migration_agent/nodes/_common.py:261 |
| `ado2gh/agents/migration_agent/nodes/_common.py::_present_plan_confirmation` | `name_review` | `e3e3acbbcb1dfb044a5cc319265538a1e520d04f` | **REJECT** | "present" is an action verb. | — | ado2gh/agents/migration_agent/nodes/_common.py:315 |
| `ado2gh/agents/migration_agent/nodes/_common.py::_present_validation_failure_to_operator` | `name_review` | `d455060a57b4f1c59f20173c97b7ec8980eb46e5` | **REJECT** | "present" is an action verb. | — | ado2gh/agents/migration_agent/nodes/_common.py:158 |
| `ado2gh/agents/migration_agent/nodes/_common.py::_transition_session` | `name_review` | `bbbbe4675eb90e8d30aeffb293a25068cc983e55` | **REJECT** | "transition" is an action verb. | — | ado2gh/agents/migration_agent/nodes/_common.py:336 |
| `ado2gh/agents/migration_agent/nodes/executor/node.py::_execute_deterministic_repo_scopes` | `bool_flag` | `18fbf2436435c3d2518021f9561ed0d436da1218` | **CONFIRM** | `dry_run` controls rollback record creation on line 92 (`and not dry_run`). | — | ado2gh/agents/migration_agent/nodes/executor/node.py:29 |
| `ado2gh/agents/migration_agent/nodes/executor/node.py::_execute_scope` | `bool_flag` | `4aa0ae55b22748ef77f3dd1b74b6bc0e7bddf850` | **REJECT** | `dry_run` is data passed to another function; no branching logic in this function itself. | — | ado2gh/agents/migration_agent/nodes/executor/node.py:435 |
| `ado2gh/agents/migration_agent/nodes/executor/node.py::executor_node` | `name_review` | `29a98967dd309831119dfb764682b689d0ef69fe` | **REJECT** | framework-fixed LangGraph node signature with appropriate node name. | graph_node | ado2gh/agents/migration_agent/nodes/executor/node.py:103 |
| `ado2gh/agents/migration_agent/nodes/executor/pipeline.py::_current_step_label` | `name_review` | `da1f119e3d9de436ffe2abedda1c94d90e61e572` | **REJECT** | query/getter pattern for extracting step label. | — | ado2gh/agents/migration_agent/nodes/executor/pipeline.py:159 |
| `ado2gh/agents/migration_agent/nodes/executor/pipeline.py::_scope_status_from_step` | `name_review` | `8fd8fe1134a755d77901e04b932b4048bae19e9a` | **REJECT** | converter pattern (noun_from_noun) for mapping step status. | — | ado2gh/agents/migration_agent/nodes/executor/pipeline.py:169 |
| `ado2gh/agents/migration_agent/nodes/executor/pipeline.py::build_executor_result_from_pipeline` | `bool_flag` | `cfd14e91d8ae140f59fae8f58838f74688278fbc` | **REJECT** | `dry_run` is stored as data; function builds same result shape regardless. | — | ado2gh/agents/migration_agent/nodes/executor/pipeline.py:180 |
| `ado2gh/agents/migration_agent/nodes/executor/pipeline.py::ensure_agent_pipeline_run` | `bool_flag` | `74e8070568ca6a6a543d4351820d7dd857c4d216` | **CONFIRM** | `dry_run` controls mode string assignment (line 80) and dict value (line 87). | — | ado2gh/agents/migration_agent/nodes/executor/pipeline.py:57 |
| `ado2gh/agents/migration_agent/nodes/executor/pipeline.py::execute_repo_migration` | `bool_flag` | `58b08e9b3ba014e3e6dfcfbd5f833f33c234e888` | **REJECT** | `dry_run` passed through to another function; no branching in this function. | — | ado2gh/agents/migration_agent/nodes/executor/pipeline.py:261 |
| `ado2gh/agents/migration_agent/nodes/executor/pipeline.py::execute_repo_via_pipeline` | `bool_flag` | `32075be24f7be6bb74be81be4866fca2d29a512f` | **REJECT** | `dry_run` passed through to another function; no branching in this function. | — | ado2gh/agents/migration_agent/nodes/executor/pipeline.py:303 |
| `ado2gh/agents/migration_agent/nodes/executor/plan.py::_repo_display_ids` | `name_review` | `6517fc5f48dd55bc0a617816a202f43bf64770c8` | **REJECT** | query/getter helper for extracting display IDs. | — | ado2gh/agents/migration_agent/nodes/executor/plan.py:154 |
| `ado2gh/agents/migration_agent/nodes/executor/plan.py::agent_pipeline_step_defs` | `name_review` | `95e38d4f3e6052c67ca12a4d79d0a89fb0adf3ba` | **REJECT** | factory noun phrase for returning definitions. | — | ado2gh/agents/migration_agent/nodes/executor/plan.py:38 |
| `ado2gh/agents/migration_agent/nodes/executor/plan.py::pipeline_counts_from_discovery` | `name_review` | `b3f63ff0a4adbe43098f80f2f5c07d17e6839290` | **REJECT** | converter noun phrase (noun_from_noun) pattern. | — | ado2gh/agents/migration_agent/nodes/executor/plan.py:126 |
| `ado2gh/agents/migration_agent/nodes/executor/plan.py::pipeline_narrative_section` | `name_review` | `6e473e44de05eafc08df700c7e958304c1ab02c2` | **REJECT** | factory noun phrase for building a narrative section. | — | ado2gh/agents/migration_agent/nodes/executor/plan.py:143 |
| `ado2gh/agents/migration_agent/nodes/executor/plan.py::repo_config_from_discovery` | `name_review` | `adda1bae9ac70360dc4bc96b789448c4716f9e59` | **REJECT** | converter noun phrase (noun_from_noun) pattern. | — | ado2gh/agents/migration_agent/nodes/executor/plan.py:104 |
| `ado2gh/agents/migration_agent/nodes/executor/plan.py::work_item_scopes` | `name_review` | `29545fdc73cacfce8bbed4591b04f99da7884b93` | **REJECT** | getter pattern for extracting scopes from a work item. | — | ado2gh/agents/migration_agent/nodes/executor/plan.py:291 |
| `ado2gh/agents/migration_agent/nodes/executor/scope.py::_discovery_from_session` | `name_review` | `17d9127c9e22731085755154cdba425799fb6d84` | **REJECT** | getter helper extracting discovery from session. | — | ado2gh/agents/migration_agent/nodes/executor/scope.py:335 |
| `ado2gh/agents/migration_agent/nodes/executor/scope.py::_execute_secrets_scope` | `bool_flag` | `3ea77a443ffeed00c36e9db8e8639fb16801ba16` | **CONFIRM** | `dry_run` controls branching on line 500 (`if not dry_run:`). | — | ado2gh/agents/migration_agent/nodes/executor/scope.py:464 |
| `ado2gh/agents/migration_agent/nodes/executor/scope.py::_project_for_repo_key` | `name_review` | `b9b2906775cbc0429e1f5bf1c08159495ac9e010` | **REJECT** | converter helper (noun_from_noun) pattern. | — | ado2gh/agents/migration_agent/nodes/executor/scope.py:45 |
| `ado2gh/agents/migration_agent/nodes/executor/scope.py::default_agent_enabled_scopes` | `name_review` | `57bc1fcbd1d31476511f7918bbf9cf0ac1b90a77` | **REJECT** | factory returning default scopes list. | — | ado2gh/agents/migration_agent/nodes/executor/scope.py:35 |
| `ado2gh/agents/migration_agent/nodes/executor/scope.py::discovery_repo_lookup` | `name_review` | `100568875da57ebf1e4702230e6eccbce0074891` | **REJECT** | builder creating a lookup table. | — | ado2gh/agents/migration_agent/nodes/executor/scope.py:193 |
| `ado2gh/agents/migration_agent/nodes/executor/scope.py::execute_migration_scope` | `bool_flag` | `f6373fc2eb70d7ec86ef808e8f0d0eb13c98ff5e` | **CONFIRM** | `dry_run` controls status return branching on line 637 (`if dry_run:`). | — | ado2gh/agents/migration_agent/nodes/executor/scope.py:524 |
| `ado2gh/agents/migration_agent/nodes/executor/scope.py::repo_dependency_report` | `name_review` | `0f3b585f7d33b1409db77b91bb90d4bf186593b4` | **REJECT** | builder/reporter pattern for dependency facts. | — | ado2gh/agents/migration_agent/nodes/executor/scope.py:49 |
| `ado2gh/agents/migration_agent/nodes/executor/scope.py::scope_accelerator_endpoint` | `name_review` | `a7b353b872971dea9c2e84b17c72f44ccaee4baf` | **REJECT** | getter pattern mapping scope to endpoint. | — | ado2gh/agents/migration_agent/nodes/executor/scope.py:25 |
| `ado2gh/agents/migration_agent/nodes/intent.py::_begin_new_agent_migration` | `name_review` | `ee5fd27d6d882f0e08b873bdfa0b867bb4710f83` | **REJECT** | "begin" is an action verb. | — | ado2gh/agents/migration_agent/nodes/intent.py:92 |
| `ado2gh/agents/migration_agent/nodes/intent.py::_classify_user_intent` | `name_review` | `d60a04738f6c26d60a8bec86f315239e56894df2` | **REJECT** | "classify" is an action verb. | — | ado2gh/agents/migration_agent/nodes/intent.py:107 |
| `ado2gh/agents/migration_agent/nodes/intent.py::classify_intent_node` | `name_review` | `c80f648117445a38ab1d806f7fff032c8ea147ea` | **REJECT** | "classify" is an action verb. | — | ado2gh/agents/migration_agent/nodes/intent.py:177 |
| `ado2gh/agents/migration_agent/nodes/orchestrator.py::_handle_general_chat` | `bool_flag` | `9e26ee74016f28d585d90322c01d68195bb74694` | **CONFIRM** | `llm_unconfigured` controls branching on line 415 (`if llm_unconfigured or not llm:`). | — | ado2gh/agents/migration_agent/nodes/orchestrator.py:407 |
| `ado2gh/agents/migration_agent/nodes/orchestrator.py::_handle_migration_action` | `bool_flag` | `d4c40549ab8b36d07cccf83e8767e65856272bcb` | **CONFIRM** | `llm_unconfigured` controls branching on line 625 (`if llm and not llm_unconfigured:`). | — | ado2gh/agents/migration_agent/nodes/orchestrator.py:609 |
| `ado2gh/agents/migration_agent/nodes/orchestrator.py::_handle_migration_info` | `bool_flag` | `311776c15cb3708adbf4cd75e44ef2810cd982ba` | **CONFIRM** | `llm_unconfigured` controls branching on line 499 (`if llm and not llm_unconfigured:`). | — | ado2gh/agents/migration_agent/nodes/orchestrator.py:480 |
| `ado2gh/agents/migration_agent/nodes/orchestrator.py::_orchestrator_node_impl` | `name_review` | `f3ce64ec5e412367232829f1c74f744261b5f899` | **REJECT** | implementation helper pattern (_role_node_impl). | — | ado2gh/agents/migration_agent/nodes/orchestrator.py:72 |
| `ado2gh/agents/migration_agent/nodes/orchestrator.py::orchestrator_node` | `name_review` | `9a3688f37485fb6100a3188144be09c8daed96c5` | **REJECT** | framework-fixed LangGraph node signature with appropriate node name. | graph_node | ado2gh/agents/migration_agent/nodes/orchestrator.py:47 |
| `ado2gh/agents/migration_agent/nodes/orchestrator_tools.py::_planner_handoff_state_clear` | `name_review` | `2f98e5159ea9e95a361bf9ebbecfccb7adfd8a67` | **REJECT** | helper builder pattern for clearing state fields. | — | ado2gh/agents/migration_agent/nodes/orchestrator_tools.py:17 |
| `ado2gh/agents/migration_agent/nodes/planner.py::_planner_node_impl` | `name_review` | `597a8ff92cb9d541c6b5bb1309866a1460c0e6c3` | **REJECT** | implementation helper pattern (_role_node_impl). | — | ado2gh/agents/migration_agent/nodes/planner.py:189 |
| `ado2gh/agents/migration_agent/nodes/planner.py::_planner_post_validation_handoff` | `name_review` | `37dcc0f4aafb7a5efc5befd385906455fb7b0c28` | **REJECT** | handler pattern for routing validation outcomes. | — | ado2gh/agents/migration_agent/nodes/planner.py:67 |
| `ado2gh/agents/migration_agent/nodes/planner.py::planner_node` | `name_review` | `83f6b09ed0aaf331953efcd1944caf58bf20d6f4` | **REJECT** | framework-fixed LangGraph node signature with appropriate node name. | graph_node | ado2gh/agents/migration_agent/nodes/planner.py:39 |
| `ado2gh/agents/migration_agent/nodes/planner_plan_builders.py::_advance_migration_queue` | `name_review` | `92d662a0e204e4104f540047a1e5d084ca343915` | **REJECT** | "advance" is an action verb. | — | ado2gh/agents/migration_agent/nodes/planner_plan_builders.py:54 |
| `ado2gh/agents/migration_agent/nodes/planner_research.py::_planner_append_thinking` | `name_review` | `bd1519052273e287ecc396ea31ae82ecb4c28bee` | **REJECT** | "append" is an action verb. | — | ado2gh/agents/migration_agent/nodes/planner_research.py:198 |
| `ado2gh/agents/migration_agent/nodes/planner_research.py::_planner_operator_input_return` | `name_review` | `36e443111e6c187fe91f97b7b94a1874b952be46` | **REJECT** | builder pattern for formatting operator input return. | — | ado2gh/agents/migration_agent/nodes/planner_research.py:216 |
| `ado2gh/agents/migration_agent/nodes/planner_research.py::_planner_text_indicates_blocker` | `name_review` | `d48f4c8c91b9aef28447626ea11739b30b9706a4` | **REJECT** | predicate checker pattern. | — | ado2gh/agents/migration_agent/nodes/planner_research.py:180 |
| `ado2gh/agents/migration_agent/nodes/planner_research.py::_run_planner_research_loop` | `bool_flag` | `9fe1203f65415530ddbcaf0fb96651954ba7828e` | **CONFIRM** | `dry_run` controls branching on line 325 (`if dry_run:`) to select different LLM prompt guidance. | — | ado2gh/agents/migration_agent/nodes/planner_research.py:248 |
| `ado2gh/agents/migration_agent/nodes/validator.py::_all_failures_benign` | `bool_flag` | `8f135af2c2f06c1893c1236d956d63473efa51f0` | **REJECT** | `dry_run` passed through to another predicate; no branching in this function. | — | ado2gh/agents/migration_agent/nodes/validator.py:554 |
| `ado2gh/agents/migration_agent/nodes/validator.py::_all_failures_benign` | `name_review` | `8f135af2c2f06c1893c1236d956d63473efa51f0` | **REJECT** | predicate pattern "all_X_benign". | — | ado2gh/agents/migration_agent/nodes/validator.py:554 |
| `ado2gh/agents/migration_agent/nodes/validator.py::_failure_is_benign` | `bool_flag` | `b22a84671ae6573bb3c7e86505cf365ee963bd7e` | **CONFIRM** | `dry_run` controls branching on line 546 (`if not dry_run: return False`). | — | ado2gh/agents/migration_agent/nodes/validator.py:545 |
| `ado2gh/agents/migration_agent/nodes/validator.py::_failure_is_benign` | `name_review` | `b22a84671ae6573bb3c7e86505cf365ee963bd7e` | **REJECT** | predicate pattern "is_benign". | — | ado2gh/agents/migration_agent/nodes/validator.py:545 |
| `ado2gh/agents/migration_agent/nodes/validator.py::_failure_text` | `name_review` | `4fccac006823834fb4eb544f7bac2333042ab068` | **REJECT** | getter pattern extracting text from failure dict. | — | ado2gh/agents/migration_agent/nodes/validator.py:515 |
| `ado2gh/agents/migration_agent/nodes/validator.py::_scope_result_is_benign` | `bool_flag` | `8f254bb2a6adc400f4fbd80dd3848fdc09001133` | **CONFIRM** | `dry_run` controls branching on line 562 (`if dry_run:`). | — | ado2gh/agents/migration_agent/nodes/validator.py:558 |
| `ado2gh/agents/migration_agent/nodes/validator.py::_scope_result_is_benign` | `name_review` | `8f254bb2a6adc400f4fbd80dd3848fdc09001133` | **REJECT** | predicate pattern "scope_result_is_benign". | — | ado2gh/agents/migration_agent/nodes/validator.py:558 |
| `ado2gh/agents/migration_agent/nodes/validator.py::_validate_scope` | `bool_flag` | `b39177e1cc496db796e85c3c06fdfadd68ef630c` | **CONFIRM** | `dry_run` controls branching on lines 611, 626, and 658. | — | ado2gh/agents/migration_agent/nodes/validator.py:571 |
| `ado2gh/agents/migration_agent/nodes/validator.py::validator_node` | `name_review` | `3ec42b1e1de18dcd9f711f4f8fa0e61343338dd7` | **REJECT** | framework-fixed LangGraph node signature with appropriate node name. | graph_node | ado2gh/agents/migration_agent/nodes/validator.py:32 |
| `ado2gh/agents/migration_agent/nodes/validator_investigation.py::_validator_append_thinking` | `name_review` | `43ae83550fbc55eedd8f13fc4ec199da45f11c67` | **REJECT** | "append" is an action verb. | — | ado2gh/agents/migration_agent/nodes/validator_investigation.py:119 |
| `ado2gh/agents/migration_agent/nodes/validator_investigation.py::_validator_executor_log_summary` | `name_review` | `bbdede584a5f572756128e6e0aab1423961d3a57` | **REJECT** | formatter/builder pattern for executor log summary. | — | ado2gh/agents/migration_agent/nodes/validator_investigation.py:24 |
| `ado2gh/agents/migration_agent/nodes/validator_investigation.py::_validator_executor_metadata` | `name_review` | `d7374c495267a71772fbe776201f75f524be9233` | **REJECT** | formatter/builder pattern for executor metadata. | — | ado2gh/agents/migration_agent/nodes/validator_investigation.py:35 |
| `ado2gh/agents/migration_agent/nodes/validator_investigation.py::_validator_has_pipeline_scope` | `name_review` | `30fe3d22a08c853e74eaf034b374704d161d96be` | **REJECT** | predicate pattern "resource_has_X". | — | ado2gh/agents/migration_agent/nodes/validator_investigation.py:107 |
| `ado2gh/agents/migration_agent/nodes/validator_investigation.py::_validator_text_indicates_blocker` | `name_review` | `69dd687422885572afa73b63c835796a09e4a3f6` | **REJECT** | predicate pattern for text checking. | — | ado2gh/agents/migration_agent/nodes/validator_investigation.py:115 |
| `ado2gh/agents/migration_agent/policies.py::_role_may_execute_live` | `name_review` | `c92f345e61b291bea0045689f58d671151fc4058` | **REJECT** | This is a clear predicate checking role capability for live execution. | — | ado2gh/agents/migration_agent/policies.py:117 |
| `ado2gh/agents/migration_agent/policies.py::can_access_agent_session` | `bool_flag` | `2b9758dbcbee9925cc5c3501c157b678635b1ead` | **ESCALATE** | The `write` parameter gates authorization logic for session access; remediation risks changing access control semantics. | — | ado2gh/agents/migration_agent/policies.py:278 |
| `ado2gh/agents/migration_agent/policies.py::enforce_live_mode_request` | `bool_flag` | `85dc69411fc183ff50263c86bb5d73fb763414d6` | **ESCALATE** | The `dry_run` parameter gates safety checks (CA-001); this controls approval flow and authentication, remediation risks changing access control semantics. | — | ado2gh/agents/migration_agent/policies.py:155 |
| `ado2gh/agents/migration_agent/policies.py::execution_policy_summary` | `name_review` | `1278ffe8dc0e37c142fe89a9bdbab482159941b0` | **REJECT** | The name clearly describes what policy summary is being built. | — | ado2gh/agents/migration_agent/policies.py:239 |
| `ado2gh/agents/migration_agent/policies.py::live_execution_block_message` | `name_review` | `491aa49f0a5ab8d08f4ce9cbf1a909685fe1bae5` | **REJECT** | The name accurately describes building a block message. | — | ado2gh/agents/migration_agent/policies.py:222 |
| `ado2gh/agents/migration_agent/policies.py::scope_refusal_reply` | `name_review` | `67cea3c6a8fc82aea62a5ea4f59d315bc2d8a14d` | **REJECT** | The name accurately describes generating a refusal reply message. | — | ado2gh/agents/migration_agent/policies.py:92 |
| `ado2gh/agents/migration_agent/policies.py::session_owner_username` | `name_review` | `d7da7bba014e268ad7ae6900995751026eb107a4` | **REJECT** | The name clearly describes extracting the owner's username. | — | ado2gh/agents/migration_agent/policies.py:274 |
| `ado2gh/agents/migration_agent/policies.py::session_requires_live_approval` | `name_review` | `4f9cf5fde008c48f2da5e963986cab127382763b` | **REJECT** | This predicate clearly describes whether approval is required. | — | ado2gh/agents/migration_agent/policies.py:143 |
| `ado2gh/agents/migration_agent/route_helpers.py::_accel_get` | `name_review` | `0170b89be07fdb28174d495393c6a7dcd4037451` | **REJECT** | The name clearly describes a GET wrapper operation. | — | ado2gh/agents/migration_agent/route_helpers.py:170 |
| `ado2gh/agents/migration_agent/route_helpers.py::_accel_get_impl` | `name_review` | `c7a186acdd8631d00345a07bf372b861c356e3f3` | **REJECT** | The `_impl` suffix clearly indicates this is the implementation function. | — | ado2gh/agents/migration_agent/route_helpers.py:150 |
| `ado2gh/agents/migration_agent/route_helpers.py::_accel_headers` | `name_review` | `ce8b8befd68732335fc4ac50bcb70921a670e2e9` | **REJECT** | The name clearly describes building accelerator HTTP headers. | — | ado2gh/agents/migration_agent/route_helpers.py:130 |
| `ado2gh/agents/migration_agent/route_helpers.py::_accel_post` | `name_review` | `46ca4501db701b83f8f153c4359c0c6f6981193d` | **REJECT** | The name clearly describes a POST wrapper operation. | — | ado2gh/agents/migration_agent/route_helpers.py:161 |
| `ado2gh/agents/migration_agent/route_helpers.py::_accel_post_impl` | `name_review` | `7684709222998ca65a304ca65704452312f5e693` | **REJECT** | The `_impl` suffix clearly indicates this is the implementation function. | — | ado2gh/agents/migration_agent/route_helpers.py:141 |
| `ado2gh/agents/migration_agent/route_helpers.py::_audit_actor` | `name_review` | `48664abded995ece294697655c9a2c751f515eda` | **REJECT** | The name clearly describes extracting audit actor information. | — | ado2gh/agents/migration_agent/route_helpers.py:185 |
| `ado2gh/agents/migration_agent/route_helpers.py::_enqueue_session_live_approval` | `name_review` | `f1fde107785d59301d7373bdce57a6aff663543b` | **REJECT** | The name clearly describes enqueueing a session for approval. | — | ado2gh/agents/migration_agent/route_helpers.py:508 |
| `ado2gh/agents/migration_agent/route_helpers.py::_platform_user` | `name_review` | `17f3f0e700a1f59bfe3331ecf04b20bd2892ee8f` | **REJECT** | The name clearly describes extracting the platform user. | — | ado2gh/agents/migration_agent/route_helpers.py:179 |
| `ado2gh/agents/migration_agent/route_helpers.py::_profile` | `name_review` | `ca795901dedd327828ab5b5ac6024d2736238eba` | **REJECT** | The name clearly describes retrieving/loading a profile object. | — | ado2gh/agents/migration_agent/route_helpers.py:108 |
| `ado2gh/agents/migration_agent/route_helpers.py::_session_accel_token` | `name_review` | `b37671b6a0d3a18a06d785aa589034f4117f46cd` | **REJECT** | The name clearly describes retrieving an accelerator token for a session. | — | ado2gh/agents/migration_agent/route_helpers.py:122 |
| `ado2gh/agents/migration_agent/route_helpers.py::_session_payload` | `name_review` | `2b3a3b582f8988f3f0afda8f59d8e75e2cf10065` | **REJECT** | The name clearly describes building a session response payload. | — | ado2gh/agents/migration_agent/route_helpers.py:326 |
| `ado2gh/agents/migration_agent/route_helpers.py::_session_token_from_request` | `name_review` | `8ca5a3b4fd53146516847db3a84b649e2a9c5972` | **REJECT** | The name clearly describes extracting the session token from request cookies. | — | ado2gh/agents/migration_agent/route_helpers.py:116 |
| `ado2gh/agents/migration_agent/route_helpers.py::_thinking_log_for_current_turn` | `name_review` | `e4ba66993e1419f39abd5dfa4d2eba27e0343538` | **REJECT** | The name clearly describes extracting thinking logs for the current turn. | — | ado2gh/agents/migration_agent/route_helpers.py:287 |
| `ado2gh/agents/migration_agent/runtime/context_window.py::_token_counter` | `name_review` | `4de81d4a3b164eedff8e14a8224c5ef76c8ed9f9` | **REJECT** | The name clearly describes counting tokens. | — | ado2gh/agents/migration_agent/runtime/context_window.py:15 |
| `ado2gh/agents/migration_agent/runtime/llm_bridge.py::_model_id_implies_thinking` | `name_review` | `a2aa9d405ee32ff605e0226814f71a4a68fda0f4` | **REJECT** | This predicate checks what a model ID implies about thinking capability. | — | ado2gh/agents/migration_agent/runtime/llm_bridge.py:73 |
| `ado2gh/agents/migration_agent/runtime/orchestrator.py::_node_to_subagent` | `name_review` | `64d1ce862f24cf093173f0e6b4258dc29c9aadf3` | **REJECT** | This follows the `X_to_Y` conversion pattern and clearly describes the mapping. | — | ado2gh/agents/migration_agent/runtime/orchestrator.py:583 |
| `ado2gh/agents/migration_agent/runtime/orchestrator.py::_state_has_interrupt` | `name_review` | `8b46021471b85052d5da71d689da3ff3ee38df1c` | **REJECT** | This predicate checks whether a state has an interrupt condition. | — | ado2gh/agents/migration_agent/runtime/orchestrator.py:136 |
| `ado2gh/agents/migration_agent/runtime/orchestrator.py::continue_session_graph` | `name_review` | `4fffc5bfe29a65538c0dba707e0e355f7d5fb9ec` | **REJECT** | `continue` is a verb clearly describing the continuation operation. | — | ado2gh/agents/migration_agent/runtime/orchestrator.py:227 |
| `ado2gh/agents/migration_agent/runtime/orchestrator.py::continue_session_graph_stream` | `name_review` | `3aea3786337003f6203c987472beb24f19823e1c` | **REJECT** | `continue` is a verb clearly describing the streaming continuation operation. | — | ado2gh/agents/migration_agent/runtime/orchestrator.py:545 |
| `ado2gh/agents/migration_agent/runtime/tracing.py::graph_run_config` | `name_review` | `4ad46b0c30286bbf1bac8df6fc1cf27134b158e1` | **REJECT** | The name clearly describes building graph run configuration. | — | ado2gh/agents/migration_agent/runtime/tracing.py:26 |
| `ado2gh/agents/migration_agent/session/lifecycle.py::_now` | `name_review` | `f2bc771bf1c58964238f64c0fda34c161eb275cc` | **REJECT** | The name clearly returns the current timestamp. | — | ado2gh/agents/migration_agent/session/lifecycle.py:10 |
| `ado2gh/agents/migration_agent/session/lifecycle.py::_repo_parts` | `name_review` | `7c3401b2f2faee023aa161e10c315c69a4481101` | **REJECT** | The name clearly describes extracting project and repo parts from an ID. | — | ado2gh/agents/migration_agent/session/lifecycle.py:90 |
| `ado2gh/agents/migration_agent/session/lifecycle.py::new_isolated_agent_session` | `bool_flag` | `2cb8e62192d8afc149290ff0d4f9d655e4bd29a1` | **REJECT** | The boolean parameters (`llm_degraded`, `llm_unconfigured`, `dry_run`) are DATA values stored on the session, not behavior switches controlling internal flow. | — | ado2gh/agents/migration_agent/session/lifecycle.py:14 |
| `ado2gh/agents/migration_agent/session/lifecycle.py::new_isolated_agent_session` | `name_review` | `2cb8e62192d8afc149290ff0d4f9d655e4bd29a1` | **REJECT** | `new` is a verb clearly describing factory creation. | — | ado2gh/agents/migration_agent/session/lifecycle.py:14 |
| `ado2gh/agents/migration_agent/session/state.py::SessionStateMachine.transition` | `name_review` | `7182c698ae4c35ca458cb6db4170eba42acfff06` | **REJECT** | `transition` is a verb clearly describing the state transition operation. | — | ado2gh/agents/migration_agent/session/state.py:81 |
| `ado2gh/agents/migration_agent/session/state.py::maybe_reset_for_migration_request` | `bool_flag` | `1e80c4a04a21850ee6d38ed3946a01a797ed405b` | **CONFIRM** | The `force` parameter gates whether reset is unconditional (force=True) or conditional; this is a genuine behavior switch. | — | ado2gh/agents/migration_agent/session/state.py:151 |
| `ado2gh/agents/migration_agent/session/state.py::maybe_reset_for_migration_request` | `name_review` | `1e80c4a04a21850ee6d38ed3946a01a797ed405b` | **REJECT** | The `maybe_` prefix clearly indicates a predicate that may perform an action. | — | ado2gh/agents/migration_agent/session/state.py:151 |
| `ado2gh/agents/migration_agent/session/store.py::MigrationSessionStore.acquire_repo_lock` | `name_review` | `6055ae81e958380b160b7b3be7a74a61cce14113` | **REJECT** | `acquire` is a verb clearly describing the lock acquisition operation. | — | ado2gh/agents/migration_agent/session/store.py:500 |
| `ado2gh/agents/migration_agent/session/store.py::MigrationSessionStore.create_session` | `bool_flag` | `ca2472ccb7228384febdcd7cad76e4d22af22bbb` | **REJECT** | The `dry_run` parameter is DATA stored on the session record, not a behavior switch controlling internal function flow. | — | ado2gh/agents/migration_agent/session/store.py:196 |
| `ado2gh/agents/migration_agent/session/store.py::MigrationSessionStore.repo_lock_holder` | `name_review` | `dc190836028f6b12ebcc1f32d8f7117c43ef6089` | **REJECT** | The name clearly describes retrieving who holds an active lock. | — | ado2gh/agents/migration_agent/session/store.py:516 |
| `ado2gh/agents/migration_agent/session/store.py::_messages_json` | `name_review` | `0fec83d8396b090ea7da812b4e2efa961d76730f` | **REJECT** | The name clearly describes serializing messages to JSON. | — | ado2gh/agents/migration_agent/session/store.py:22 |
| `ado2gh/agents/migration_agent/session/store.py::_now` | `name_review` | `f2bc771bf1c58964238f64c0fda34c161eb275cc` | **REJECT** | The name clearly returns the current timestamp. | — | ado2gh/agents/migration_agent/session/store.py:14 |
| `ado2gh/agents/migration_agent/session/store.py::_uuid` | `name_review` | `7c08e10dfa75f096acb53a653fd160e24afcae2f` | **REJECT** | The name clearly generates a UUID. | — | ado2gh/agents/migration_agent/session/store.py:18 |
| `ado2gh/agents/migration_agent/tools/orchestrator_tools.py::get_orchestrator_tools` | `bool_flag` | `6348d184f303bccdb7a9104e41474ce7162ee970` | **REJECT** | This function has no boolean parameters controlling behavior; the parameters are optional callables and strings. | langchain_tool | ado2gh/agents/migration_agent/tools/orchestrator_tools.py:129 |
| `ado2gh/agents/migration_agent/utils.py::_now` | `name_review` | `f2bc771bf1c58964238f64c0fda34c161eb275cc` | **REJECT** | The name clearly returns the current timestamp. | — | ado2gh/agents/migration_agent/utils.py:15 |
| `ado2gh/agents/migration_agent/utils.py::_safe_reply` | `name_review` | `8c7a078e4b6139862416ba6debaaebf2bdab5d4d` | **REJECT** | The name clearly describes extracting a safe reply. | — | ado2gh/agents/migration_agent/utils.py:326 |
| `ado2gh/agents/migration_agent/utils.py::_tool_status_done` | `name_review` | `02673c6f0bb4c16f17911f995591d5b3f4790bb6` | **REJECT** | The name clearly describes generating a completion status message. | — | ado2gh/agents/migration_agent/utils.py:183 |
| `ado2gh/agents/migration_agent/utils.py::canonical_plan_repo_key` | `name_review` | `e52b9805ada31fdeacb7129bc249fb8c6579e602` | **REJECT** | The name clearly describes normalizing a repo key for comparison. | — | ado2gh/agents/migration_agent/utils.py:426 |
| `ado2gh/agents/migration_agent/utils.py::canonical_repo_id` | `name_review` | `f9a2448a3fda45f22410a744ce26692fc8a58baf` | **REJECT** | The name clearly describes returning a canonical repo identifier. | — | ado2gh/agents/migration_agent/utils.py:415 |
| `ado2gh/agents/migration_agent/utils.py::discovery_repo_names` | `name_review` | `3b04c4707dea5c69315eed3ca5b56ee5352c65f4` | **REJECT** | The name clearly describes collecting repo names from discovery. | — | ado2gh/agents/migration_agent/utils.py:556 |
| `ado2gh/agents/migration_agent/utils.py::repo_key_aliases` | `name_review` | `328bc0c5b418b12cb35d19cce40c85ff6a5a52ec` | **REJECT** | The name clearly describes returning repo key aliases. | — | ado2gh/agents/migration_agent/utils.py:442 |
| `ado2gh/agents/migration_agent/utils.py::repo_matches_plan_keys` | `name_review` | `de5a751dd45d816fd6194764c145fa2c2dc5b38f` | **REJECT** | This predicate clearly describes checking repo key matches. | — | ado2gh/agents/migration_agent/utils.py:484 |
| `ado2gh/agents/migration_agent/utils.py::repo_not_found_clarification` | `name_review` | `3cc363e0ebf24284a576efcd614894750a3462c2` | **REJECT** | The name clearly describes building a clarification payload. | — | ado2gh/agents/migration_agent/utils.py:618 |

#### Module proposals (`module_name_review`)

| module id | state_hash | decision | rationale |
|-----------|------------|----------|-----------|
| `ado2gh/agents/` | `6399cc89481ca0d4b27c40972100e314d7456d8f` | **REJECT** | `agents` accurately describes the folder containing agent implementations. |
| `ado2gh/agents/metrics.py` | `a76f262a34ff723d4420f74863b9b385e5685ce4` | **REJECT** | `metrics` accurately describes the metrics collection module. |
| `ado2gh/agents/migration_agent/` | `7b6259cae8e388ae80e4d040cae1cbfc38a65ecd` | **REJECT** | `migration_agent` accurately describes the folder containing the migration agent implementation. |
| `ado2gh/agents/migration_agent/agent.py` | `d295c3d72ae218522b2b034b364a75048f6b715a` | **REJECT** | `agent` accurately describes the main agent entry point module. |
| `ado2gh/agents/migration_agent/constants.py` | `203f76f1dd5fecb211ca2fbca0cc37e06b70112f` | **REJECT** | `constants` accurately describes the constants definition module. |
| `ado2gh/agents/migration_agent/graph/` | `92a982a4f8c3a001915eb18c41aec6a82950684f` | **REJECT** | `graph` accurately describes the folder containing LangGraph definitions. |
| `ado2gh/agents/migration_agent/graph/builder.py` | `7dfca810fb759c5d0417be904caccb722b7d4d1e` | **REJECT** | `builder` accurately describes the graph builder module. |
| `ado2gh/agents/migration_agent/graph/state.py` | `58bbb8a40d5e804ff4b42915de1a8178e3d8abfb` | **REJECT** | `state` accurately describes the agent state schema module. |
| `ado2gh/agents/migration_agent/guardrails.py` | `1b59ac4eb6157f0d5e5d1eb2c998882109d183df` | **REJECT** | `guardrails` accurately describes tool execution guardrail validation. |
| `ado2gh/agents/migration_agent/hitl/` | `a0166dc27a99489efda2c2215c6d590c311fad61` | **REJECT** | "hitl" clearly denotes human-in-the-loop subsystem responsibility. |
| `ado2gh/agents/migration_agent/hitl/blockers.py` | `0be731296de3155ed1b7c2a7f2195eb2103919bc` | **REJECT** | module name clearly describes blocked work-item tracking responsibility. |
| `ado2gh/agents/migration_agent/hitl/form_fields.py` | `4c9fcb0c56bd2e2a627febb380cf48dafddf275d` | **REJECT** | module name clearly describes form field models and enrichment responsibility. |
| `ado2gh/agents/migration_agent/hitl/forms.py` | `4581ed71b13a29ec4f974ea72ce635776fdc2675` | **REJECT** | module name clearly describes dynamic form builders responsibility. |
| `ado2gh/agents/migration_agent/hitl/intake.py` | `b9cdd1367e80c4455a0745f2949c1d5aa26aa3fb` | **REJECT** | module name clearly describes intake routing and schema handling responsibility. |
| `ado2gh/agents/migration_agent/hitl/intake_guardrails.py` | `42225726ed363df78870baa7c17be123628448ed` | **REJECT** | module name clearly describes operator message analysis guardrails responsibility. |
| `ado2gh/agents/migration_agent/hitl/intake_llm.py` | `321fe3fca1442ed0b9551cf2067e1055eb126e68` | **REJECT** | module name clearly describes LLM-driven message analysis responsibility. |
| `ado2gh/agents/migration_agent/hitl/interrupt_node.py` | `5bf13120c3b582f49f66636895ec0f6b554ed83b` | **REJECT** | module name clearly describes LangGraph interrupt node responsibility. |
| `ado2gh/agents/migration_agent/hitl/operator_input.py` | `9979b60945e120758c8d8c94afeecb8ac3bb426a` | **REJECT** | module name clearly describes operator input requests responsibility. |
| `ado2gh/agents/migration_agent/hitl/schemas.py` | `9272dba3f1eb4be66dae87ae7b1406e4dae81288` | **REJECT** | module name clearly describes Pydantic schema definitions responsibility. |
| `ado2gh/agents/migration_agent/message_format.py` | `d84c9c956b24dff884dd7b66a777230d45d0de18` | **REJECT** | `message_format` accurately describes message formatting utilities. |
| `ado2gh/agents/migration_agent/nodes/` | `f926e292c5976291651af5ab39fdf2b71e6314c3` | **REJECT** | folder name describes its purpose (contains agent nodes). |
| `ado2gh/agents/migration_agent/nodes/_common.py` | `d15ee09a1a448ce754917644a60cbb780b31fbb7` | **REJECT** | module name describes its content (common helpers). |
| `ado2gh/agents/migration_agent/nodes/executor/` | `a3a458dd2ba597b499cb522de445e20b9e32202a` | **REJECT** | folder name describes its purpose (executor-related modules). |
| `ado2gh/agents/migration_agent/nodes/executor/node.py` | `e0d9d670c228dced720f3bb9b473be4a1f2a42b3` | **REJECT** | module name describes its content (executor node). |
| `ado2gh/agents/migration_agent/nodes/executor/pipeline.py` | `567353383bfb3c239c88a6449e4676ebc9373485` | **REJECT** | module name describes its content (pipeline execution). |
| `ado2gh/agents/migration_agent/nodes/executor/plan.py` | `242a75cbbfe4bffa5993b5b556fb0c318c071f92` | **REJECT** | module name describes its content (plan construction). |
| `ado2gh/agents/migration_agent/nodes/executor/scope.py` | `4a20d15afbf268a3d2c2e2fc7dcf39b276b2282a` | **REJECT** | module name describes its content (scope execution). |
| `ado2gh/agents/migration_agent/nodes/finalize.py` | `19101de9ad97092ae7bce4bbd66ebc61c1b70ec2` | **REJECT** | module name describes its purpose (finalization logic). |
| `ado2gh/agents/migration_agent/nodes/intent.py` | `aa8d1ddd2c30fb7afeaf4abfb7981b35f558e132` | **REJECT** | module name describes its content (intent classification). |
| `ado2gh/agents/migration_agent/nodes/messaging.py` | `4ccc435544a6d784789eaa1b65cc730fea716dd5` | **REJECT** | module name describes its content (messaging utilities). |
| `ado2gh/agents/migration_agent/nodes/orchestrator.py` | `93ad6923d5858b7e973d2e6135fe157f9d70e304` | **REJECT** | module name describes its content (orchestrator node). |
| `ado2gh/agents/migration_agent/nodes/orchestrator_tools.py` | `2e2c95550c3cabbda7ab1abcc5b564dfc60fa005` | **REJECT** | module name describes its content (orchestrator tools). |
| `ado2gh/agents/migration_agent/nodes/planner.py` | `f0b13d29fb007a721ff1e26608f45508828fcad3` | **REJECT** | module name describes its content (planner node). |
| `ado2gh/agents/migration_agent/nodes/planner_plan_builders.py` | `9edd411bca16dfd588d716252eecdd3c3119eb77` | **REJECT** | module name describes its content (plan building helpers). |
| `ado2gh/agents/migration_agent/nodes/planner_research.py` | `5bfd2cc167f1d6c675db802d0ac7fc6e59638bd9` | **REJECT** | module name describes its content (research loop). |
| `ado2gh/agents/migration_agent/nodes/read_tools.py` | `c4af9e04a14b7ef22be51455892d83601e0a8393` | **REJECT** | module name describes its content (read-only tools). |
| `ado2gh/agents/migration_agent/nodes/streaming.py` | `979129cb10d0990f2fe52535d66b39f18493dae0` | **REJECT** | module name describes its content (streaming utilities). |
| `ado2gh/agents/migration_agent/nodes/validator.py` | `971b13bb1479c2a4472afe1dcf0e37fef8501a4b` | **REJECT** | module name describes its content (validator node). |
| `ado2gh/agents/migration_agent/nodes/validator_investigation.py` | `5730bb6b57300ffa19b17054c7f2e1c57f0b08d6` | **REJECT** | module name describes its content (validator investigation loop). |
| `ado2gh/agents/migration_agent/policies.py` | `ba959f4583063d9d859d7af105dcc9fe946bb35d` | **REJECT** | `policies` accurately describes policy enforcement logic. |
| `ado2gh/agents/migration_agent/prompts.py` | `5df51dcc755bc3d2a57b1e0fd96db4bba62988ac` | **REJECT** | `prompts` accurately describes the system prompts module. |
| `ado2gh/agents/migration_agent/route_helpers.py` | `ecbeb8d39fffc8ef9495328afa022c51c554dee6` | **REJECT** | `route_helpers` accurately describes route handler utility functions. |
| `ado2gh/agents/migration_agent/runtime/` | `77f42351bfa24a6fe972700c056a172403896fe2` | **REJECT** | `runtime` accurately describes the folder containing agent runtime implementation. |
| `ado2gh/agents/migration_agent/runtime/context_window.py` | `e5d4cd6c15e1b29e6391c5e16613b7dcd36f28ae` | **REJECT** | `context_window` accurately describes context window management. |
| `ado2gh/agents/migration_agent/runtime/deps.py` | `fbb817b2f6ca4a01c6ca545f604ee5a7d0749e48` | **REJECT** | `deps` accurately describes dependency injection utilities. |
| `ado2gh/agents/migration_agent/runtime/llm_bridge.py` | `7b101660d242f4e5ca9ec484ebc78c17b739ada5` | **REJECT** | `llm_bridge` accurately describes the LLM bridge module. |
| `ado2gh/agents/migration_agent/runtime/orchestrator.py` | `6136d7a43b72a2631828cf051afbd5363ca922fb` | **REJECT** | `orchestrator` accurately describes orchestrator entry point logic. |
| `ado2gh/agents/migration_agent/runtime/tracing.py` | `35fbb8e8992a2edb47a6716b58e30b5fabb96afe` | **REJECT** | `tracing` accurately describes tracing configuration utilities. |
| `ado2gh/agents/migration_agent/session/` | `c7098edefa2c8c61e7d224be03eba4ed5519913f` | **REJECT** | `session` accurately describes the folder containing session management logic. |
| `ado2gh/agents/migration_agent/session/lifecycle.py` | `6840bfe2d9d8935791d405e852d7bb4ede4e2a09` | **REJECT** | `lifecycle` accurately describes session lifecycle management. |
| `ado2gh/agents/migration_agent/session/state.py` | `f4cf55f485646728db95b42baf286e2586d3dbc3` | **REJECT** | `state` accurately describes session state management. |
| `ado2gh/agents/migration_agent/session/store.py` | `ee6027981ef210d74f66356f0a6685895b209a66` | **REJECT** | `store` accurately describes persistent session storage. |
| `ado2gh/agents/migration_agent/tools/` | `c0ad4fb1a26e80c48a621d4e811ac51fca6a48ff` | **REJECT** | `tools` accurately describes the folder containing LangChain tool definitions. |
| `ado2gh/agents/migration_agent/tools/executor_tools.py` | `21779df317ea367d357387ae1922fb080e410fa4` | **REJECT** | `executor_tools` accurately describes executor-specific tool definitions. |
| `ado2gh/agents/migration_agent/tools/orchestrator_tools.py` | `5816afa3f3323792349a1cd2234901cea0256ead` | **REJECT** | `orchestrator_tools` accurately describes orchestrator-specific tool definitions. |
| `ado2gh/agents/migration_agent/tools/planner_tools.py` | `a7efeb9db9b98ad5858252a0b30887b2a8e21366` | **REJECT** | `planner_tools` accurately describes planner-specific tool definitions. |
| `ado2gh/agents/migration_agent/tools/shared_tools.py` | `142bf74f2ec5c30616032f2ed5d376b42c515194` | **REJECT** | `shared_tools` accurately describes tools shared across agent roles. |
| `ado2gh/agents/migration_agent/tools/validator_tools.py` | `9eca3c7d8cc0d7fa0573772c68156ff665420370` | **REJECT** | `validator_tools` accurately describes validator-specific tool definitions. |
| `ado2gh/agents/migration_agent/utils.py` | `a4788755fa467822633a5d32e88dd18af3a462a1` | **REJECT** | `utils` accurately describes shared utility functions. |

### `ado2gh/cli`

9 proposals (1 function/export, 8 module) — CONFIRM 1, REJECT 8, ESCALATE 0. Reviewer batches: B10.

#### Function / export proposals

| id | tag | state_hash | decision | rationale | protected as | at |
|----|-----|------------|----------|-----------|--------------|----|
| `ado2gh/cli/main.py::cli` | `name_review` | `a8995ba4356a195d27e934a0498b64d4735178b3` | **REJECT** | Click command callback; function name is framework-fixed by Click to become CLI group name. | cli_command,orphan_allowlist | ado2gh/cli/main.py:15 |

#### Module proposals (`module_name_review`)

| module id | state_hash | decision | rationale |
|-----------|------------|----------|-----------|
| `ado2gh/cli/` | `aa5c4f62d05bbb41f190feae6d387a1de1627437` | **REJECT** | Name appropriately describes the package as the CLI. |
| `ado2gh/cli/discover.py` | `71d9c1c2cf88e4f9598eba993ffe96685721e804` | **REJECT** | Name appropriately describes discovery-related commands (planning is closely related to discovery). |
| `ado2gh/cli/helpers.py` | `c069a68d37fcb0e413340e98317f9d18d764a911` | **REJECT** | Name appropriately describes shared helper functions. |
| `ado2gh/cli/main.py` | `9ea73560646c7a0fc4e51da342cdd74511820a0d` | **REJECT** | Name appropriately describes the main CLI entry point. |
| `ado2gh/cli/misc.py` | `1e48c5ab11cd977c6fc046e753c83736c881e84b` | **REJECT** | Name appropriately describes miscellaneous commands. |
| `ado2gh/cli/phase.py` | `2ba74f3f81d9032f2e2d03393ade1b5ba3e6f9e8` | **REJECT** | Name appropriately describes phase-related commands. |
| `ado2gh/cli/pipelines.py` | `43346df030a3863f65a46572675df0cf4cb0f876` | **REJECT** | Name appropriately describes pipeline-related commands. |
| `ado2gh/cli/run_cmd.py` | `294c057eb9a6e33d19a676666277dff361ec9e7b` | **CONFIRM** | Name "run_cmd" does not describe the module's broader responsibilities (status, rollback, validate, report, export-failed, token-status); emphasizes run command only. |

### `services/accelerator_api`

77 proposals (66 function/export, 11 module) — CONFIRM 7, REJECT 70, ESCALATE 0. Reviewer batches: B11, B12.

#### Function / export proposals

| id | tag | state_hash | decision | rationale | protected as | at |
|----|-----|------------|----------|-----------|--------------|----|
| `services/accelerator_api/auth_routes.py::_onboarding_redirect` | `name_review` | `f81ba3d42962c658b3eacb26df6f2a1c76fc8940` | **REJECT** | Property getter; name appropriately describes what it returns (the onboarding redirect URL). | — | services/accelerator_api/auth_routes.py:49 |
| `services/accelerator_api/auth_routes.py::_user_payload` | `name_review` | `e413b181e480e8314f6b0ee82da6f45eae541831` | **REJECT** | Factory/builder function; noun names are correct for functions that construct data structures. | — | services/accelerator_api/auth_routes.py:74 |
| `services/accelerator_api/auth_routes.py::approve_user` | `name_review` | `70c17fb528108cbd53696ef909d4edfe301df933` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route | services/accelerator_api/auth_routes.py:210 |
| `services/accelerator_api/auth_routes.py::current_session` | `name_review` | `817675d59c92cd4ad756977db4e12e9a7069020a` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route | services/accelerator_api/auth_routes.py:251 |
| `services/accelerator_api/auth_routes.py::login` | `name_review` | `ec45f6aadde9b57f1321df8298f4ca70f8f098d7` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route | services/accelerator_api/auth_routes.py:137 |
| `services/accelerator_api/auth_routes.py::logout` | `name_review` | `9aef9a8a17b854cebbdd748e8079ad7e6a0a5344` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route | services/accelerator_api/auth_routes.py:242 |
| `services/accelerator_api/main.py::_onboarding_redirect` | `name_review` | `f81ba3d42962c658b3eacb26df6f2a1c76fc8940` | **REJECT** | Property getter; name appropriately describes what it returns (the onboarding redirect URL). | orphan_allowlist | services/accelerator_api/main.py:138 |
| `services/accelerator_api/main.py::_pipeline_readiness_impl` | `name_review` | `b3518365d25b3a25226a817be564db1260f80216` | **REJECT** | Implementation function; noun name with `_impl` suffix appropriately describes the calculation it performs. | orphan_allowlist | services/accelerator_api/main.py:364 |
| `services/accelerator_api/main.py::dashboard` | `name_review` | `10c660ff0a736c41c19c12407a5a737a2af19762` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route,orphan_allowlist | services/accelerator_api/main.py:430 |
| `services/accelerator_api/main.py::enqueue_job` | `name_review` | `1921a651243103cd501c2e8c1427b661ecd686e6` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route,orphan_allowlist | services/accelerator_api/main.py:491 |
| `services/accelerator_api/main.py::freshness` | `name_review` | `9c0a51f04ad7a8f93fbe9799680a1c3d448fe608` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route,orphan_allowlist | services/accelerator_api/main.py:461 |
| `services/accelerator_api/main.py::health` | `name_review` | `bc191c7a31d14cd0372827d5e34fbc15504a9d8a` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route,orphan_allowlist | services/accelerator_api/main.py:156 |
| `services/accelerator_api/main.py::job_status` | `name_review` | `a913d34dce73955d465e74312488ebe46af5d895` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route,orphan_allowlist | services/accelerator_api/main.py:508 |
| `services/accelerator_api/main.py::migration_status` | `name_review` | `c43969408f52a885c704b13e3c71880b5832eb48` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route,orphan_allowlist | services/accelerator_api/main.py:417 |
| `services/accelerator_api/main.py::onboarding_status` | `name_review` | `b4a9fb3c98ece1fd03535ed6df60c7a0097cabca` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route,orphan_allowlist | services/accelerator_api/main.py:148 |
| `services/accelerator_api/main.py::pipeline_readiness` | `name_review` | `7c5df6f9ec103803372d182bf9d8ea2941ac6853` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route,orphan_allowlist | services/accelerator_api/main.py:407 |
| `services/accelerator_api/main.py::pipeline_readiness_post` | `name_review` | `d970e5c7423640e3cce2004bb9eac936ddcdf5b4` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route,orphan_allowlist | services/accelerator_api/main.py:412 |
| `services/accelerator_api/main.py::platform_auth_middleware` | `name_review` | `88bb1f59378d8d6a6b1ed8f20c9236320f213fce` | **REJECT** | Middleware function name correctly describes its role (platform authentication middleware). | orphan_allowlist | services/accelerator_api/main.py:105 |
| `services/accelerator_api/main.py::ready` | `name_review` | `753a147c4626e8ca497411f60af0eaae1245f44e` | **REJECT** | Framework-fixed signature: FastAPI route handler with wire-contract parameter names. | http_route,orphan_allowlist | services/accelerator_api/main.py:161 |
| `services/accelerator_api/routes/_shared.py::_accel` | `name_review` | `f841b9a9a093aaa9f15f1ce8791bbd15e7b51039` | **REJECT** | factory function with terse but clear naming; underscore-prefixed helpers commonly use short names. | — | services/accelerator_api/routes/_shared.py:69 |
| `services/accelerator_api/routes/_shared.py::_config_path` | `name_review` | `c34990dbb819d0ec6a29ec44f15b93414cd1451c` | **REJECT** | property accessor pattern; non-verb names are standard for getters that return a single value. | — | services/accelerator_api/routes/_shared.py:73 |
| `services/accelerator_api/routes/_shared.py::_governance_http_error` | `name_review` | `20ccb55f25201e39403fee39cf994874bbccd139` | **REJECT** | error converter function; -error pattern is standard for conversion factories. | — | services/accelerator_api/routes/_shared.py:89 |
| `services/accelerator_api/routes/_shared.py::_live_store` | `name_review` | `70ec7c83a0091b04dd84caa04c6a5d008689f7c1` | **REJECT** | factory/getter returning a store instance; noun naming is correct for factories. | — | services/accelerator_api/routes/_shared.py:85 |
| `services/accelerator_api/routes/_shared.py::_maybe_audit_model_enabled` | `bool_flag` | `df497ca42112f6a26db8bf1b21700bdc08f8c9d9` | **REJECT** | before_enabled and before_default are comparison values, not behaviour switches; used only in conditional at line 147 for state comparison. | — | services/accelerator_api/routes/_shared.py:136 |
| `services/accelerator_api/routes/_shared.py::_maybe_audit_model_enabled` | `name_review` | `df497ca42112f6a26db8bf1b21700bdc08f8c9d9` | **REJECT** | audit is a verb; maybe_audit is an appropriate name for conditional logging. | — | services/accelerator_api/routes/_shared.py:136 |
| `services/accelerator_api/routes/_shared.py::_platform_user` | `name_review` | `94e6a74a4a916f49ac45e69db9ab9e4552c0b8b2` | **REJECT** | property accessor following Python convention for request-scoped lookups. | — | services/accelerator_api/routes/_shared.py:77 |
| `services/accelerator_api/routes/_shared.py::_require_profile` | `bool_flag` | `501f4c04c3f90a0921ac1827dd38d5fb6758ad6e` | **CONFIRM** | require_active parameter controls distinct code path (line 113-118: validates active status only when true). | — | services/accelerator_api/routes/_shared.py:108 |
| `services/accelerator_api/routes/_shared.py::asdict_adv` | `name_review` | `d3321eac94d1cc66de045d933093360ca6f68720` | **CONFIRM** | abbreviation "adv" is ambiguous and should be expanded to describe the actual purpose. | — | services/accelerator_api/routes/_shared.py:161 |
| `services/accelerator_api/routes/migrate_guard.py::_live_scope_id` | `name_review` | `220a1b35e3342457b45bdaaf5931705a84fc90dc` | **REJECT** | builder/factory returning a scope identifier; non-verb noun naming is correct. | — | services/accelerator_api/routes/migrate_guard.py:33 |
| `services/accelerator_api/routes/migrate_guard.py::active_profile_id` | `name_review` | `7dc99daf586c014957ec42bb53e3e0c4dac7ada9` | **REJECT** | property accessor pattern; is_* and get_* style names without verbs are idiomatic for accessors. | — | services/accelerator_api/routes/migrate_guard.py:25 |
| `services/accelerator_api/routes/migrate_guard.py::audit_live_migration` | `name_review` | `1e56b6f4bb4df9aa9f5e9b740ba109641aed49c0` | **REJECT** | audit is a verb; name correctly describes the function's action. | — | services/accelerator_api/routes/migrate_guard.py:38 |
| `services/accelerator_api/routes/migrate_routes.py::_build_scope_context` | `bool_flag` | `52b666292fe06687a03f826ab489a34908e6ad89` | **CONFIRM** | dry_run parameter determines response context strategy (line 127: if dry_run sets different status). | — | services/accelerator_api/routes/migrate_routes.py:108 |
| `services/accelerator_api/routes/migrate_routes.py::_scope_handler_response` | `bool_flag` | `8677d77fb59a71dbd252c3c50b49895a13c1ef21` | **CONFIRM** | dry_run parameter determines response status field value (line 139-144: if dry_run sets status to "dry_run" vs success/partial/failed). | — | services/accelerator_api/routes/migrate_routes.py:132 |
| `services/accelerator_api/routes/migrate_routes.py::_scope_handler_response` | `name_review` | `8677d77fb59a71dbd252c3c50b49895a13c1ef21` | **REJECT** | describes builder function for scope responses; underscore-prefixed helpers with noun naming are appropriate. | — | services/accelerator_api/routes/migrate_routes.py:132 |
| `services/accelerator_api/routes/migrate_routes.py::_state_db` | `name_review` | `6d05240d73a9b7f81cb1181975ad13336b4509ed` | **REJECT** | factory/getter returning database instance; noun name is standard. | — | services/accelerator_api/routes/migrate_routes.py:87 |
| `services/accelerator_api/routes/migrate_routes.py::artifacts_publish` | `name_review` | `6cf99401cdb432da8b94ac149edde94868c29a58` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/artifacts")). | http_route | services/accelerator_api/routes/migrate_routes.py:599 |
| `services/accelerator_api/routes/migrate_routes.py::boards_migrate` | `name_review` | `94853d0a0d200a6af84ffd915649a503242b7cbd` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/boards")). | http_route | services/accelerator_api/routes/migrate_routes.py:437 |
| `services/accelerator_api/routes/migrate_routes.py::branch_policies_migrate` | `name_review` | `7d5505655ee11fd25e5016b65414f8a209139940` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/branch-policies")). | http_route | services/accelerator_api/routes/migrate_routes.py:729 |
| `services/accelerator_api/routes/migrate_routes.py::git_mirror` | `name_review` | `534482be14040248033ee8abdcd2a528ff8ee143` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/git-mirror")). | http_route | services/accelerator_api/routes/migrate_routes.py:193 |
| `services/accelerator_api/routes/migrate_routes.py::pipeline_convert` | `name_review` | `ef2325a658211c3c7ab16626b41d5d5b975e32f2` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/pipeline-convert")). | http_route | services/accelerator_api/routes/migrate_routes.py:250 |
| `services/accelerator_api/routes/migrate_routes.py::secret_provision` | `name_review` | `cc80ca50f3693fa3c617eea4690ef9dfd762aa36` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/secret-provision")). | http_route | services/accelerator_api/routes/migrate_routes.py:301 |
| `services/accelerator_api/routes/migrate_routes.py::service_connection_migrate` | `name_review` | `79736094cd94ba4138e8d09fe54827aa731d885d` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/service-connection")). | http_route | services/accelerator_api/routes/migrate_routes.py:362 |
| `services/accelerator_api/routes/migrate_routes.py::wiki_migrate` | `name_review` | `e54569a629c1cfc90041155d0ce0c07b5816e80c` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/wiki")). | http_route | services/accelerator_api/routes/migrate_routes.py:661 |
| `services/accelerator_api/routes/pipeline_routes.py::approve_live_execution` | `name_review` | `0c152d4cdf2022bd5af08852efd05a2ad0f8a71f` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/v1/platform/approvals/{approval_id}/approve")). | http_route | services/accelerator_api/routes/pipeline_routes.py:213 |
| `services/accelerator_api/routes/pipeline_routes.py::pipeline_steps` | `name_review` | `e48592e53ccdb32a989391f16c726a2466e5a085` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.get("/v1/pipeline/steps")). | http_route | services/accelerator_api/routes/pipeline_routes.py:50 |
| `services/accelerator_api/routes/profile_routes.py::activate_profile` | `name_review` | `b210e5aac34799d125c535257dd376710fe2b91f` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/v1/settings/profiles/{profile_id}/activate")). | http_route | services/accelerator_api/routes/profile_routes.py:349 |
| `services/accelerator_api/routes/profile_routes.py::appeal_profile` | `name_review` | `07ba20f601e3a7cecce143195f7b1efd30de6ee9` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/v1/settings/profiles/{profile_id}/appeal")). | http_route | services/accelerator_api/routes/profile_routes.py:328 |
| `services/accelerator_api/routes/profile_routes.py::approve_profile` | `name_review` | `da9e0e926e1306fac606555ae7bd7619462bc14d` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/v1/settings/profiles/{profile_id}/approve")). | http_route | services/accelerator_api/routes/profile_routes.py:283 |
| `services/accelerator_api/routes/profile_routes.py::deactivate_profile` | `name_review` | `a8965d2ab92ac856cacee06cfedaf1eb1e46d451` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/v1/settings/profiles/{profile_id}/deactivate")). | http_route | services/accelerator_api/routes/profile_routes.py:262 |
| `services/accelerator_api/routes/profile_routes.py::migration_scan_inline` | `name_review` | `1c7e0be2f2e8cd0a7cb3590750c7a78b7aa9ae53` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/v1/migration/scan")). | http_route | services/accelerator_api/routes/profile_routes.py:161 |
| `services/accelerator_api/routes/profile_routes.py::migration_scan_profile` | `bool_flag` | `edc7475b25dd23c1dcc2d42fb84a363274628a1e` | **CONFIRM** | sync parameter controls code path (line 185: if sync executes synchronously vs returns async job). | http_route | services/accelerator_api/routes/profile_routes.py:177 |
| `services/accelerator_api/routes/profile_routes.py::migration_scan_profile` | `name_review` | `edc7475b25dd23c1dcc2d42fb84a363274628a1e` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/v1/settings/profiles/{profile_id}/scan")). | http_route | services/accelerator_api/routes/profile_routes.py:177 |
| `services/accelerator_api/routes/profile_routes.py::profile_scan_status` | `name_review` | `e6f861ebc589f63ee9565d560de3e0053208864d` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.get("/v1/settings/profiles/{profile_id}/scan/status")). | http_route | services/accelerator_api/routes/profile_routes.py:198 |
| `services/accelerator_api/routes/proxy_routes.py::_audit_github_write` | `name_review` | `314eb4eaeec708ed9a7c7be5df4d8daebf18c36e` | **REJECT** | audit is a verb; name correctly describes the logging action. | — | services/accelerator_api/routes/proxy_routes.py:82 |
| `services/accelerator_api/routes/proxy_routes.py::_github_endpoint` | `name_review` | `b67fb0f3610b34efed6cbb36b05ba935331cf289` | **REJECT** | parser/normalizer extracting endpoint from path; non-verb noun is appropriate. | — | services/accelerator_api/routes/proxy_routes.py:76 |
| `services/accelerator_api/routes/proxy_routes.py::_http_error` | `name_review` | `f22ab4679db7360ad68191124bbb03f794b66832` | **REJECT** | error converter function; -error naming is standard for exception-to-exception factories. | — | services/accelerator_api/routes/proxy_routes.py:25 |
| `services/accelerator_api/routes/proxy_routes.py::_proxy_github_request` | `name_review` | `cc9f2d0fcd70f5f6fef214d6d176f6c77e3b3b7c` | **REJECT** | proxy is a verb; name correctly describes the forwarding action. | — | services/accelerator_api/routes/proxy_routes.py:102 |
| `services/accelerator_api/routes/proxy_routes.py::proxy_ado_get` | `name_review` | `a5cd2f2f6c5fc5c8059f38dab792e3f5cb48e474` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.get("/v1/ado/{path:path}")). | http_route | services/accelerator_api/routes/proxy_routes.py:38 |
| `services/accelerator_api/routes/proxy_routes.py::proxy_github_delete` | `name_review` | `987fbae97d8ec206894db99ad08515a22439482b` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.delete("/v1/github/{path:path}")). | http_route | services/accelerator_api/routes/proxy_routes.py:188 |
| `services/accelerator_api/routes/proxy_routes.py::proxy_github_get` | `name_review` | `0ea63ae3ddf5bc1e4f8eddd8965b20932ef9903a` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.get("/v1/github/{path:path}")). | http_route | services/accelerator_api/routes/proxy_routes.py:155 |
| `services/accelerator_api/routes/proxy_routes.py::proxy_github_patch` | `name_review` | `4a193ac7173ef7262e93b2b8c8a3c7191a4356f0` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.patch("/v1/github/{path:path}")). | http_route | services/accelerator_api/routes/proxy_routes.py:170 |
| `services/accelerator_api/routes/proxy_routes.py::proxy_github_post` | `name_review` | `da06cb963cc3ee1804b2c006cda6352bd34a22b7` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/v1/github/{path:path}")). | http_route | services/accelerator_api/routes/proxy_routes.py:161 |
| `services/accelerator_api/routes/proxy_routes.py::proxy_github_put` | `name_review` | `91283af26ada5bc6cd55eaf953d9536ab1b1c0f9` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.put("/v1/github/{path:path}")). | http_route | services/accelerator_api/routes/proxy_routes.py:179 |
| `services/accelerator_api/routes/settings_routes.py::approve_cloud_credentials` | `name_review` | `313126edc647c84f6db0c25bdca3fa9c23923852` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.post("/v1/settings/cloud-credentials/{provider}/approve")). | http_route | services/accelerator_api/routes/settings_routes.py:263 |
| `services/accelerator_api/routes/settings_routes.py::list_cloud_credentials` | `bool_flag` | `7869b9bf3ec839b91191b3c46f53de4b8be34edf` | **CONFIRM** | scan parameter controls code path (line 222: if scan calls .scan() before listing). | http_route | services/accelerator_api/routes/settings_routes.py:220 |
| `services/accelerator_api/routes/settings_routes.py::revoke_cloud_credentials` | `name_review` | `e195c6a22a77e2b01a523bcb23e86387d0fe0f8f` | **REJECT** | framework-fixed signature (FastAPI route handler at @router.delete("/v1/settings/cloud-credentials/{provider}")). | http_route | services/accelerator_api/routes/settings_routes.py:304 |

#### Module proposals (`module_name_review`)

| module id | state_hash | decision | rationale |
|-----------|------------|----------|-----------|
| `services/accelerator_api/auth_routes.py` | `6ecfc1c7aaf956ae35fe8488d38d0bf0d83f42bc` | **REJECT** | Module name clearly describes responsibility (authentication routes). |
| `services/accelerator_api/main.py` | `f4e7667e1b5704adfb270daa29e6bd7701015ff6` | **CONFIRM** | "main" is generic and does not describe responsibility; module orchestrates FastAPI app setup and includes routers (rename to app.py for clarity). |
| `services/accelerator_api/routes/` | `edebacd7d27f4fb883bba7936d67a6e45236a034` | **REJECT** | folder name routes describes its responsibility. |
| `services/accelerator_api/routes/_shared.py` | `c61cac2d64dee3a207f38b03d7775b4d5e5ad7ce` | **REJECT** | module name describes shared helpers for route modules. |
| `services/accelerator_api/routes/migrate_guard.py` | `b379e9f432258b3cef0619226ca946df8e7a2a19` | **REJECT** | module name describes live execution guard functionality. |
| `services/accelerator_api/routes/migrate_routes.py` | `4bee630ad46b59c3fc6aeb1389b19ffa7f662ff9` | **REJECT** | module name describes migration feature routes. |
| `services/accelerator_api/routes/migrate_routes_models.py` | `2ee94ccd1a5d282b50fc2663eb468b64b34f2e59` | **REJECT** | module name describes migration route request/response models. |
| `services/accelerator_api/routes/pipeline_routes.py` | `abf9ff9d312958ecfdb0c71fc57a3c27ce5005ff` | **REJECT** | module name describes pipeline run and approval routes. |
| `services/accelerator_api/routes/profile_routes.py` | `cc149d43d9b484209d1d6d13eb1291ff35ce80d8` | **REJECT** | module name describes profile management and scan routes. |
| `services/accelerator_api/routes/proxy_routes.py` | `289f6476471f9e09bff218fec4ce1184a652b97e` | **REJECT** | module name describes ADO and GitHub API proxy routes. |
| `services/accelerator_api/routes/settings_routes.py` | `f8fa3c373a1bfadef9e9ca812a546775e3180cd8` | **REJECT** | module name describes settings, LLM, connectivity, and credentials routes. |

### `services/agent`

16 proposals (11 function/export, 5 module) — CONFIRM 0, REJECT 16, ESCALATE 0. Reviewer batches: B13.

#### Function / export proposals

| id | tag | state_hash | decision | rationale | protected as | at |
|----|-----|------------|----------|-----------|--------------|----|
| `services/agent/main.py::_recover_sessions_on_restart` | `name_review` | `9dab39ac43a3ff4f4e9bd93451e6e13256ac0a92` | **REJECT** | The name is imperative (verb: recover) and accurately describes loading persisted sessions on startup. | orphan_allowlist | services/agent/main.py:58 |
| `services/agent/main.py::agent_auth_middleware` | `name_review` | `c457f98eabb8cf21e17ff36c99106850e7de700d` | **REJECT** | Middleware functions use noun-based names as an established pattern; the name clearly conveys its authentication function for the agent. | orphan_allowlist | services/agent/main.py:99 |
| `services/agent/profiles.py::capability_matrix` | `name_review` | `273fecfd10c8f23d3cf20cfd3bd44a5d93de9c44` | **REJECT** | Factory functions returning data structures conventionally use noun names; this accurately describes the dict of capability states. | — | services/agent/profiles.py:117 |
| `services/agent/routes/run_routes.py::health` | `name_review` | `295e955bcd036f5aac5a2145bdaee2335dc2bb59` | **REJECT** | Framework-fixed FastAPI route handler; endpoint names are HTTP wire contracts and cannot be changed. | http_route | services/agent/routes/run_routes.py:21 |
| `services/agent/routes/run_routes.py::metrics` | `name_review` | `e44848ba1fc14f9ba8da4cc10214f1d77b9e3faf` | **REJECT** | Framework-fixed FastAPI route handler; endpoint names are HTTP wire contracts and cannot be changed. | http_route | services/agent/routes/run_routes.py:96 |
| `services/agent/routes/session_routes.py::_continue_graph_after_form` | `name_review` | `6a074dc9943e39af9cbe5e87d953ce155378b5aa` | **REJECT** | The name uses a verb (continue) and accurately describes resuming graph execution after form submission. | — | services/agent/routes/session_routes.py:104 |
| `services/agent/routes/session_routes.py::agent_models` | `name_review` | `c4925211632072a1706b27156ab76fd99aa48445` | **REJECT** | Framework-fixed FastAPI route handler; endpoint names are HTTP wire contracts and cannot be changed. | http_route | services/agent/routes/session_routes.py:1263 |
| `services/agent/routes/session_routes.py::approve_session` | `name_review` | `01a59051b750f879040c84c784c604886d593313` | **REJECT** | Framework-fixed FastAPI route handler; endpoint names are HTTP wire contracts and cannot be changed. | http_route | services/agent/routes/session_routes.py:426 |
| `services/agent/routes/session_routes.py::llm_status` | `name_review` | `15fbd0aec75e66d600885975403b67b402b8600b` | **REJECT** | Framework-fixed FastAPI route handler; endpoint names are HTTP wire contracts and cannot be changed. | http_route | services/agent/routes/session_routes.py:1270 |
| `services/agent/routes/session_routes.py::session_message` | `name_review` | `14f11b4898568368ba6ed7503812f9181d4c8f6d` | **REJECT** | Framework-fixed FastAPI route handler; endpoint names are HTTP wire contracts and cannot be changed. | http_route | services/agent/routes/session_routes.py:523 |
| `services/agent/routes/session_routes.py::session_message_stream` | `name_review` | `2d6a22c83278e7a2696022e68456b7d608189a52` | **REJECT** | Framework-fixed FastAPI route handler; endpoint names are HTTP wire contracts and cannot be changed. | http_route | services/agent/routes/session_routes.py:569 |

#### Module proposals (`module_name_review`)

| module id | state_hash | decision | rationale |
|-----------|------------|----------|-----------|
| `services/agent/main.py` | `cf8550671deb32db142dc08617600ba715ce8cf5` | **REJECT** | Standard name for FastAPI app entry point and startup configuration. |
| `services/agent/profiles.py` | `bfee03e7a912255d21a606eca75082f54fc24f42` | **REJECT** | Name directly describes module responsibility of loading and managing runtime profiles. |
| `services/agent/routes/` | `588d9b55c2031bc842c706cdfbe5315e3500fa1e` | **REJECT** | Standard package name for grouping route handlers by domain. |
| `services/agent/routes/run_routes.py` | `5409326cd02e18627b18d0b1955cede7ab3b33f3` | **REJECT** | Name reasonably describes operational/health-check routes for monitoring the running agent service state. |
| `services/agent/routes/session_routes.py` | `e3c704108dace4ce4c66fa74d136cd088e654234` | **REJECT** | Name directly describes module responsibility of session lifecycle and message routing. |

### `apps/migration-ui`

11 proposals (11 function/export, 0 module) — CONFIRM 3, REJECT 8, ESCALATE 0. Reviewer batches: B14.

#### Function / export proposals

| id | tag | state_hash | decision | rationale | protected as | at |
|----|-----|------------|----------|-----------|--------------|----|
| `apps/migration-ui/src/components/CredentialActions.tsx::IconButton` | `bool_flag` | `a628f464701ebff3743aa646bc3c73585ae4c6e2` | **REJECT** | disabled is passed straight through to the DOM button element attribute, not a behavior switch within the function. | — | apps/migration-ui/src/components/CredentialActions.tsx:38 |
| `apps/migration-ui/src/components/CredentialActions.tsx::ValidationResult` | `bool_flag` | `cf55ebc31ac6e9bd089162a5081555c90b3a588b` | **REJECT** | valid boolean is rendered as part of the className, not controlling multiple code paths inside the function. | — | apps/migration-ui/src/components/CredentialActions.tsx:65 |
| `apps/migration-ui/src/components/PipelineProgress.tsx::StepPipelineBar` | `bool_flag` | `c9558e1d57c95b803c852e9f68f8ea3e81f7575e` | **CONFIRM** | compact controls iconSize calculation and conditional rendering of the labels row (line 109); it's a behavior switch. | — | apps/migration-ui/src/components/PipelineProgress.tsx:75 |
| `apps/migration-ui/src/components/ScanRecommendations.tsx::ScanRecommendations` | `bool_flag` | `cacb63e5958699d000f63280d5158e98be2ce9dc` | **CONFIRM** | compact controls className and conditional rendering of repo list (line 63); it's a behavior switch. | — | apps/migration-ui/src/components/ScanRecommendations.tsx:5 |
| `apps/migration-ui/src/lib/agent.ts::approveAgentSession` | `bool_flag` | `7bec5b3eff8c55452b69089765b4df80d51d97d5` | **REJECT** | approved is passed straight through to the HTTP request body as data. | — | apps/migration-ui/src/lib/agent.ts:317 |
| `apps/migration-ui/src/lib/agent.ts::createAgentSession` | `bool_flag` | `4edc5f323b7aa9a520460b152e45733376dfa8a8` | **REJECT** | dry_run is passed straight through to the HTTP request body as data. | — | apps/migration-ui/src/lib/agent.ts:194 |
| `apps/migration-ui/src/lib/agent.ts::patchAgentExecutionMode` | `bool_flag` | `a853a4f2440037f8b74a9dbe1b7564102cb1d777` | **REJECT** | dry_run is passed straight through to the HTTP request body as data. | — | apps/migration-ui/src/lib/agent.ts:305 |
| `apps/migration-ui/src/lib/agentChat.ts::isAgentInterruptible` | `bool_flag` | `9bc051e40cd2b91d96dfb22ad775d84498237124` | **CONFIRM** | flags object controls early return paths (line 42) and final return value computation; each flag is a behavior switch. | — | apps/migration-ui/src/lib/agentChat.ts:32 |
| `apps/migration-ui/src/lib/api.ts::fetchReadiness` | `bool_flag` | `bd3587af9e1373b4d8ca62a111083e06d6e7ae42` | **REJECT** | refreshInventory is passed straight through to the HTTP request body as data. | — | apps/migration-ui/src/lib/api.ts:55 |
| `apps/migration-ui/src/lib/api.ts::startPipelineRun` | `bool_flag` | `5769d112369511abc49852995ce1ee0236c98961` | **REJECT** | dry_run is passed straight through to the HTTP request body as data. | — | apps/migration-ui/src/lib/api.ts:366 |
| `apps/migration-ui/src/lib/cloudCredentials.ts::fetchCloudCredentials` | `bool_flag` | `360172c7c812c4b331e628df2c6d28334cc836e4` | **REJECT** | scan is passed to the query string as data (line 72), not controlling code paths inside the function. | — | apps/migration-ui/src/lib/cloudCredentials.ts:71 |

## Confirmed remediation worklist (CONFIRM only)

| package | id | tag | state_hash |
|---------|----|-----|------------|
| `ado2gh/reporting` | `ado2gh/reporting/reporter.py::_colour` | `name_review` | `f920fd446fc911e82a66d7b966bc027d54364ebe` |
| `ado2gh/core` | `ado2gh/core/ado_cleanup.py::ADOCleanup.__init__` | `bool_flag` | `6605ddaaf5a87c100647bb1c2f95a3cf27933f54` |
| `ado2gh/core` | `ado2gh/core/ado_cleanup.py::ADOCleanup._cleanup_one` | `bool_flag` | `743162111cdf86d45ba7440cee9a27d900b2c828` |
| `ado2gh/core` | `ado2gh/core/ado_cleanup.py::ADOCleanup.cleanup_repos` | `bool_flag` | `6e5b7492ed91c00d62d64fd54e86ab9b0527e978` |
| `ado2gh/core` | `ado2gh/core/gei_runtime.py::gei_subprocess_env` | `name_review` | `8eb2e7ee643fc61eb369225d2ac271455d4dc382` |
| `ado2gh/core` | `ado2gh/core/migration_engine.py::MigrationEngine.__init__` | `bool_flag` | `353e0baa42887fcf612f4e0364b1457748e831d1` |
| `ado2gh/core` | `ado2gh/core/migration_engine.py::MigrationEngine._in_progress_block_reason` | `name_review` | `ecea0feaeb5109212b31942eb3c5d6118321d30c` |
| `ado2gh/core` | `ado2gh/core/migration_fr036.py::repo_conflict_reason` | `name_review` | `a1ca03f6722fe79912a8302abc2a4228efbe5058` |
| `ado2gh/core` | `ado2gh/core/migration_fr036.py` | `module_name_review` | `f49e81f1248e4bf69ff75b341b44c8a230c78937` |
| `ado2gh/core` | `ado2gh/core/rollback.py::RollbackHandler._rollback_branch_protection` | `bool_flag` | `9fa74f4f54a223f519de917853792d9c31551881` |
| `ado2gh/core` | `ado2gh/core/rollback.py::RollbackHandler._rollback_pipelines` | `bool_flag` | `48134868dbde13f6b780eefbbb55bbd543fc2729` |
| `ado2gh/core` | `ado2gh/core/rollback.py::RollbackHandler._rollback_repo` | `bool_flag` | `794706635193e6b12da653debc088ecce770e55d` |
| `ado2gh/core` | `ado2gh/core/rollback.py::RollbackHandler.rollback_repos` | `bool_flag` | `62524c9388c27c054fed010bc1d231ab2617dc75` |
| `ado2gh/core` | `ado2gh/core/rollback.py::RollbackHandler.rollback_wave` | `bool_flag` | `8880c143d5fcc9f41703aa12d54f4d8a21a620a9` |
| `ado2gh/core` | `ado2gh/core/scopes/git_scope.py::GitScopeHandler._source_default_branch` | `name_review` | `868cd831db05b14769d5862fef28a0ea51b76619` |
| `ado2gh/core` | `ado2gh/core/scopes/git_scope.py::_ado_git_env` | `name_review` | `5be4a9083209176b4a6d75415c513a21c4218575` |
| `ado2gh/core` | `ado2gh/core/scopes/pipelines_scope.py::_workflow_branch` | `name_review` | `3bd507b1de8739c6a38f97aaf78016a1b395bd7c` |
| `ado2gh/core` | `ado2gh/core/wave_runner.py::WaveRunner.run_wave` | `bool_flag` | `6bfa78f851cc28a780f41720b84ebd9538370161` |
| `ado2gh/api` | `ado2gh/api/` | `module_name_review` | `5f1b6abff9257d2d5ea9c638b3119841a4b7e35e` |
| `ado2gh/api` | `ado2gh/api/llm/http_llm.py::build_llm_http_client` | `bool_flag` | `0236d7769767421fc60604083f84a188d4535d31` |
| `ado2gh/api` | `ado2gh/api/llm/llm_provider_registry.py::list_provider_specs` | `bool_flag` | `7381904a11fb3e1644dbe12251a89f8c7dca20e2` |
| `ado2gh/api` | `ado2gh/api/llm/model_validation.py::_validate_openai_compatible` | `bool_flag` | `13421aebc0fd0e0cd5c47168e613977cdc03f231` |
| `ado2gh/api` | `ado2gh/api/migration_scan.py::persist_scan_results` | `bool_flag` | `5b8a05b46ee324afede8d768197ce3852cf33791` |
| `ado2gh/api` | `ado2gh/api/migration_scan.py::scan_with_credentials` | `bool_flag` | `dbd712caa812b1d5225cb7c608d478a7f4fb046f` |
| `ado2gh/api` | `ado2gh/api/migration_status_report.py::_pipeline_run_repo_outcomes` | `name_review` | `5d43b96a64145ec1471c6a89fa29dcf53b39dcc1` |
| `ado2gh/api` | `ado2gh/api/migration_work_plan.py::executable_work_items` | `name_review` | `9b18b6fe96943d67130e908c0e421dcd66bb7e05` |
| `ado2gh/api` | `ado2gh/api/migration_work_plan.py::plan_narrative_from_work_items` | `bool_flag` | `fbf880fdc728c735996c815f2005d4d6f955be75` |
| `ado2gh/api` | `ado2gh/api/platform_rbac.py::operator_requires_live_approval` | `bool_flag` | `cd15368399c683b118ab94397cd44a815c1aeefe` |
| `ado2gh/api` | `ado2gh/api/run_reporting.py::_scope_detail_message` | `name_review` | `85bfc515ca5ec33a0186ad1789782612605c9265` |
| `ado2gh/api` | `ado2gh/api/run_reporting.py::validation_message` | `name_review` | `bfce3c13d9377b4656a062de687b154f82bfccda` |
| `ado2gh/api` | `ado2gh/api/settings_store.py::SettingsStore.update_phases` | `bool_flag` | `b7cb40352818d75246b68951fafeb9b983d7f299` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/hitl/form_fields.py::resolution_options_from_context` | `bool_flag` | `191a11002321f9168bd8ce03e583a0ee1afc030d` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/hitl/intake.py::consult_planner_context` | `bool_flag` | `d6a7e94380eb9f7ea4f48945f920091449e61bff` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/hitl/intake_guardrails.py::apply_analysis_guardrails` | `bool_flag` | `86e483b0a5baf9016cd5cf5780aad2a08d696518` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/nodes/executor/node.py::_execute_deterministic_repo_scopes` | `bool_flag` | `18fbf2436435c3d2518021f9561ed0d436da1218` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/nodes/executor/pipeline.py::ensure_agent_pipeline_run` | `bool_flag` | `74e8070568ca6a6a543d4351820d7dd857c4d216` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/nodes/executor/scope.py::_execute_secrets_scope` | `bool_flag` | `3ea77a443ffeed00c36e9db8e8639fb16801ba16` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/nodes/executor/scope.py::execute_migration_scope` | `bool_flag` | `f6373fc2eb70d7ec86ef808e8f0d0eb13c98ff5e` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/nodes/orchestrator.py::_handle_general_chat` | `bool_flag` | `9e26ee74016f28d585d90322c01d68195bb74694` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/nodes/orchestrator.py::_handle_migration_action` | `bool_flag` | `d4c40549ab8b36d07cccf83e8767e65856272bcb` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/nodes/orchestrator.py::_handle_migration_info` | `bool_flag` | `311776c15cb3708adbf4cd75e44ef2810cd982ba` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/nodes/planner_research.py::_run_planner_research_loop` | `bool_flag` | `9fe1203f65415530ddbcaf0fb96651954ba7828e` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/nodes/validator.py::_failure_is_benign` | `bool_flag` | `b22a84671ae6573bb3c7e86505cf365ee963bd7e` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/nodes/validator.py::_scope_result_is_benign` | `bool_flag` | `8f254bb2a6adc400f4fbd80dd3848fdc09001133` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/nodes/validator.py::_validate_scope` | `bool_flag` | `b39177e1cc496db796e85c3c06fdfadd68ef630c` |
| `ado2gh/agents` | `ado2gh/agents/migration_agent/session/state.py::maybe_reset_for_migration_request` | `bool_flag` | `1e80c4a04a21850ee6d38ed3946a01a797ed405b` |
| `ado2gh/cli` | `ado2gh/cli/run_cmd.py` | `module_name_review` | `294c057eb9a6e33d19a676666277dff361ec9e7b` |
| `services/accelerator_api` | `services/accelerator_api/main.py` | `module_name_review` | `f4e7667e1b5704adfb270daa29e6bd7701015ff6` |
| `services/accelerator_api` | `services/accelerator_api/routes/_shared.py::_require_profile` | `bool_flag` | `501f4c04c3f90a0921ac1827dd38d5fb6758ad6e` |
| `services/accelerator_api` | `services/accelerator_api/routes/_shared.py::asdict_adv` | `name_review` | `d3321eac94d1cc66de045d933093360ca6f68720` |
| `services/accelerator_api` | `services/accelerator_api/routes/migrate_routes.py::_build_scope_context` | `bool_flag` | `52b666292fe06687a03f826ab489a34908e6ad89` |
| `services/accelerator_api` | `services/accelerator_api/routes/migrate_routes.py::_scope_handler_response` | `bool_flag` | `8677d77fb59a71dbd252c3c50b49895a13c1ef21` |
| `services/accelerator_api` | `services/accelerator_api/routes/profile_routes.py::migration_scan_profile` | `bool_flag` | `edc7475b25dd23c1dcc2d42fb84a363274628a1e` |
| `services/accelerator_api` | `services/accelerator_api/routes/settings_routes.py::list_cloud_credentials` | `bool_flag` | `7869b9bf3ec839b91191b3c46f53de4b8be34edf` |
| `apps/migration-ui` | `apps/migration-ui/src/components/PipelineProgress.tsx::StepPipelineBar` | `bool_flag` | `c9558e1d57c95b803c852e9f68f8ea3e81f7575e` |
| `apps/migration-ui` | `apps/migration-ui/src/components/ScanRecommendations.tsx::ScanRecommendations` | `bool_flag` | `cacb63e5958699d000f63280d5158e98be2ce9dc` |
| `apps/migration-ui` | `apps/migration-ui/src/lib/agentChat.ts::isAgentInterruptible` | `bool_flag` | `9bc051e40cd2b91d96dfb22ad775d84498237124` |

