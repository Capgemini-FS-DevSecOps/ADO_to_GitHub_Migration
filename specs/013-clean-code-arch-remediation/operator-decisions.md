# Operator decision memo — feature 013 open items

**Date**: 2026-09-13
**Branch**: `feature/ado-agentic-ai`, HEAD `5ee1c56`
**Scope**: the thirteen items feature `013-clean-code-arch-remediation` cannot close without
an operator decision. Every file and line cited below was read in the working tree on the
date above; where a line number differs from the one recorded in `gap-register.md`, the
memo gives the current one and says so.

The high-severity tally this memo works against is the one T095 measured: **16 remediated,
2 deferred (GAP-018, GAP-022), 4 open (GAP-019, GAP-024, GAP-031, GAP-054) = 22**.

Items 1, 2, 3, 6 and 8 change a public contract under FR-006 and therefore need an entry in
`plan.md` § Approved contract changes before their fix may be applied (FR-024). Ready-to-paste
entries are given in each section as indented blockquotes; those blocks are verbatim
deliverables and sit outside the per-section length budget, which the analysis above each of
them keeps. They are numbered **9–13**, on the assumption that item 13
promotes the four changes currently awaiting sign-off into plan entries **5–8**, matching the
numbers they already carry in `contracts/public-contract-freeze.md`. If the operator approves
a different subset, renumber sequentially from the last approved entry.

---

## 1. GAP-018 — flip the `--dry-run` default on `run`, `phase run` and `ado-cleanup`

**Question.** Should the three migration commands default to dry-run, with live execution
requiring an explicit opt-in flag?

**Context.** All three declare `--dry-run` as `is_flag=True, default=False`:
`ado2gh/cli/migration.py:35` (`run`), `ado2gh/cli/phase.py:84` (`phase run`),
`ado2gh/cli/misc.py:89` (`ado-cleanup`). The register cites `ado2gh/cli/run_cmd.py:18-19` for
the first; that file no longer exists — the command moved to `ado2gh/cli/migration.py` and the
defect moved with it unchanged. None calls `click.confirm`; the only one in `ado2gh/` is
`rollback` (`ado2gh/cli/migration.py:175`).

GAP-018's approval-token half landed in `5f799e7`, but it does not cover the CLI: the CLI never
sends a token — `ado2gh/cli/migration.py:74-76` builds `RunWaveRequest(config_path=…, wave_id=…,
dry_run=dry_run, db_path=db)` — and `ado2gh/api/accelerator.py:186` only enters the check under
`if request.live_approval_id:`. On the CLI path the flag default is the only guard that exists.

**Options.**

*A — flip all three, via a paired flag.* Change the three decorators to
`@click.option("--dry-run/--live", default=True, …)` and update the three help strings; no
handler body changes, since `dry_run` keeps its name and `ExecutionMode.from_dry_run(dry_run=…)`
already sits at the boundary. **Snapshot: yes, three lines** in `cli_commands`, measured with
click 8.4.2 against the formatter at `tests/contract/test_public_surface_snapshot.py:91-97`:

```
"ado2gh run :: param dry_run :: opts=--dry-run/--live :: type=boolean :: default=True :: required=False :: is_flag=True :: multiple=False"
"ado2gh phase run :: param dry_run :: opts=--dry-run/--live :: type=boolean :: default=True :: required=False :: is_flag=True :: multiple=False"
"ado2gh ado-cleanup :: param dry_run :: opts=--dry-run/--live :: type=boolean :: default=True :: required=False :: is_flag=True :: multiple=False"
```

Entry count stays 96. Test: `tests/unit/test_gap_018_dry_run_default.py` — three cases
asserting each command performs no write without `--live`, plus one asserting `--dry-run`
still parses. Docs: `docs/COMMAND_REFERENCE.md:210, 237, 357` (the **Options:** lines) plus the
`--dry-run` examples at `:188, :234, :345, :491, :518`; `README.md`, `docs/EXECUTION_MANUAL.md`,
`docs/MIGRATION_RUNBOOK.md`, `docs/ARCHITECTURE.md` and `docs/LOCAL_DEVELOPMENT.md` carry
`--dry-run` lines that stay correct but read as redundant. Blast radius: any unattended script
running `ado2gh run` without `--dry-run` silently stops migrating.

*B — flip `ado-cleanup` only.* Same mechanics, one line of snapshot drift. Narrower blast
radius, and `ado-cleanup` is the least reversible of the three (it disables ADO pipelines and
can archive the source repo). Leaves `run` and `phase run` exactly as GAP-018 describes them.

*C — keep all three.* Zero drift, zero doc churn. GAP-018 stays `deferred` with the landed
token check as its compensating control.

**Safeguards.** CA-001 requires every dry-run preview path to survive and behave identically:
A and B keep `--dry-run` typeable and unchanged in meaning, so CA-001 holds and is
strengthened. CA-002 forbids *reducing* the set of actions requiring confirmation; an opt-in
only enlarges it.

**Recommendation: A.** The CLI is the one live path with no server-side approval check
whatsoever, so the flag default is the only guard available there, and a paired flag keeps
every existing `--dry-run` runbook working verbatim.

**Plan entry (paste into `plan.md` § Approved contract changes):**

> **9. `run`, `phase run` and `ado-cleanup` default to dry-run and require `--live` to execute
> (GAP-018). Decision (operator, 2026-09-13): approved.** The three migration commands declared
> `--dry-run` as `is_flag=True, default=False`, so each mutated unless the operator opted out,
> and none prompted for confirmation. On the CLI path that default was the only guard in
> existence: the approval-token check landed in `5f799e7` fires only when a request quotes a
> `live_approval_id`, and `ado2gh/cli/migration.py` never sends one. Each option becomes
> `--dry-run/--live` with `default=True` in `ado2gh/cli/migration.py`, `ado2gh/cli/phase.py`
> and `ado2gh/cli/misc.py`; `--dry-run` keeps its name and its meaning, and
> `ExecutionMode.from_dry_run(dry_run=…)` still converts at the boundary.
>
> *Migration note.* Any script or runbook that ran these three commands without `--dry-run` and
> expected a real migration must add `--live`. Until it does, the command reports what it would
> do and changes nothing — a safe failure, but an unattended pipeline will appear to succeed
> while migrating nothing. Scripts that already pass `--dry-run` are unaffected.
>
> *Snapshot impact: yes — three lines on `cli_commands`.* The `dry_run` param line for
> `ado2gh run`, `ado2gh phase run` and `ado2gh ado-cleanup` each change `opts=--dry-run` to
> `opts=--dry-run/--live` and `default=False` to `default=True`. The entry count stays at 96;
> no command or option is added or removed.

---

## 2. GAP-019 — `/provision` and `/remediate` trust a client-supplied `actor`

**Question.** Should both routes bind `Request` and derive the actor from the authenticated
session, deleting the client-supplied `actor` field?

**Context.** `services/agent/routes/session_routes.py:253-263` —
`provision_session(session_id: str, req: ProvisionRequest)` takes no `Request`, and grants the
write tier on `if req.tier == "write" and req.actor != "approver"` (`:259`), a string
comparison against a free-text body field declared at `services/agent/routes/_helpers.py:97-102`
(`actor: str = "operator"`). `remediate_session` (`:266-277`) has the same missing `Request`,
but `RemediateRequest` (`_helpers.py:105-109`) carries only `repo_key` and `retry_count` —
there is **no** `actor` field to remove there. Both also read `_sessions.get(session_id)`
directly (`:256`, `:269`) instead of `_get_accessible_session` (`_helpers.py:262-280`), so
neither performs the ownership check every sibling route does.

The helpers needed already exist in the same file: `_platform_user` (`:203`), `_audit_actor`
(`:215`), `_require_operate` (`:227`), `_require_approve_live` (`:237`), and
`attach_actor_to_session` (`ado2gh/agents/migration_agent/policies.py:158`). A repo-wide search
of `apps/migration-ui/src/` for `provision` or `remediate` returns nothing: **there is no
console caller**, and `session["provision_tier"]` has no reader.

**Options.**

*A — bind `Request`, derive server-side, delete the field.* In `session_routes.py`, add
`request: Request` to both handlers; replace `_sessions.get(...)` with
`_get_accessible_session(session_id, request)`; replace the `req.actor != "approver"` check with
`_require_approve_live(request)`; take the message author from `_audit_actor(request)[0]` at
`:262`. In `_helpers.py`, delete `ProvisionRequest.actor`. No console edit is needed —
`apps/migration-ui/src/lib/api.ts` and `agentApi` never call either route. Eight sibling call
sites already use this pattern, e.g. `execution_routes.py:103-106`. Test:
`tests/auth/test_gap_019_provision_actor_server_side.py` — write tier refused without an
approver identity, refused when the body claims `actor: "approver"`, granted for a real
APPROVER, and a session owned by another user answers 404. **Snapshot: no** — `agent POST
/v1/sessions/{session_id}/provision` and `…/remediate` stay in `http_routes` unchanged; the
snapshot records method and path only, not request schemas. It is still an FR-006 contract
change, because the request payload shape changes.

*B — keep the field but ignore it.* Zero client impact. FR-006a forbids it, and here the
prohibition earns its keep: a declared, documented, defaulted `actor` field the server silently
discards is a worse artefact than either the bug or the fix, because a caller reading the model
has no way to learn that setting it does nothing. The field is the whole evidence of the
defect; leaving it invites the next reader to wire it back up.

**Safeguards.** CA-004: option A routes the provision message through `_audit_actor` and so
names a real identity where the audit line currently records whatever string the caller sent.
CA-002 is unaffected — the write-tier gate moves from the body to the session, it is not removed.

**Recommendation: A.** The field has no console caller, the helpers sit forty lines away in
the same module, and the ownership check comes free with `_get_accessible_session`.

**Plan entry:**

> **10. `/v1/sessions/{id}/provision` and `/remediate` derive the actor server-side;
> `ProvisionRequest.actor` is removed (GAP-019). Decision (operator, 2026-09-13): approved.**
> `provision_session` granted the `write` provisioning tier on a string comparison against a
> free-text `actor` field in the request body, and neither route received a `Request` at all,
> so neither could consult the authenticated identity or check session ownership. Both handlers
> in `services/agent/routes/session_routes.py` now take `Request`, resolve the session through
> `_get_accessible_session`, gate the write tier on `_require_approve_live`, and attribute the
> recorded message through `_audit_actor`. `ProvisionRequest.actor` is deleted outright from
> `services/agent/routes/_helpers.py` with no shim (FR-006a). `RemediateRequest` never had an
> `actor` field and is unchanged apart from the added `Request`.
>
> *Migration note.* Neither route has an in-repo caller, so nothing here needs updating. An
> external client sending `actor` to `/provision` finds it ignored — `ProvisionRequest` does not
> set `extra="forbid"`, so the request is accepted, not rejected — and must instead authenticate
> as a user holding `can_approve_live_execution` to obtain the write tier. A client addressing a
> session it does not own now receives 404 rather than a successful write.
>
> *Snapshot impact: none.* Both route paths and methods are unchanged, and request-model
> fields are not among the four frozen keys.

---

## 3. GAP-024 — boolean `recommended_value` stringified server-side

**Question.** Should `recommended_value` stay a real JSON boolean end to end, or should the
browser learn to parse the string the server sends?

**Context.** `ado2gh/agents/migration_agent/hitl/form_fields.py:204` sets
`"recommended_value": False` for `confirm_execute` as a real Python bool. `:99-100` then does
`if recommended_value is not None and str(recommended_value).strip() != "": field["recommended_value"] = str(recommended_value).strip()[:120]`,
turning it into `"False"`; `ado2gh/agents/migration_agent/hitl/forms.py:39-43` repeats the
pattern. In the browser, `apps/migration-ui/src/lib/agentChat.ts:180-181` does
`if (field.type === 'checkbox') return Boolean(field.recommended_value)`, and `Boolean("False")`
is `true` — so the confirmation checkbox renders **checked** against the backend's stated
intent. (Register line numbers `:74-78`/`:176-183` have drifted to `:99-100`/`:204`.)

Two things reduce the blast radius: `apps/migration-ui/src/lib/agent.ts:117` already declares
`recommended_value?: string | boolean`, and the server re-checks live authority independently
(`policies.py:138-149` via `_try_start_pev_run`), so no unauthorised execution path exists.
One increases it: `agentChat.ts:194-195` holds a **second, independent inversion** —
`if (field.type === 'checkbox') return field.name === 'confirm_execute'`, so a `confirm_execute`
checkbox arriving with no `recommended_value` at all initialises to `true`. Whichever option is
chosen, that line should be fixed in the same change.

**Options.**

*A — JSON booleans end to end.* In `form_fields.py:99-100`, preserve a `bool` unchanged and
stringify only non-bool values; same at `forms.py:39-43`. In `agentChat.ts:179-183`, coerce
explicitly (`typeof v === 'boolean' ? v : v !== 'false' && v !== 'False'`) so a legacy string
payload still reads correctly, and change `:195` to `return false`. No TS type
change (`agent.ts:117` already allows it). Tests:
`tests/agent/test_gap_024_boolean_recommended_value.py` asserting `field_dict_from_spec` and
the `forms.py` builder emit `False`, not `"False"`, plus new cases in
`apps/migration-ui/src/lib/agentChat.test.ts` (extend the block at `:125-127`) for `false`,
`"False"` and a checkbox with no recommendation at all. **Snapshot: no** — no route, CLI name, env var or table changes; it is
an FR-006 change because the inter-service message shape changes.

*B — parse strings in TS only.* One file touched, `agentChat.ts:179-183` alone; no backend
change and no contract change at all. The backend keeps asserting a type it does not send, and
the trap is re-armed for every future boolean field — the register's own blast-radius note
("any other boolean recommended field reproduces it") is exactly this.

**Safeguards.** CA-002 — `confirm_execute` is the confirmation control, and A restores its
documented safe default. CA-003 is unaffected either way: `recommended_value` never carries a
secret, and the `[:120]` truncation stays on the string branch.

**Recommendation: A.** The TS type already declares `string | boolean`, so only the Python
stringify and the checkbox coercion move; B leaves the backend lying about the type and
re-arms the trap for the next boolean field.

**Plan entry:**

> **11. Form fields carry boolean `recommended_value` as a JSON boolean (GAP-024). Decision
> (operator, 2026-09-13): approved.** `field_dict_from_spec` coerced every recommended value
> through `str(...)`, so the planner's `recommended_value: False` for `confirm_execute` reached
> the console as the string `"False"`, and `Boolean("False")` is `true` — the live-execution
> confirmation checkbox rendered pre-checked against the backend's intent.
> `ado2gh/agents/migration_agent/hitl/form_fields.py` and `.../hitl/forms.py` now pass a `bool`
> through unchanged and stringify only non-boolean values; `apps/migration-ui/src/lib/agentChat.ts`
> coerces explicitly and still accepts the old string form, and its `confirm_execute` fallback
> for a field with no recommendation returns unchecked instead of checked. The console type
> `AgentFormField.recommended_value` already declared `string | boolean`, so no type changed.
>
> *Migration note.* Any client rendering agent HITL forms must accept `true`/`false` as well as
> `"true"`/`"false"` for `recommended_value`; one that applied `Boolean(...)` to the string form
> will now, correctly, see an unchecked confirmation box. No server-side authority changes: live
> execution was and remains re-checked from server-held state before any run, independent of the
> submitted `confirm_execute`.
>
> *Snapshot impact: none.* No route path, CLI command, environment variable or table name is
> touched; the payload field type is not one of the four frozen keys.

---

## 4. GAP-031 — variable-group variables never reach the workflow or its notes

**Question.** Should the generated migration notes gain a "Variable groups" section listing
group and variable names, and should the workflow YAML also gain `env:` placeholders?

**Context.** `ado2gh/pipelines/extractor.py:270-275` and `:323-328` capture each referenced
group into `meta.variable_groups` as `{"id", "name", "type", "variables"}`, where `variables`
is `list(vg.get("variables", {}).keys())` — **names only, no values ever**. Nothing downstream
reads it: `ado2gh/pipelines/transform/transformer.py:453-460` builds `env` solely from
`meta.variables`, and `_write_migration_notes` (`:462-515`) has a `## Service Connections`
section at `:498-503` with no equivalent for groups. The one mitigation is aggregate:
`ado2gh/reporting/pipeline_readiness.py:223-226` warns "Variable groups need manual setup" and
adds effort hours, so the omission is visible in the readiness report but not in the artefacts
an operator actually reads.

**Options.**

*A — notes section only.* Add a `## Variable Groups` block to `_write_migration_notes`
(`transformer.py:462-515`), modelled byte-for-byte on the service-connections block at
`:498-503`: group name as a heading line, then its variable names as backticked list items,
with a one-line instruction to create the equivalent GitHub secrets or repository variables.
Because the extractor only ever stored names, this section cannot print a value even if the
ADO API returned one. File size: `transformer.py` is 515 lines, well under the 800-line cap
(`tests/unit/test_file_size_limit.py:12`). Test:
`tests/pipeline/test_gap_031_variable_groups_in_notes.py` — a metadata fixture with two groups
produces a notes file naming both groups and every variable name, and a fixture whose group
dict carries a stray `value` key still prints no value. **Snapshot: no.** **Contract: no** —
a generated markdown artefact is none of FR-006's five frozen contracts.

*B — A plus `env:` placeholders in the workflow.* Additionally extend `_build_env_block`
(`:453-460`) to emit `env: { NAME: "${{ vars.NAME }}" }` (or `secrets.NAME`) for each
group-sourced name, which also feeds `env_keys` at `:66-67` and would let
`rewrite_expressions_inplace` resolve `$(name)` macros instead of leaving literal text.
Genuinely better for the operator who runs the workflow — and genuinely riskier: the generator
cannot know whether a group variable is secret, so every placeholder is a guess, and a wrong
`vars.`/`secrets.` choice produces a workflow that parses, runs, and silently resolves to
empty. That needs a per-variable secrecy signal the extractor does not currently capture.

**Safeguards.** CA-003 is the governing one. Option A is safe by construction because the
stored structure holds no values (`extractor.py:274`, `:327`). Option B does not print values
either, but it writes names into an executable artefact, so the CA-003 test must assert that no
`env:` entry ever carries a literal.

**Recommendation: A.** It closes the operator-visibility defect the gap actually describes,
with a data structure that cannot leak a value; B needs a secret/non-secret signal the
extractor does not collect yet and should be filed as its own follow-up.

---

## 5. `sync: bool = False` on `POST /v1/settings/profiles/{id}/scan`

**Question.** Record the parameter as wire data with a `noqa` and an exception-register row,
or replace it with an enum/`mode` query value?

**Context.** `services/accelerator_api/routes/profile_routes.py:278` — `sync: bool = False` on
`migration_scan_profile`, one of the four ruff findings that currently fail CI's
`ruff check ado2gh/ services/` step (T095 log, `run-t095-local-ci.txt:12,23`: FBT001 and FBT002
at `profile_routes.py:278:5`). The inventory row
`services/accelerator_api/routes/profile_routes.py::migration_scan_profile` carries
`proposed_tags: ["bool_flag"]` with `disposition: "pending"`. It is genuinely wire data with
live callers: `services/agent/routes/form_routes.py:157` and `:443` both issue
`/v1/settings/profiles/{profile_id}/scan?sync=true`, and `tests/profile/test_profile_approval_flow.py:70`
does the same. The docstring at `:290-297` already documents both modes properly.

**Options.**

*A — `bool_data` + `# noqa: FBT001,FBT002` + exception row.* One line changes at `:278`; the
disposition moves from `pending` to `exception` via
`python specs/013-clean-code-arch-remediation/scripts/function_inventory.py --confirm 'services/accelerator_api/routes/profile_routes.py::migration_scan_profile:bool_flag' --rationale "…" --decided-by operator`,
and one row is appended to `exception-register.md`. Cap check: the register holds **27 rows**
against a cap of **29** (`exception-register.md:14-16`), so this fits with one slot left — and
recommending removal in item 6 keeps that slot free. No test is required beyond the existing
`tests/profile/test_profile_approval_flow.py:70`. **Snapshot: no. Contract: no.**

*B — replace with `mode: ScanMode` (`"background" | "inline"`).* Changes the route's query
schema, so every caller moves: `form_routes.py:157`, `:443`, `test_profile_approval_flow.py:70`,
and the docstring. New test `tests/profile/test_gap_NNN_scan_mode_param.py`, where `NNN` is the
next free register id at the time (GAP-055 unless item 6 takes it first). **Snapshot: no** —
`http_routes` records `accelerator POST
/v1/settings/profiles/{profile_id}/scan` with no query string. **Contract: yes**, under FR-006's
"HTTP route … payload shapes", so this option needs its own plan entry. Practical obstacle:
`profile_routes.py` is **795 lines** against the 800-line cap in
`tests/unit/test_file_size_limit.py:12`, and an enum declaration plus a widened signature does
not fit without splitting the module first.

**Safeguards.** None of CA-001..CA-004 applies: the parameter selects inline versus background
execution of a read-only discovery scan.

**Recommendation: A.** It is caller-supplied wire data with three live callers and an accurate
docstring, and `profile_routes.py` has five lines of headroom before the file-size gate — an
enum cannot land here without a module split that is out of scope.

---

## 6. `scan: bool = False` on `GET /v1/settings/cloud-credentials`

**Question.** Remove the parameter and its branch, or keep it as `bool_data` with an exception
row?

**Context.** `services/accelerator_api/routes/settings_routes.py:443` —
`list_cloud_credentials(request: Request, scan: bool = False)`, with `if scan:
_cloud_credentials.scan()` at `:474-475`; the other two ruff CI failures are here
(`run-t095-local-ci.txt:34,43`). Its own docstring (`:451-455`) says the same probe is available
as `POST /v1/settings/cloud-credentials/scan`, "which is the one to use when the probe itself is
the point — **unlike this flag, it writes an audit record**". That is exactly right: the POST at
`:479-509` calls `_cloud_credentials.scan()` then `write_profile_audit("cloud_credentials.scanned", …)`
at `:503-508`; the GET flag runs the identical host probe and writes nothing.

The console uses both. `apps/migration-ui/src/lib/cloudCredentials.ts:76-79` exposes
`fetchCloudCredentials(scan: boolean = true)` and
`apps/migration-ui/src/app/settings/cloud-credentials/page.tsx:165` calls it with `true` on page
load, so every page view and every `refetch()` (`:220`, `:253`) silently re-probes the host
unaudited. The page **already has a Rescan button** wired to the POST (`page.tsx:169-170,
209-212`).

**Options.**

*A — remove the parameter and its branch.* Delete `scan` from the signature at `:443` and the
`if scan:` branch at `:474-475`, and the `Args:` line at `:459-460`; drop the `scan` parameter
from `cloudCredentials.ts:76-79` and the query-string concatenation at `:78`; change
`page.tsx:165` to `fetchCloudCredentials()`; delete the now-false assertion at
`apps/migration-ui/src/lib/cloudCredentials.test.ts:9-15` and replace it with one asserting no
query string. Behaviour: the page loads the last recorded detection, and the operator clicks
Rescan — which is audited — to refresh. The signature entry, the two `Args:` lines, the
two-line branch and the five-line docstring paragraph that documents the flag all go, so
`settings_routes.py` sheds roughly nine of its **798** lines — useful headroom against the
800-line cap. Test: `tests/cloud_credentials/test_gap_055_listing_does_not_probe.py` asserting
the GET performs no probe and the POST still does. **Snapshot: no**
(`accelerator GET /v1/settings/cloud-credentials` is unchanged as a path). **Contract: yes**
under FR-006 payload shapes. This is an inventory `bool_flag` proposal rather than a register
gap; GAP-055 is the next free id if the operator wants a register entry to point the test at.

*B — `bool_data` + `# noqa` + exception row.* Same mechanics as item 5A. Costs the last free
exception slot (28 of 29 after item 5), keeps an unaudited duplicate of an audited endpoint,
and adds lines to a file two under the size cap.

**Safeguards.** CA-004 decides it: `scan=true` performs a host-probing state observation with
no audit record while the sibling POST writes one, so removal moves every probe onto the
audited path. CA-003 is satisfied either way — neither route reads or returns a credential
value (`:446-449`).

**Recommendation: A.** The POST already does the same probe *with* an audit record and the
console already has a button for it, so the flag is an unaudited duplicate; removing it also
keeps the last exception slot free for item 5 and buys back four lines under the file-size cap.

**Plan entry:**

> **12. `GET /v1/settings/cloud-credentials` no longer accepts `scan` (GAP-055). Decision
> (operator, 2026-09-13): approved.** The listing endpoint accepted `?scan=true` to re-probe the
> host for cloud credential sources before answering. It performed exactly the probe that
> `POST /v1/settings/cloud-credentials/scan` performs, but wrote no audit record, so the console's
> page load — `fetchCloudCredentials(true)` on every render and every refetch — was probing the
> host outside the audit trail (CA-004). The parameter and its branch are removed from
> `services/accelerator_api/routes/settings_routes.py` with no shim (FR-006a);
> `apps/migration-ui/src/lib/cloudCredentials.ts` drops the argument and the console's existing
> Rescan button, already wired to the audited POST, becomes the only way to re-probe.
>
> *Migration note.* A client still sending `?scan=true` gets the listing without a probe —
> FastAPI ignores an undeclared query parameter, so nothing errors, but the response reflects the
> last recorded detection rather than a fresh one. Any caller that relied on the flag must issue
> `POST /v1/settings/cloud-credentials/scan` first, which needs the same `can_manage_models`
> capability and additionally writes a `cloud_credentials.scanned` audit event.
>
> *Snapshot impact: none.* The route path and method are unchanged; query parameters are not
> recorded in `http_routes` and are not among the four frozen keys.

---

## 7. Rename `services/accelerator_api/routes/_shared.py`

**Question.** Rename the module, or override the pending `module_name_review` proposal and
keep the name?

**Context.** 336 lines. Its docstring reads "Shared state and helpers for accelerator API route
modules", and that is what it holds: five lazy patch-friendly wrappers around credential and
scan functions (`:36-123`), the process-wide singletons `_settings`, `_runner`, `_llm_models`,
`_cloud_credentials`, `_connectivity` (`:199-208`), per-request helpers `_platform_user` (`:147`)
and `_require_admin` (`:159`), profile lookups `_require_profile` (`:210`) and
`_require_active_profile` (`:228`), the two approval executors `_execute_approved_migrate`
(`:254`) and `_execute_approved_pipeline` (`:268`), and `advanced_settings_as_dict` (`:325`).
Unlike item 9, this module has **no reviewer decision at all** in `tag-decisions.json` — it is an
unexamined generator proposal.

Importers (11 production, 3 test): `services/accelerator_api/main.py:86`,
`routes/approval_routes.py:22`, `routes/migrate_guard.py:16`, `routes/migrate_routes.py:20`,
`routes/migrate_scope.py:25`, `routes/pipeline_routes.py:34`,
`routes/profile_credential_routes.py:24-25`, `routes/profile_routes.py:26-27`,
`routes/settings_routes.py:20`; `tests/auth/test_gap_002_auth_disabled_bypasses_live_approval.py:61`,
`tests/auth/test_gap_005_coordinator_bypasses_live_approval.py:51`,
`tests/pipeline/test_gap_004_client_self_certified_live_approval.py:53`.

**Options.**

*A — rename.* Two concrete candidates: **`route_context.py`** (what a handler needs to answer a
request: the stores, the caller's identity, the profile) or **`shared_stores.py`** (the literal
dominant content — the five singletons every route module imports `_settings` for). Mechanics:
`git mv`, rewrite the 14 import sites, and append a row to `docs/STRUCTURAL_CHANGELOG.md` in
the same change (`| File Path | New Path | Change Type | Reason | Verified | Test Status | Timestamp |`).
**Snapshot: no** — module paths are explicitly internal under FR-006 and Q/A line 44 of `spec.md`.
No test change beyond the three import lines; `tests/unit/test_no_orphaned_modules.py` builds its
graph dynamically and needs no allowlist edit, because this module is statically imported.

*B — override and keep.* `python specs/013-clean-code-arch-remediation/scripts/function_inventory.py
--reject 'services/accelerator_api/routes/_shared.py:module_name_review' --rationale "…"
--decided-by operator`, which appends one row to `tag-decisions.json` and clears the pending
proposal. No code, no imports, no changelog row.

**Safeguards.** None apply; this is a pure rename with no behaviour change either way.

**Recommendation: B.** The docstring and the contents agree, no reviewer contested the name,
and renaming churns 14 import sites for zero behaviour change — record the rejection and move on.

---

## 8. GAP-054 — `JobStore.complete()`/`.fail()` assign a field `JobRecord` does not declare

**Question.** Add `created_at`/`updated_at` to `JobRecord`, or work around the missing fields?

**Context.** `ado2gh/models.py:501-510` declares `JobRecord` with `id`, `job_type`, `status`,
`payload`, `result`, `error`, `idempotency_key` and nothing else. Measured live this session:
`rec.updated_at = …` raises `ValueError: "JobRecord" object has no field "updated_at"`, while
`model_copy(update={...})` succeeds, sets the attribute, and leaves `model_dump()` keys unchanged
(`['error','id','idempotency_key','job_type','payload','result','status']`).

The kwargs are passed at six construction sites — `ado2gh/state/job_store.py:109`, `:185`
(SQLite), `:257`, `:330` (Postgres), `:407`, `:432` (Dynamo) — and silently dropped in every
one. SQLite and Postgres survive because they write their own columns directly (`:102`,
`:120-123`). DynamoDB does not: `_save` (`:386-399`) reads `record.created_at.isoformat()` at
`:396`, so `enqueue` raises `AttributeError` on the first write, before `complete()` (`:537-545`)
or `fail()` (`:547-555`) are reached. `ado2gh/core/orchestration/worker.py:116-122` makes it
fatal: `store.complete(...)` in a `try`, `store.fail(...)` in the paired `except`, nothing
wrapping the fallback, so the fallback's own exception escapes `run_worker`'s loop. `boto3` is
neither installed nor declared in `pyproject.toml`, so the backend cannot be constructed here.

The serialized record reaches `ado2gh/api/contracts.py:138-141` (`JobStatusResponse.job`),
returned by `services/accelerator_api/main.py:751-799` on `POST /v1/jobs` and
`GET /v1/jobs/{job_id}`. A repo-wide search of `apps/migration-ui/src/` for `v1/jobs` returns
nothing — **no console consumer** — and no test asserts an exact key set.

**Options.**

*A — add the two fields.* `created_at`/`updated_at` with UTC `default_factory` on
`ado2gh/models.py:501-510`. `_save` and `_load` then work exactly as written, and all **ten**
`# type: ignore` comments in `job_store.py` (six `[call-arg]`, four `[attr-defined]`) are
deleted rather than permanently suppressed. Test:
`tests/unit/test_gap_054_job_record_timestamps.py` — a dict-backed fake table with
`put_item`/`get_item` (since `boto3` is absent) driven through `enqueue → claim → complete` and
`→ fail`, asserting no exception, that `created_at` survives the round trip, and that `fail()`
after a raising `complete()` still records the failure; plus one `SQLiteJobStore` round-trip
case. **Snapshot: no** (both `/v1/jobs` paths unchanged). **Contract: yes** — `model_dump()`
gains two keys on every backend's job payload, an FR-006 message-shape change.

*B — `model_copy(update=…)` in `complete()`/`fail()`.* Measured to work and to leave
`model_dump()` untouched, so zero contract change. It fixes only the two `ValueError`s;
`enqueue`'s `AttributeError` at `_save`:396 remains, so no job can be enqueued and the gap is
not closed. Incomplete as stated.

*C — keep the model, thread `created_at` through `_save`.* Also zero contract change, but
`complete()`/`fail()` reach `_save` via `self.get(job_id)` → `_load`, which would no longer
carry `created_at`, so every update would reset the creation time to now — a data defect traded
for a type defect.

**Safeguards.** CA-003 holds: the fields are timestamps and the exceptions name a field, never
a value. CA-004 improves — a job that today kills the worker is recorded as failed instead.

**Recommendation: A.** It is the only option that makes `_save`/`_load` correct as written and
lets `created_at` round-trip, and the cost is two extra keys on a response with no console
consumer and no key-set assertion anywhere.

**Plan entry:**

> **13. `JobRecord` gains `created_at` and `updated_at` (GAP-054). Decision (operator,
> 2026-09-13): approved.** Every job store already passed both timestamps to the `JobRecord`
> constructor and `DynamoDBJobStore._save` already read `record.created_at`, but the model
> declared neither field, so pydantic dropped them at construction and raised on assignment.
> `DynamoDBJobStore.enqueue` therefore failed on its first write, and `complete()`/`fail()` each
> raised `ValueError` — which, through the worker's catch-then-`fail()` fallback in
> `ado2gh/core/orchestration/worker.py`, killed the worker process instead of recording one
> failed job. `ado2gh/models.py` now declares both fields with UTC `default_factory` defaults,
> and the ten `# type: ignore` comments added across `ado2gh/state/job_store.py` to suppress the
> symptom are removed.
>
> *Migration note.* The job payload returned by `POST /v1/jobs` and `GET /v1/jobs/{job_id}` gains
> two ISO-8601 timestamp keys, `created_at` and `updated_at`. No in-repo client reads these
> routes; an external client that asserts an exact key set on the job object must be widened.
> No stored schema changes — the SQLite and Postgres `jobs` tables already carry both columns,
> and this change only stops discarding them on the way out.
>
> *Snapshot impact: none.* Both route paths are unchanged, no table is added or renamed, and
> model fields are not among the four frozen keys.

---

## 9. Rename `services/agent/routes/_helpers.py`

**Question.** Rename the module, or override T079's prescribed name and keep it?

**Context.** 599 lines. Its own docstring claims the file holds helpers "needed by route
handlers but not specific to LangGraph agent logic", and the reviewer's recorded decision in
`tag-decisions.json` (`decision: "confirm"`, `decided_by: "cavecrew-reviewer"`) disagrees with
that claim in terms: "file mixes trivial accessors with substantial migration-plan-building and
live-approval/PEV-start business logic … the helpers name undersells what a reader needs to
know here". The substance backing that: `_build_migration_plan` (`:432`),
`_enqueue_session_live_approval` (`:544`), `_try_start_pev_run` (`:568`), the session registries
`_runs` and `_sessions` (`:48-49`), the audit bridge `_audit` (`:50`), the accelerator HTTP
client (`:154-200`) and the auth guards (`:203-245`).

Importers (9 production, 2 test): `ado2gh/agents/migration_agent/session/lifecycle.py`,
`services/agent/main.py`, and `services/agent/routes/{execution,form,message,model,plan,run,session}_routes.py`;
`tests/contract/test_gap_015_work_item_field_contract.py`, `tests/unit/test_thinking_isolation.py`.

**Options.**

*A — rename.* Two concrete candidates: **`session_runtime.py`** (the session and run registries
plus everything that advances a session — plan building, live-approval enqueue, PEV start) or
**`agent_route_services.py`** (honest about it being the services layer the route modules sit on).
Mechanics as in item 7: `git mv`, rewrite 11 import sites, one `docs/STRUCTURAL_CHANGELOG.md`
row. **Snapshot: no.** No test change beyond the two import lines.

*B — override and keep.* `--reject 'services/agent/routes/_helpers.py:module_name_review'
--rationale "…" --decided-by operator`, which supersedes the reviewer's `confirm` with the
operator's decision. T079 prescribed this name deliberately when it moved the module out of
`ado2gh/agents/migration_agent/route_helpers.py`, so keeping it is a defensible standing
decision rather than an oversight.

**Safeguards.** None apply.

**Recommendation: B, with a follow-up filed.** A rename does not fix what the reviewer actually
objected to — `_build_migration_plan`, `_enqueue_session_live_approval` and `_try_start_pev_run`
being business logic in a helpers module — so record the override and file the extraction of
those three functions as a separate follow-up; `session_runtime.py` becomes the right name only
once it holds only that.

---

## 10. The leftover revert-proof stash

**Question.** Confirm the working tree is intact and drop `stash@{0}`?

**Context.** `git stash list` shows one entry:
`stash@{0}: a5fbb01 test(GAP-012): commit the console regression test for the catalog API key query string`.
`git stash show --stat stash@{0}` reports six paths: five one-line production diffs plus a
zero-line rename entry (`ado2gh/api/agentic_routes.py` → `services/accelerator_api/routes/history_routes.py`,
100 % similarity), which is an index artefact of the T079 move, not a change. The five diffs are
the deliberate sabotage used for the T082 revert proofs of GAP-017, GAP-018, GAP-030 and
GAP-032: `if False and request.live_approval_id` in `ado2gh/api/accelerator.py`,
`checker.check(phase, override=override, reason=reason)` in `ado2gh/cli/phase.py`, a token-bearing
`target_url` in `ado2gh/core/scopes/git_scope.py`, and `if not v.is_secret` in both
`ado2gh/pipelines/transform/job_graph.py` and `.../transformer.py`.

Verified this session: `git diff --stat` on all five paths returns empty, and the tree holds the
fixed forms — `ado2gh/api/accelerator.py:186` `if request.live_approval_id:`,
`ado2gh/pipelines/transform/transformer.py:456` and `.../job_graph.py:204` both `if v.is_secret:`.
The stash is a pure leftover; prior sessions could not drop it because `git stash drop` was
blocked by the session's permission classifier.

**Commands.**

```bash
git stash show --stat "stash@{0}"                     # six paths, 5 insertions / 5 deletions
git stash show -p "stash@{0}"                         # confirm the five sabotage lines
git diff --stat -- ado2gh/api/accelerator.py ado2gh/cli/phase.py \
  ado2gh/core/scopes/git_scope.py ado2gh/pipelines/transform/job_graph.py \
  ado2gh/pipelines/transform/transformer.py           # must print nothing
git stash drop "stash@{0}"
git stash list                                        # must print nothing
```

**Safeguards.** CA-003: the `git_scope.py` diff embeds a token *expression*, not a value, so the
`-p` output is safe to read; do not paste it into a report. Dropping the stash destroys nothing
recoverable — the five reverts exist only to be thrown away, and the rename is already committed.

**Recommendation: drop it,** once `git diff --stat` on the five paths prints nothing.

---

## 11. `test_only_scripts_dev_remains` fails on the operator's untracked bridge scripts

**Question.** Commit the Codex/Claude bridge scripts and widen the test, or move them out of the
repository?

**Context.** `tests/unit/test_scripts_cleanup.py:18-19` asserts
`len(dev_files) <= 3` for `scripts/dev/`. `git ls-files scripts/dev/` returns exactly three
tracked files (`_local-common.ps1`, `run-local-agent.ps1`, `run-ui.ps1`), but the directory on
disk holds seven: the operator's untracked `claude-mcp-bridge.mjs`, `claude-mcp-bridge.test.mjs`,
`codex-mcp-bridge.mjs`, `codex-mcp-bridge.test.mjs`. `docs/AGENT_MCP.md` is untracked too, and
the uncommitted working-tree `CLAUDE.md:27` and `AGENTS.md:20` both link to it —
`git show HEAD:CLAUDE.md` contains no such reference, so the dangling link is confined to the
uncommitted T085 edits. This is the **only** failing test in T095's local CI reproduction
(`run-t095-local-ci.txt:87-88`: 1 failed, 1019 passed, 30 skipped) and it is a local-only
artefact: in a clean CI checkout the four files are absent and the test passes.

`.gitignore` is locally modified to add `.specify/*`, `specs/*`, `CLAUDE.md`, `AGENTS.md`
(and `.claude/`, `.agents/`), which is why anything under `specs/` needs `git add -f`.

**Options.**

*A — commit them and widen the test.* `git add -f scripts/dev/*.mjs docs/AGENT_MCP.md`, then
replace the count assertion at `tests/unit/test_scripts_cleanup.py:19` with a name allowlist
(`_local-common.ps1`, `run-local-agent.ps1`, `run-ui.ps1`, the four bridge files) — the count was
only ever a proxy for "no stray scripts", and a name list says that directly and does not need
editing again the next time a legitimate file is added. Commit `CLAUDE.md` and `AGENTS.md` in the
same change so the `docs/AGENT_MCP.md` link is not dangling for other contributors. Note the
coupling with item 12: if CI is enabled *before* this test is widened, committing the four files
turns the currently-green pytest job red.

*B — move them out of the repository.* `mv scripts/dev/*-mcp-bridge*.mjs ~/tools/` (or anywhere
outside the checkout), leave the test untouched, and either drop `docs/AGENT_MCP.md` or commit it
with the paths rewritten. Zero test edit, zero repository growth; the bridge stops being
reproducible for anyone else, and `CLAUDE.md`'s instruction to use the `chatgpt` MCP server
becomes unactionable from a clean clone.

**Safeguards.** CA-003: before committing, confirm the four `.mjs` files carry no token, key or
endpoint credential — they are launcher shims, but they are also the one thing here that talks to
an external service.

**Recommendation: A.** `CLAUDE.md` already documents the bridge as project workflow and links to
`docs/AGENT_MCP.md`, so the files belong in the repository; swap the count cap for a name
allowlist in the same commit.

---

## 12. CI has never run for this branch

**Question.** Open a pull request, add a manual/branch trigger to `ci.yml`, or both?

**Context.** `.github/workflows/ci.yml:3-6` declares only:

```yaml
on:
  push:
    branches: [main, master]
  pull_request:
```

No `workflow_dispatch`, no `feature/**` push trigger. And the workflow file is not on the
default branch: `git ls-tree -r --name-only origin/main -- .github/` returns only
`.github/workflows/migrate-repo.yml`, so the `push` trigger can never fire for it.
`gh pr list --state all --head feature/ado-agentic-ai` returns nothing — no PR exists (the most
recent PRs in the repository, #2–#6, all merged in April). The net effect is that
`ci.yml` has never executed, and T095's instruction to "confirm CI green" is currently
uncheckable.

T095 reproduced every CI command locally (`run-t095-local-ci.txt`). Expected first run:

| Job / step | Expected | Evidence |
|---|---|---|
| `lint` — ruff `E9,F821` gate | green | `:6-7` "All checks passed!" |
| `lint` — `ruff check ado2gh/ services/` | **red, 4 errors** | `:11-51` — FBT001+FBT002 at `profile_routes.py:278:5` (item 5) and `settings_routes.py:443:46` (item 6) |
| `lint` — `mypy ado2gh/ --ignore-missing-imports` | green | `:55` "no issues found in 195 source files" |
| `test` — `pytest --cov=ado2gh --cov-fail-under=61` | green in CI | `:84-88` 61.49 % ≥ 61; the one local failure, `test_only_scripts_dev_remains`, cannot occur in a clean checkout (item 11) |

So the ruff step stays red until items 5 and 6 are decided and applied, and everything else is
green on Python 3.11.

**Options.**

*A — open a draft PR `feature/ado-agentic-ai` → `main`.* The intended path: `pull_request` is
already declared, and for a same-repo PR GitHub runs the workflow as it exists on the merge of
the head branch, so `ci.yml` executes from this branch without being on `main` first. Zero file
change. `gh pr create --draft --base main --head feature/ado-agentic-ai`. Gives the feature its
review surface as well as its CI.

*B — add triggers to `ci.yml`.* A one-line-per-trigger edit at `.github/workflows/ci.yml:3-6`:
insert `  workflow_dispatch:` after line 3, and/or add `feature/**` to the branch list at line 5
(`branches: [main, master, 'feature/**']`). Runs on every push without a PR; `workflow_dispatch`
additionally gives a manual re-run button. Costs a workflow-file diff that ships to `main`.

*C — both.* Draft PR for the review surface, `workflow_dispatch` for the manual re-run.

**Safeguards.** None of CA-001..CA-004 applies. Note only that the runners need no secrets for
these four jobs — nothing in `ci.yml` touches ADO or GitHub credentials.

**Recommendation: C.** The draft PR is the path the existing `pull_request` trigger was written
for and costs no file change, and `workflow_dispatch` is one line that makes a re-run possible
without pushing a commit — expect the ruff step red until items 5 and 6 land.

---

## 13. Four contract changes still listed as "awaiting sign-off" although their fixes shipped

**Question.** Sign off the four changes already applied in Phase 5, or reconsider any of them?

**Context.** `plan.md:269-279` and `contracts/public-contract-freeze.md:261-380` list four
changes as present in the working tree but unapproved. All four are in HEAD, verified by
`git log --grep` and by reading the code:

| # | Change | GAP | Commit | Verified in tree |
|---|---|---|---|---|
| 5 | Nine `/v1/migrate/*` routes gain a live-execution guard | GAP-007 | `36edbc3` | `services/accelerator_api/routes/migrate_guard.py` exists and hangs `guard_live_migration` off the `/v1/migrate` router |
| 6 | GitHub write proxy requires `can_approve_live_execution` | GAP-008 | `a6309ea` | `services/accelerator_api/routes/proxy_routes.py` split by verb |
| 7 | Agent `/v1/internal/` fails closed without `ADO2GH_INTERNAL_TOKEN` | GAP-003 | `8285f0c` | `services/agent/main.py:100-103` reads the variable and warns when unset |
| 8 | `phase gate-check --override` rejects an empty `--reason` | GAP-017 | `ceb6b0b` (fix), `aefea23` (test) | `ado2gh/cli/phase.py:166-168` raises `click.UsageError`; `tests/unit/test_gap_017_gate_check_signature.py` |

**What the operator is signing off**, per change: for **5**, that an unauthenticated live
request to any `/v1/migrate/*` route now gets 401 and an OPERATOR/COORDINATOR gets 403 with an
`awaiting_approval` `approval_id` instead of proceeding — dry-run requests pass through
unchanged (`freeze:291-305`). For **6**, that GitHub proxy writes (POST/PATCH/PUT/DELETE) now
require `can_approve_live_execution` and are audited before forwarding, with reads unaffected
(`freeze:319-333`). For **7**, that `ADO2GH_INTERNAL_TOKEN` becomes mandatory wherever
`ADO2GH_AUTH_ENABLED=true`, and that a deployment which forgets it will see approvals accepted
in the console while the run silently never resumes (`freeze:353-368`). For **8**, that
`--override` with an empty `--reason` is now a clean `click.UsageError` where it previously
raised `TypeError` and wrote no gate row (`freeze:372-378`).

**Snapshot impact: none, for all four.** GAP-007 and GAP-008 change who gets an answer, not any
path; GAP-003's `ADO2GH_INTERNAL_TOKEN` is already in the frozen `env_vars` list; GAP-017 adds,
removes and retypes no CLI option. That is the freeze document's own assessment
(`freeze:267-272`), and it matches the four frozen keys in
`tests/contract/public_surface_snapshot.json` (96 `cli_commands`, 25 `db_tables`, 68 `env_vars`,
148 `http_routes`).

**Options.** *A — sign off all four.* They are applied, tested and unreverted; three are
security fixes the plan already states are not revertible. *B — sign off selectively.* Only
GAP-017 is genuinely optional; reverting it restores a `TypeError` on empty `--reason`.

**The edits.** In `contracts/public-contract-freeze.md`, change each `**Status: awaiting
operator sign-off.**` line (`:307`, `:335`, `:370`, `:380`) to
`**Status: approved (operator, 2026-09-13).**`, change the four `**No — needs sign-off**` cells
in the table at `:269-272` to `Yes — 2026-09-13`, and move the section heading's four entries
into § Approved contract changes as entries 5–8. In `plan.md`, replace the paragraph at
`:269-279` with a one-line note and append the four as plan entries **5–8**, each ending with
`*Snapshot impact: none.*` and the justification from the table above; the new entries from
items 1, 2, 3, 6 and 8 of this memo then take numbers **9–13**.

**Recommendation: A.** All four are applied, tested, and drift no frozen key, and three of them
are the security fixes the feature exists for — leaving them formally unapproved is the only
thing still making them look optional.

---

## Summary

| # | Item | Recommendation | Contract change | Effort |
|---|------|----------------|-----------------|--------|
| 1 | GAP-018 `--dry-run` default | Flip all three to `--dry-run/--live`, default True | **Yes** (entry 9; snapshot: 3 lines) | M |
| 2 | GAP-019 client-supplied `actor` | Bind `Request`, derive server-side, delete the field | **Yes** (entry 10; snapshot: none) | S |
| 3 | GAP-024 stringified boolean | JSON booleans end to end, plus the `:195` fallback | **Yes** (entry 11; snapshot: none) | S |
| 4 | GAP-031 variable groups | Notes section only, names never values | No | S |
| 5 | `sync: bool` on profile scan | `bool_data` + `noqa` + exception row (28/29) | No | S |
| 6 | `scan: bool` on cloud-credentials | Remove the parameter and its branch | **Yes** (entry 12; snapshot: none) | S |
| 7 | `_shared.py` rename | Override and keep; record `--reject` | No | S |
| 8 | GAP-054 `JobRecord` timestamps | Add `created_at`/`updated_at`, drop 10 ignores | **Yes** (entry 13; snapshot: none) | M |
| 9 | `_helpers.py` rename | Override and keep; file the logic extraction as follow-up | No | S |
| 10 | Leftover stash | Verify clean, then `git stash drop "stash@{0}"` | No | S |
| 11 | `scripts/dev` test failure | Commit the bridge scripts; swap the count cap for an allowlist | No | S |
| 12 | CI never runs | Draft PR **and** `workflow_dispatch` | No | S |
| 13 | Four unsigned contract changes | Sign off all four as plan entries 5–8 | Records 4 existing | S |
