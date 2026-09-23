# Migration knowledge base

Reference for `ado2gh/knowledge/`, the part of the platform that records what
a migration touches — repositories, pipelines, the service connections and
variable groups a pipeline needs, the environments it deploys to — and how
those things depend on one another. It answers one question either a person
or the agent can ask: if this changed, what else would be affected? See
[ARCHITECTURE.md](ARCHITECTURE.md#migration-knowledge-base) for how this fits
next to the rest of the state database.

Everything it knows comes from the `pipeline_inventory` rows that
`ado2gh pipelines inventory` already collected. Building or refreshing the
knowledge base makes no call of its own to Azure DevOps or GitHub — it only
reads what an earlier scan already stored.

## What it records

A **node** is one thing a migration touches. `ado2gh/knowledge/models.py`
defines thirteen kinds of thing, one plain sentence each:

| Kind | What it is |
|------|------------|
| `repository` | A git repository, in the source or the target platform. |
| `pipeline` | A pipeline definition — YAML or classic build. |
| `release_pipeline` | A classic release pipeline definition. |
| `artifact_feed` | An Azure Artifacts package feed. |
| `service_connection` | A stored credential a pipeline authenticates through. |
| `variable_group` | A named set of pipeline variables, some of them possibly secret. |
| `secure_file` | A file uploaded to the pipeline library rather than kept in a repository. |
| `environment` | A deployment target a pipeline deploys to, such as staging or production. |
| `agent_pool` | A pool of build agents a pipeline's jobs run on. |
| `container_image` | The container image a pipeline's job runs inside. |
| `wiki` | A project wiki. |
| `work_item_project` | The project a set of work items belongs to. |
| `test_plan` | A test plan. |

There is also an internal `unknown` kind used only as a placeholder while
walking dependencies — a scan never records a node with that kind.

**Only some of these are actually produced today.** Reading the pipeline
inventory can only ever yield `repository`, `pipeline`, `service_connection`,
`variable_group`, `environment`, `agent_pool` and `artifact_feed` — the seven
kinds a build pipeline's own metadata can name. `release_pipeline`,
`secure_file`, `container_image`, `wiki`, `work_item_project` and `test_plan`
are modelled for a future extractor that reads something other than the
pipeline inventory; nothing populates them yet.

A **dependency** (an edge, in `ado2gh/knowledge/models.py`) is always written
from the consumer to the thing it relies on — a pipeline needs a service
connection, not the other way round. There are twelve kinds:

| Kind | What it is | Derived today? |
|------|------------|-----------------|
| `builds` | The pipeline builds this repository. | Yes |
| `extends_template_in` | The pipeline extends a YAML template kept in this repository. | Yes |
| `consumes_artifact_of` | The pipeline downloads build artifacts produced by this other pipeline. | Yes |
| `consumes_feed` | The pipeline reads or publishes packages through this artifact feed. | Yes |
| `needs_credential` | The pipeline authenticates through this service connection. | Yes |
| `needs_variable_group` | The pipeline reads its settings from this variable group. | Yes |
| `deploys_to` | The pipeline deploys to this environment. | Yes |
| `runs_on_pool` | The pipeline runs its jobs on this agent pool. | Yes |
| `checks_out` | The pipeline checks out this repository in addition to the one it builds. | No — see Limits |
| `triggered_by` | The pipeline starts when this other pipeline or resource completes. | No — see Limits |
| `needs_secure_file` | The pipeline needs this secure file at run time. | No — see Limits |
| `uses_container` | The pipeline's job runs inside this container image. | No — see Limits |

## Confidence

Every dependency carries one of three confidence levels
(`ado2gh/knowledge/models.py`, `Confidence`):

- **`declared`** — the pipeline definition named the thing outright (a
  variable group listed by name, an environment named in a deployment job).
- **`resolved`** — a name in the pipeline matched something this same scan
  also read and could identify unambiguously (a pipeline downloading
  artifacts from another pipeline it recognised by name).
- **`inferred`** — a heuristic matched a name in free text without being able
  to confirm it (a service connection reference found in task inputs; the
  stored inventory row does not say which heuristic matched, so every
  `needs_credential` dependency is `inferred`).

`inferred` is a guess and can be wrong. Nothing in this platform ever shows
an inferred dependency as though it were declared, and both the read routes
and the agent tools carry the confidence on every dependency they return so a
reader can judge how much weight to put on it.

## Running a scan

Two things fill the knowledge base today, and neither makes a call of its own
to Azure DevOps or GitHub — both read rows that `ado2gh pipelines inventory`
already collected.

A real pipeline inventory run builds it as the last step of that run, for
whichever migration profile is active when the run finishes
(`ado2gh/pipelines/inventory.py`, `PipelineInventoryBuilder._record_knowledge`).
A preview run builds nothing, because a preview changes nothing else this
platform keeps either. The organisation a thing is recorded under is read the
same way here as it is by the on-request rebuild described below — the active
profile's Azure DevOps organisation address — so the same pipeline is never
recorded twice under two identifiers depending on which of the two ways built
it.

Filling the knowledge base can never cost the inventory run the rows it just
collected: no active migration profile, a state store that cannot be read, or
a derivation that raised are all caught and reported in plain words on the
inventory run's own summary, under a key named `_knowledge` — a name that
starts with an underscore so it is never mistaken for an Azure DevOps project,
and the organisation-wide pipeline totals skip it for the same reason. A
failed or skipped build is reported; it never fails the inventory run itself.

An operator who wants the knowledge base rebuilt without re-reading Azure
DevOps can ask for it directly: **`POST /v1/knowledge/scan`**, described
below, reads the same stored inventory rows on request.

A scan — however it was started — reads every `pipeline_inventory` row for
the profile, derives what it
can from each, and finishes by marking any dependency an earlier scan
recorded but this one did not see again as `disappeared` rather than
deleting it. What happened is written to a `knowledge_scans` row, including a
`coverage` dictionary (`ado2gh/knowledge/builder.py`, `_Derivation.close`)
with these fields:

| Field | Meaning |
|-------|---------|
| `source` | Always `pipeline_inventory` — where the facts came from. |
| `rows_read` | How many inventory rows the scan looked at. |
| `rows_unreadable` | Rows the scan could not make sense of at all. |
| `pipelines_with_unreadable_text` | Pipelines whose stored YAML could not be parsed for feed and artifact references. |
| `pipelines_without_stored_text` | Pipelines with no YAML stored at all — usually collected before this feature existed, or a pipeline type inventory does not read the text for. |
| `nodes_recorded` | Nodes written or refreshed this pass. |
| `edges_recorded` | Dependencies written or refreshed this pass. |
| `edges_by_kind` | The same count broken down by dependency kind. |
| `edges_marked_disappeared` | Dependencies an earlier scan recorded that this one no longer sees. |
| `edge_kinds_not_attempted` | The four kinds this builder never tries to derive — see Limits. |

Every answer the store or the routes give repeats this coverage and a list of
caveats. That is deliberate: a knowledge base built from one project's
inventory and one built from an entire organisation's both answer an
unfamiliar question with an empty list, and only the coverage tells a reader
which kind of empty it is. When the store has nothing more specific to say it
falls back to one standing caveat: "This answer covers only dependencies
recorded by the scans listed under coverage. A thing with no recorded
dependency may still have one that was never scanned, or one expressed in a
form this platform does not read."

## Reading it — service routes

`services/accelerator_api/routes/knowledge_routes.py` exposes five routes
under `/v1/knowledge`: four read-only ones, plus one that rebuilds the
knowledge base on request. All five require the `can_operate` capability (the
same one guarding the rest of the inventory reads) and answer only from the
caller's active migration profile — with no active profile they respond
`400 No active migration profile`. On the four read routes, an identifier the
knowledge base has never seen is not an error: the dependency, consumer and
impact routes answer with empty lists (the impact route with a null subject),
on the reasoning that not knowing about a thing is an ordinary state for a
knowledge base built from partial scans. Asking for a kind that does not
exist, on either the node or the dependency vocabulary, is the one case that
does fail, with `400 Unknown NodeKind value: <name>` or the equivalent for
`EdgeKind`.

**`GET /v1/knowledge/search`** — find things by name.

```
GET /v1/knowledge/search?text=payments&limit=20
```

```json
{
  "text": "payments",
  "kinds": [],
  "count": 2,
  "results": [
    {
      "node_id": "node-a1b2c3",
      "kind": "repository",
      "name": "payments-api",
      "identity_key": "contoso/payments/payments-api",
      "system": "azure_devops",
      "organization": "contoso",
      "project": "payments",
      "url": ""
    },
    {
      "node_id": "node-d4e5f6",
      "kind": "pipeline",
      "name": "payments-api-ci",
      "identity_key": "contoso/payments/payments-api-ci",
      "system": "azure_devops",
      "organization": "contoso",
      "project": "payments",
      "url": ""
    }
  ],
  "coverage": { "source": "pipeline_inventory", "rows_read": 40, "...": "..." },
  "caveats": ["..."]
}
```

**`GET /v1/knowledge/nodes/{node_id}/dependencies`** and the mirror-image
**`.../consumers`** — one hop out, in the named direction.

```
GET /v1/knowledge/nodes/node-d4e5f6/dependencies
```

```json
{
  "node_id": "node-d4e5f6",
  "direction": "dependencies",
  "count": 1,
  "neighbours": [
    {
      "node": { "node_id": "node-a1b2c3", "kind": "repository", "name": "payments-api", "...": "..." },
      "edge_kind": "builds",
      "confidence": "declared",
      "evidence": { "source": "inventory_repository_column" },
      "extraction_method": "inventory_repository_column",
      "source_locator": "pipeline_inventory:payments-api-ci",
      "status": "active",
      "last_seen_at": "2026-09-23T04:12:00Z"
    }
  ],
  "coverage": { "...": "..." },
  "caveats": ["..."]
}
```

**`GET /v1/knowledge/nodes/{node_id}/impact`** — prerequisites, consumers and
anything shared with other consumers, walked out to `max_depth` hops (default
2, never more than 4 — a request asking for more gets the ceiling instead of
a refusal, and the depth actually walked comes back in the response).

```
GET /v1/knowledge/nodes/node-a1b2c3/impact?max_depth=2
```

```json
{
  "subject": { "node_id": "node-a1b2c3", "kind": "repository", "name": "payments-api", "...": "..." },
  "max_depth": 2,
  "prerequisites": [],
  "consumers": [
    {
      "node": { "node_id": "node-d4e5f6", "kind": "pipeline", "name": "payments-api-ci", "...": "..." },
      "relation": "consumer",
      "distance": 1,
      "path": ["node-a1b2c3", "node-d4e5f6"],
      "weakest_confidence": "declared",
      "evidence": [{ "source": "inventory_repository_column" }]
    }
  ],
  "shared_dependencies": [],
  "coverage": { "...": "..." },
  "caveats": ["..."],
  "truncated": false
}
```

**`POST /v1/knowledge/scan`** — rebuild the knowledge base for the active
profile from the pipeline inventory already collected. This is the same build
a real pipeline inventory run performs on its own; this route lets an operator
ask for it directly, without waiting for another inventory run. Running it
twice over unchanged rows writes the same rows under the same identifiers, so
an operator unsure whether it ran may simply run it again.

```
POST /v1/knowledge/scan
```

```json
{
  "scan_id": "scan-9f8e7d",
  "status": "completed",
  "source_scope": "https://dev.azure.com/contoso",
  "coverage": {
    "source": "pipeline_inventory",
    "rows_read": 40,
    "rows_unreadable": 0,
    "pipelines_with_unreadable_text": 0,
    "pipelines_without_stored_text": 0,
    "nodes_recorded": 12,
    "edges_recorded": 18,
    "edges_by_kind": { "builds": 6, "needs_credential": 4, "...": "..." },
    "edges_marked_disappeared": 0,
    "edge_kinds_not_attempted": ["checks_out", "triggered_by", "needs_secure_file", "uses_container"]
  },
  "caveats": ["..."],
  "error_summary": ""
}
```

`error_summary` is empty on success and holds a plain-words description of
what went wrong when `status` comes back `failed`.

## Reading it — agent tools

`ado2gh/agents/migration_agent/tools/knowledge_tools.py` gives the planner two
tools. Both call the routes above through the accelerator — neither opens a
database itself — and both mask secret shapes in the response and then cap
its size, in that order, so a cap can never cut a masked value into an
unrecognisable fragment. The coverage and the caveats get more room than the
rest of the answer (four thousand characters and up to two hundred entries,
against four hundred characters and fifty entries for everything else),
because a caveat trimmed away is what would turn a qualified answer into a
confident-looking one.

**`knowledge_search(text, limit=10)`** — same search as the route above,
called as `GET /v1/knowledge/search?text=...&limit=...`. Ask it something
like:

```
knowledge_search(text="payments-api", limit=5)
```

and it returns the masked, size-capped version of the search response shown
above, with one field always added: a `note` explaining that an empty result
means nothing was recorded within the coverage shown, not that nothing
matching exists.

**`knowledge_impact(node_id, max_depth=2)`** — same walk as the impact route,
called as `GET /v1/knowledge/nodes/{node_id}/impact?max_depth=...`:

```
knowledge_impact(node_id="node-a1b2c3", max_depth=2)
```

returns the masked, capped impact response with the same `note` added. The
tool's own description tells the model to say which confidence it is relying
on before using the answer in a plan, and to treat an empty list as "nothing
recorded," never as proof that nothing depends on the thing.

If the accelerator connection was never wired up for a session, both tools
return `{"error": "accelerator_unavailable"}` rather than guessing at an
answer.

## Limits

These are read from the builder's own coverage fields and from what it does
not attempt, not guessed at:

- **Four dependency kinds are never derived.** `checks_out`, `triggered_by`,
  `needs_secure_file` and `uses_container` are modelled in
  `ado2gh/knowledge/models.py` but `ado2gh/knowledge/builder.py` does not try
  to find any of them yet (`EDGE_KINDS_NOT_ATTEMPTED`). This list travels in
  every scan's coverage as `edge_kinds_not_attempted`, precisely so that an
  empty answer about, say, a container image reads as "not looked at" and not
  "looked at, and there is none."
- **Six node kinds have nothing that produces them.** `release_pipeline`,
  `secure_file`, `container_image`, `wiki`, `work_item_project` and
  `test_plan` are modelled but the pipeline inventory the builder reads
  cannot name any of them today.
- **A dependency written through a variable is skipped, not guessed.** If a
  reference is only known at run time (`$(SomeVariable)`) or only at compile
  time (`${{ parameters.x }}`), the builder does not try to resolve it and
  records nothing for that reference (`ado2gh/knowledge/builder.py`,
  `_is_literal`).
- **A template's own repository needs a fresh inventory scan.** The reference
  to the repository a pipeline's YAML template lives in is read while
  `pipelines inventory` first extracts that pipeline's YAML
  (`ado2gh/pipelines/inventory.py`), before the stored copy gets the template
  inlined into it. A pipeline inventoried before this feature existed has no
  template reference stored yet; re-running `pipelines inventory` fills it
  in, and only then can the knowledge base see it.
- **The knowledge base is only ever as fresh as the last inventory run or the
  last on-request rebuild, whichever happened more recently.** A pipeline
  inventory run fills it automatically, but only when the run is real, not a
  preview, and `POST /v1/knowledge/scan` fills it on request. Neither path is
  wired to a command-line command yet, and a profile that has never had either
  one run against it has an empty knowledge base.
- **It only ever reads the pipeline inventory.** Nothing under
  `ado2gh/knowledge/` makes a call of its own to Azure DevOps or GitHub — a
  fact this platform has not inventoried is a fact the knowledge base cannot
  have either.

## Follow-ups

Named directly from the limits above, not from any separate plan:

- Deriving `checks_out`, `triggered_by`, `needs_secure_file` and
  `uses_container` dependencies.
- Producing `release_pipeline`, `secure_file`, `container_image`, `wiki`,
  `work_item_project` and `test_plan` nodes from whatever inventory would
  need to exist first.
- Wiring `build_knowledge_base` into a command-line command, so a scan can be
  started without an inventory run and without calling the route directly.
- Resolving dependencies that are today only expressed through variables, at
  least where the variable's value is itself known and literal elsewhere in
  the same pipeline.
