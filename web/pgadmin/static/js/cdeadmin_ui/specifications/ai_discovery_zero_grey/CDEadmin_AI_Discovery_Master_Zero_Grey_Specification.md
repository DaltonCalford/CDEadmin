# CDEadmin AI Interface + Data Discovery & Intelligence — Master Zero-Grey Specification

**Version:** 1.0 design baseline  
**Date:** 2026-09-10  
**Status:** Normative combined human/AI reference

> The individual files in this package remain authoritative for machine implementation. This master is a combined review copy.


---

<!-- SOURCE: 00_README_AND_PRECEDENCE.md -->

# CDEadmin AI Interface + Data Discovery & Intelligence — Zero-Grey Specification Package

**Version:** 1.0 design baseline  
**Date:** 2026-09-10  
**Status:** Normative implementation/design handoff  
**Audience:** low-capability design agents, coding agents, architecture reviewers, security reviewers

This package adds two cornerstone first-party modules to CDEadmin:

1. **AI Interface Module** — an integrated CDEadmin AI client/orchestration environment whose AI/model is always external to the database engine and whose database activity runs as an ordinary authenticated database user through dedicated AI connection profiles, connector policies, resource scopes, and database permissions.
2. **Data Discovery & Intelligence Module** — enterprise-wide technical/business data discovery, catalog, semantic search, marketplace, trust, usage, access workflow, search-to-analysis, and contextual intelligence.

## Non-negotiable ScratchBird model

ScratchBird is **one engine** with one underlying identity, storage, filesystem/page, security, and MGA transaction authority. It is not 24 engines hidden in one process.

Compatibility/reference parsers are external compatibility surfaces. Each parser presents exactly one legacy dialect/wire/API surface and is sandboxed to the compatibility workarea/schema presented to that client. Compatibility catalog/system views present the system tables/API/ABI shape that the legacy client expects.

**SBsql** is ScratchBird's native unified/normalized language. SBsql can address the recursive ScratchBird schema tree and, when the authenticated principal has the required security rights, can perform cross-compatibility/cross-emulation queries. Legacy compatibility parser sessions cannot gain that cross-tree visibility merely because the physical engine underneath is unified.

Therefore:

- one durable ScratchBird object = one canonical discovery identity;
- legacy compatibility names/catalog rows = access-surface aliases/projections of that canonical identity;
- usage through PostgreSQL/MySQL/Firebird/etc compatibility is aggregated to the canonical object while retaining per-surface evidence;
- AI using a legacy parser stays inside that parser's sandbox;
- AI needing a cross-emulation query must use an authorized SBsql access surface;
- neither module may synthesize cross-root visibility in application code to bypass engine security.

## Mandatory read order for an AI implementation agent

1. CDEadmin Platform Architecture/UIUX Specification.
2. CDEadmin Zero-Grey Component/Interaction Specification.
3. Zero-Grey machine tokens, component contracts, menu taxonomy, dialogs, states, shortcuts.
4. `01_SCRATCHBIRD_CANONICAL_IDENTITY_ACCESS_SECURITY_MODEL.md`.
5. The relevant module specification in `modules/`.
6. Its mechanics document.
7. Machine manifest, permissions, operations, screen contracts, and form contracts.
8. SVG plates.
9. Provider-specific runtime contracts/evidence.

## Zero-guess rule

If a required decision is not explicit:

`SPEC-GAP: <precise missing decision>`

Do not substitute a MUI default, Windows convention, pgAdmin convention, arbitrary colour, guessed security behavior, or guessed ScratchBird visibility rule.


## Compatibility fidelity versus canonical identity

The ScratchBird compatibility surfaces are expected to be sufficiently faithful that legacy engine
client tools and the legacy engine projects' own regression tests can exercise them successfully. This
is a compatibility-surface requirement only. It MUST NOT cause the AI or Discovery modules to model
those surfaces as separate physical engines. The canonical ScratchBird UUID-backed object and the one
ScratchBird storage/MGA/security authority remain the source of truth.


---

<!-- SOURCE: 01_SCRATCHBIRD_CANONICAL_IDENTITY_ACCESS_SECURITY_MODEL.md -->

# ScratchBird Canonical Identity, Access-Surface, and Security Model

**Status:** Normative for the AI Interface and Data Discovery & Intelligence modules  
**Purpose:** Prevent compatibility surfaces from being mistaken for separate databases or from weakening ScratchBird security.

---

## 1. Engine authority model

ScratchBird has one durable engine authority for a database/instance:

```text
ScratchBird engine
  ├─ UUID catalog / durable identity
  ├─ recursive schema tree
  ├─ storage/filespaces/pages
  ├─ MGA transaction/visibility/finality
  ├─ security/authorization
  └─ execution through bound internal representation
```

Compatibility parsers do not own those authorities.

A parser:

1. negotiates the source client protocol/API;
2. authenticates through ScratchBird authority;
3. parses exactly one source dialect;
4. resolves visible names to durable ScratchBird identities;
5. lowers accepted work to the internal request representation;
6. receives/re-shapes results/diagnostics for the source client.

The engine rechecks the work.

---

## 1.1 Compatibility fidelity and physical unification

ScratchBird compatibility is not a loose metadata skin. The compatibility parser plus its sandboxed
schema/catalog projections must present the legacy client with the dialect, wire behavior, system
catalog/API/ABI shape, and visible semantics expected by that legacy ecosystem. The project validates
that boundary using the legacy engine projects' own regression suites and client tools.

For the AI and Discovery modules, this has two consequences:

1. **Compatibility fidelity is evidence about an access surface, not evidence of a second engine.** A
   PostgreSQL-compatible ScratchBird surface may be exercised by PostgreSQL tools and tests, but its
   durable objects, pages/filespaces, transaction visibility, security checks, and execution remain
   ScratchBird authority.
2. **Cross-surface normalization happens through canonical ScratchBird identity and SBsql/security,
   not by merging independent legacy databases in CDEadmin.** When the same ScratchBird object is
   visible through multiple compatibility surfaces, DDI indexes one canonical object with multiple
   authorized aliases/access surfaces.

The modules MUST preserve the distinction between:

```text
legacy compatibility behavior      what a legacy client is allowed to observe/do
ScratchBird canonical identity      the durable object represented by that behavior
ScratchBird SBsql                    native unified/normalized language
ScratchBird engine authority         storage + MGA transaction + security + execution
```

A successful compatibility regression suite does not grant CDEadmin permission to expose hidden
ScratchBird branches to a compatibility user. Security remains authoritative.

## 2. Recursive schema and workarea model

ScratchBird schemas are recursive. A schema may contain child schemas.

A compatibility surface is rooted in a sandboxed workarea/schema branch. Within that branch,
the compatibility layer supplies the catalog/system-table/API/ABI projections required for the
legacy client.

Conceptual example:

```text
database-root
│
├─ data/
│   ├─ canonical_customer
│   └─ canonical_order
│
├─ applications/
│
├─ emulated/
│   ├─ postgresql/
│   │   ├─ pg_catalog/                 compatibility catalog projection
│   │   ├─ information_schema/         compatibility projection
│   │   └─ app-visible namespaces
│   │
│   ├─ mysql/
│   │   ├─ mysql/                      compatibility catalog projection
│   │   ├─ information_schema/
│   │   └─ app-visible namespaces
│   │
│   └─ firebird/
│       ├─ RDB$... projections
│       └─ app-visible namespaces
│
└─ ...
```

The exact physical/schema names above are illustrative. The normative rule is the relationship,
not a required literal path.

A compatibility parser session is presented with its authorized workarea as its effective root.
It SHALL NOT traverse out of that root using CDEadmin or AI shortcuts.

---

## 3. SBsql

SBsql is the native unified/normalized ScratchBird language.

SBsql is the only language surface in this specification that may perform a single query across
multiple compatibility/emulation branches of the same ScratchBird database, and only when:

1. the connection is authenticated as a principal with SBsql access;
2. the principal has visibility/privileges for every referenced object/schema;
3. the engine accepts the cross-tree request;
4. the CDEadmin/AI policy permits that operation.

This is an engine security capability, not a CDEadmin client-side join permission.

CDEadmin MUST NOT simulate forbidden cross-emulation visibility by separately querying hidden
compatibility roots and merging the data for an identity that lacks SBsql/global-tree permission.

---

## 4. Canonical object identity

For ScratchBird, the durable canonical identity is the engine catalog UUID (UUID v7 where the
engine uses it).

Data Discovery SHALL index a durable object once:

```text
CanonicalResource
  canonical_id = ScratchBird UUID
  provider = scratchbird
  instance = ...
  canonical_kind = ...
  canonical_schema_path = ...
  access_surfaces[] = ...
```

`access_surfaces[]` may include:

```text
SBsql native
PostgreSQL compatibility
MySQL compatibility
Firebird compatibility
Cassandra / YCQL compatibility
MongoDB compatibility
...
```

An access surface is not a second data asset.

---

## 5. Access-surface reference

Use the following conceptual contract:

```text
AccessSurfaceRef
  surface_id
  provider_id
  instance_id
  kind:
    sbsql_native
    compatibility_parser
    mcp
    cdeadmin_provider
  dialect_id
  parser_profile?
  workarea_schema_ref?
  visible_qualified_name?
  catalog_projection_refs[]
  canonical_resource_ref
  visibility_scope
  query_capabilities[]
  mutation_capabilities[]
  cross_surface_visibility: none | engine_authorized
  evidence/version
```

For a compatibility parser:

`cross_surface_visibility` MUST be `none`.

For SBsql:

`cross_surface_visibility` MAY be `engine_authorized`; it is not automatically enabled.

---

## 6. Catalog/system projections

A compatibility catalog projection can describe or expose metadata in the shape expected by a
legacy client.

The presence of a row in a compatibility system catalog:

- does not create a second durable object;
- does not grant base-object access;
- does not grant other compatibility-root visibility;
- does not grant SBsql global-tree visibility;
- does not permit discovery to reveal aliases hidden by security.

Data Discovery classifies these as `compatibility_catalog_projection` or
`compatibility_system_surface`. They are hidden from business search by default and visible
under technical/system filters to authorized users.

---

## 7. Usage canonicalization

Every query/use event that can resolve to a ScratchBird UUID SHALL record both:

```text
canonical_resource_ref
access_surface_ref
```

Usage aggregation has two views:

```text
canonical usage          all approved surfaces combined
surface usage            usage via a specific compatibility/native surface
```

Do not count PostgreSQL and MySQL appearances of the same ScratchBird object as two independent
datasets.

---

## 8. Lineage canonicalization

Lineage edges bind canonical resources.

If a PostgreSQL compatibility query and an SBsql query both read the same ScratchBird UUID,
they contribute evidence to the same lineage node.

The access surface is evidence/route metadata on the observation, not a duplicated node.

An exception exists only where the compatibility surface represents a genuinely distinct
durable ScratchBird object.

---

## 9. Search alias behavior

A legacy-visible name may be indexed as an alias only for principals allowed to discover that
surface.

Example:

```text
canonical: uuid-...
SBsql name: company.sales.customer
PostgreSQL alias: public.customer
MySQL alias: sales.customer
```

A query for `public.customer` may retrieve the canonical customer object.

The result SHALL say:

```text
Canonical ScratchBird object
Matched through: PostgreSQL compatibility name public.customer
Other visible surfaces: [only those the principal may discover]
```

It SHALL NOT reveal hidden compatibility aliases.

---

## 10. CDEadmin Data Explorer versus Discovery

Data Explorer may deliberately present provider/access-surface trees because its job is operational
management.

Data Discovery defaults to canonical entities and collapses ScratchBird aliases.

A `Show access surfaces` control expands the aliases without creating new canonical results.

---

## 11. AI connection rules

An AI session never inherits cross-emulation rights because the user typed a natural-language
request.

AI database access is through a dedicated AI connection profile/principal.

### Compatibility parser connector

```text
dialect = one legacy dialect
workarea = one authorized compatibility root
cross_emulation = forbidden
```

### SBsql connector

```text
dialect = SBsql
recursive-tree visibility = engine-authorized
cross-emulation query = possible only with explicit AI policy + database grant
```

### ScratchBird MCP connector

MCP is an external AI/control interface. It does not move AI into the engine. MCP tool calls still
execute through an authenticated ScratchBird/CDEadmin authority and must obey the tool's own
security/approval contract.

---

## 12. Cross-surface query decision

When an operation references multiple ScratchBird compatibility branches in one ScratchBird
database:

```text
if requested connector == compatibility_parser:
    refuse cross-root operation
    suggest authorized SBsql connection if policy permits

if requested connector == sbsql_native:
    validate principal visibility for every resource
    validate AI cross-surface policy
    compile/execute through SBsql/engine path

if resources belong to independent external engines:
    do not claim one ScratchBird transaction
    use explicit multi-connection orchestration/federation capability
```

---

## 13. Transaction semantics

Compatibility clients may experience their expected transaction/autocommit semantics, but
ScratchBird engine MGA remains the durable transaction authority.

For AI and Discovery:

- display the visible client transaction model where useful;
- store canonical transaction/run evidence;
- never describe compatibility-parser transactions as a separate transaction engine.

SBsql cross-emulation within one ScratchBird database uses the one underlying ScratchBird
transaction authority.

---

## 14. Required UI labels

Use these labels consistently:

- `ScratchBird native (SBsql)`
- `<Engine> compatibility surface`
- `Canonical ScratchBird object`
- `Compatibility catalog projection`
- `Sandboxed workarea`
- `Cross-surface query: SBsql only`
- `Engine authorization required`

Do not label a compatibility surface as an independent physical database unless it actually connects
to an independent external legacy database.

---

## 15. Required tests

1. Same ScratchBird UUID visible via two compatibility surfaces indexes as one canonical discovery result.
2. Alias search finds the canonical object but exposes only authorized aliases.
3. Usage via two compatibility parsers aggregates canonically and preserves per-surface counts.
4. Compatibility parser AI connector cannot query sibling compatibility root.
5. SBsql connector with missing object privilege cannot cross-query that object.
6. SBsql connector with proper grants and AI policy can perform cross-surface query.
7. Catalog projection visibility does not grant base-object preview/query access.
8. System catalog projections are excluded from business discovery by default.
9. Lineage from different access surfaces resolves to one canonical ScratchBird node.
10. External PostgreSQL server remains a separate canonical provider resource and is never merged with a ScratchBird PostgreSQL compatibility alias solely by name.


---

<!-- SOURCE: 02_AI_CONNECTOR_AUTHORIZATION_EXECUTION_MECHANICS.md -->

# AI Interface — Connector, Authorization, Planning, and Execution Mechanics

**Status:** Normative companion to `modules/01_AI_INTERFACE_MODULE_ZERO_GREY.md`

---

## 1. Separation of model, orchestrator, connector, and database

There are four distinct authorities.

```text
MODEL
  proposes language/tool calls
  owns no CDEadmin/database authority
        ↓
AI ORCHESTRATOR
  validates profile/policy/plan
  selects approved connector/tool
        ↓
CONNECTOR
  authenticates as dedicated principal
  translates approved operation
        ↓
DATABASE / CDEADMIN SERVICE
  performs final authorization/execution
```

No layer may pretend a previous layer's validation is final authorization.

---

## 2. AIConnector contract

Conceptual interface:

```text
AIConnector.describe()
AIConnector.validateConfiguration()
AIConnector.authenticateOrResolveIdentity()
AIConnector.discoverCapabilities()
AIConnector.test()
AIConnector.prepare(operation)
AIConnector.execute(preparedOperation, approvalEvidence?)
AIConnector.cancel(operationRef)
AIConnector.health()
AIConnector.close()
```

`prepare()` SHALL NOT create live side effects except explicitly documented provider-safe preparation
such as compilation/explain.

`execute()` returns a structured result or TaskRef.

### 2.1 Descriptor

```text
connector_id
connector_class
version
provider_id?
dialect_id?
transport_profile
principal_binding
resource_scope
query_policy
tool_policy?
capabilities
health
last_validated
```

### 2.2 Prepared operation

```text
prepared_id
connector_snapshot
operation_kind
canonical_resource_refs[]
access_surface_refs[]
normalized_arguments
native_compiled_artifact?
risk_class
estimated_effects[]
budgets
validation[]
expires_at
```

A prepared operation is stale if relevant connector/policy/resource revision changes.

---

## 3. Connector classes and transport reuse

The AI module may reuse CDEadmin provider driver/wire implementations internally.

It SHALL still create:

- a separate AI connection profile;
- separate credential/principal binding;
- separate session/pool;
- separate policy;
- separate audit identity.

"Own connector" means own connection/security/control authority, not duplicate protocol code.

---

## 4. ScratchBird connector decision table

| Need | Allowed connector | Cross-surface? | Notes |
|---|---|---:|---|
| Native SB functionality | SBsql | engine-authorized | preferred |
| One legacy client behavior | matching compatibility parser | no | sandboxed workarea |
| Legacy regression/diagnostic context | matching compatibility parser | no | do not blend dialects |
| Query objects across compatibility branches in one SB DB | SBsql | yes if granted | one engine/MGA authority |
| ScratchBird external AI tooling | ScratchBird MCP | tool-specific | still external |
| Ordinary CDEadmin provider admin | CDEadmin/provider connector | per capability | not model privilege |

The planner SHALL refuse a request to use one compatibility connector to reach a different
compatibility root.

---

## 5. ScratchBird canonical resource resolution

Before execution, a ScratchBird query/tool operation that references visible legacy names SHALL resolve
them to canonical UUID-backed ResourceRefs where possible.

The plan review may show both:

```text
visible reference: public.customer
surface: PostgreSQL compatibility
canonical: ScratchBird UUID ...
```

This is evidence, not a rewrite of the user's requested dialect.

---

## 6. Human delegation

The human initiator grants CDEadmin permission to request action by the AI principal.

Delegation record:

```text
initiator
agent_profile
connector
allowed risk ceiling
resource scope
start/end
project?
reason?
```

Delegation cannot exceed the user's CDEadmin delegation permission.

It also cannot exceed the AI principal's actual database permissions.

---

## 7. Deterministic policy evaluation order

Evaluate in this exact order:

1. module/site emergency state;
2. AgentProfile enabled;
3. ModelProfile enabled/healthy;
4. connector enabled/healthy;
5. user has `ai.use`;
6. user may delegate requested risk/action;
7. tool/command is AI-exposed;
8. Agent ToolPolicy allows it;
9. connector policy allows operation kind;
10. target ResourceRefs are within connector scope;
11. environment policy allows it;
12. database principal capability/permission precheck;
13. data egress policy for model-visible content;
14. budget availability;
15. plan validation;
16. required approval state;
17. final backend authorization at execution.

A later check cannot override an earlier denial.

---

## 8. Tool publication

The AI-visible tool registry is derived, not hand-coded separately.

### 8.1 CDEadmin command tool

```text
tool_name
command_id
description
input_schema
output_schema
risk_class
required_permissions
creates_task
approval_rule
```

### 8.2 Read service tool

Read-only services may expose AI methods if they declare:

- stable schema;
- access checks;
- bounded result shape;
- classification behavior.

### 8.3 Hidden by default

A command/service without explicit AI exposure metadata is not published.

---

## 9. Tool schema policy

Input and output schemas use JSON Schema compatible structures.

Tool descriptions SHALL be concise semantic contracts, not hidden instructions intended to bypass
higher-level policy.

Remote MCP tool schemas are cached as external descriptors and wrapped by local policy metadata.

---

## 10. Model request construction

Model request order:

```text
system/site immutable policy summary
agent instruction asset
session mode
available tool descriptors (filtered)
explicit context summaries
conversation
current user request
```

Untrusted database/tool content is delimited and labeled as data.

No credential values are inserted.

---

## 11. Context extraction

A context item is materialized only after policy checks.

Example `SAFE_SAMPLE` flow:

```text
ResourceRef
  ↓ provider authorization
bounded sample query
  ↓ row/column security
sensitivity classification
  ↓ redaction/masking
local truncation
  ↓ egress policy
model context extract
```

The model never chooses to bypass a failed stage.

---

## 12. Large-content handles

Large data/results are represented to the model with stable temporary handles:

```text
ResultHandle
  handle_id
  result_type
  row_count?
  byte_count?
  schema_summary
  classification
  expiry
  permitted follow-up operations
```

Tools can request bounded slices/statistics through the handle.

Handles do not grant more access than the originating result.

---

## 13. Query compile/execute separation

Where provider supports it:

```text
AI drafts query
  ↓
compile / parse / resolve
  ↓
canonical resource list
  ↓
read/write classification
  ↓
explain / estimates
  ↓
policy / approval
  ↓
execute compiled artifact
```

If compile artifacts are revision-bound, the execution step rejects stale artifacts.

---

## 14. Read-only classification

A query is read-only only if the provider/compiler classifies it as such.

String-prefix checks such as `startsWith("SELECT")` are insufficient.

Stored functions/procedures with side effects require provider-aware classification.

Unknown side effects cause the query to be treated at least as R4 or refused.

---

## 15. Mutation flow

```text
draft mutation
  ↓ compile/resolve
  ↓ calculate target resources
  ↓ R4/R5/R6 classification
  ↓ show diff/effects where possible
  ↓ acquire revision-bound approval
  ↓ execute as AI principal
  ↓ capture backend result + task
  ↓ post-validate
  ↓ report success/partial/failure
```

The AI cannot alter the statement after approval without creating a new plan revision.

---

## 16. Transaction handling

AI QueryPolicy controls whether explicit transaction control is allowed.

### ScratchBird

All work remains under ScratchBird MGA transaction authority.

An SBsql cross-surface query in one ScratchBird database is one engine request/transaction context as
defined by the server/engine.

### Multiple independent external engines

Separate connectors imply separate transaction authorities unless an explicit external/distributed
transaction capability is used.

The UI must say `multi-connection operation`, not `single transaction`, when atomicity is absent.

---

## 17. CDEadmin task mapping

Any long operation creates TaskRef.

AI waits via TaskService tools:

```text
task.get
task.cancel
task.get_result
```

The model should not busy-poll faster than configured minimum interval.

Closing the AI session UI does not cancel a task unless user explicitly requests it.

---

## 18. MCP version/profile mechanics

Store per server:

```text
advertised/negotiated protocol version
transport profile
authorization profile
tool-catalog revision/TTL
supported extensions
compatibility adapter version
```

For current 2026-07-28 MCP servers, prefer the stateless request/response core and current Tasks
extension semantics.

For older servers, use explicit compatibility profile. Do not translate away security-relevant
differences silently.

---

## 19. ScratchBird MCP

The existing ScratchBird external AI/MCP layer may expose discovery, metadata, compile/execute,
vector/hybrid retrieval, audit/governance, diagnostics, remote MCP and routing tools.

The integrated CDEadmin AI module SHALL treat those as one possible ScratchBird connector.

It does not assume MCP is the only or privileged ScratchBird path; SBsql/provider connectors remain
separate choices.

---

## 20. Approval evidence

Approval evidence is created by CDEadmin, not the model.

Fields:

```text
approval_id
approver
plan_id
plan_revision/hash
step_ids
connector/principal
resource_refs
environment
risk_class
created
expires
confirmation_method
```

Backend adapters may translate this to their own approval token/evidence format.

---

## 21. Failure categories

```text
MODEL_PROVIDER_FAILURE
CONNECTOR_TRANSPORT_FAILURE
AUTHENTICATION_FAILURE
AUTHORIZATION_DENIED
POLICY_DENIED
RESOURCE_SCOPE_DENIED
BUDGET_EXCEEDED
APPROVAL_REQUIRED
APPROVAL_STALE
COMPILE_ERROR
PROVIDER_ERROR
TASK_FAILED
PARTIAL_RESULT
VERSION_INCOMPATIBLE
MCP_TOOL_ERROR
INTERNAL_ORCHESTRATOR_ERROR
```

Do not normalize all failures into "AI error."

---

## 22. Retry policy

Model request retry and database action retry are separate.

### Model request

May retry transient provider error if no consequential tool action has executed since the last
model checkpoint.

### Database read

May retry only according to provider/idempotency policy.

### Mutation

Never automatically retry unless the prepared operation is explicitly idempotent or backend provides
a transaction/idempotency token proving safe retry.

---

## 23. Cancellation

Cancellation targets:

- model generation;
- connector query;
- MCP remote task;
- CDEadmin Task.

Cancellation is best-effort unless provider proves stronger behavior.

The UI shows `cancel requested` until confirmed.

---

## 24. Observability

Emit tracing/metrics:

```text
AI session/run ID
model request latency
tool latency
connector latency
database query latency
approval wait time
token counts
tool counts
policy denials
query rows/bytes
task outcomes
```

Sensitive prompt/result contents are not required for operational metrics.

---

## 25. Audit replay

Audit should allow reviewers to reconstruct:

1. what the user asked;
2. what context was exposed;
3. which model/profile was used;
4. what the model proposed;
5. which policy checks ran;
6. who approved;
7. what exact command/query executed;
8. under which database principal/access surface;
9. what the backend returned;
10. what final response was shown.

Prompt/message text may be unavailable if retention policy deleted it; audit metadata remains.

---

## 26. UI machine contracts

Every screen/form in the package is part of this mechanics contract. A coding agent cannot replace the
Plan Review, Query Review, Approval, Context Manager or Connector test surfaces with a generic chat
message.

---

## 27. Security acceptance proof

Release evidence must contain negative tests proving:

- cross-root compatibility access denied;
- unauthorized SBsql cross-surface denied;
- tool hidden when not AI-exposed;
- user delegation cannot elevate;
- connector principal cannot elevate;
- prompt injection cannot change tool policy;
- stale approval denied;
- secret egress denied;
- oversized result not sent to model;
- R5/R6 cannot auto-execute.


---

<!-- SOURCE: 03_DISCOVERY_INDEX_SEARCH_RANKING_MECHANICS.md -->

# Data Discovery & Intelligence — Index, Search, Ranking, Trust, and Access Mechanics

**Status:** Normative companion to `modules/02_DATA_DISCOVERY_INTELLIGENCE_MODULE_ZERO_GREY.md`

---

## 1. Discovery is a derived index, not the source of truth

Authoritative sources remain:

- provider metadata/resources;
- Project/Asset service;
- Business Knowledge assets;
- Quality results;
- Lineage/RelationshipGraph;
- Contracts;
- task/query/usage evidence;
- security/permissions.

DiscoveryDocument is a search-optimized projection.

Deleting/rebuilding the Discovery index must not delete authoritative metadata.

---

## 2. Canonicalization sequence

For every input item:

```text
identify source
  ↓
resolve ResourceRef/AssetRef
  ↓
if ScratchBird:
    resolve durable UUID
    attach authorized AccessSurfaceRefs
  ↓
if imported external catalog:
    link to existing ResourceRef when explicit stable mapping exists
    otherwise create imported metadata entity / evidence relationship
  ↓
produce one canonical DiscoveryDocument
```

Names alone are not identity.

---

## 3. ScratchBird alias mechanics

A ScratchBird AccessSurface alias contributes:

```text
search tokens
surface-specific qualified name
dialect
workarea
compatibility evidence
per-surface usage
```

It does not contribute another canonical result.

At query time, alias tokens are filtered by searcher's surface visibility.

---

## 4. Security-first search execution

Exact order:

1. authenticate search principal;
2. resolve discover permissions and policy scope;
3. parse search text/filter syntax;
4. constrain candidate universe to discoverable documents/aliases;
5. perform lexical/vector/graph/structured retrieval on permitted universe or security-filtered index;
6. merge candidate sets by canonical identity;
7. compute feature scores using only permitted metadata;
8. apply ranking profile;
9. generate result explanations with permitted evidence only;
10. render facets/counts/recommendations from permitted candidates.

A system MAY use post-filtering internally for performance only if it can prove no hidden values leak through
counts, ranking, facets, snippets, autocomplete or timing-sensitive metadata exposed to the user.

---

## 5. Query parsing

Recognized user syntax MAY include:

```text
plain words
quoted phrase
field:value
field:(a OR b)
-tag:value
provider:
domain:
owner:
type:
certified:
quality:
freshness:
access:
```

Structured Advanced Search is authoritative for complex filters.

Malformed expert syntax returns position-aware diagnostic rather than silently changing meaning.

---

## 6. Text normalization

Normalize for indexing/search:

- Unicode normalization according to configured policy;
- case-folding for case-insensitive fields;
- tokenization appropriate to language;
- preserve exact identifiers separately;
- maintain original text for display;
- business synonyms applied as expansion features, not destructive replacement.

Do not normalize provider identifiers in a way that changes their case-sensitive meaning.

---

## 7. Lexical index fields

At minimum:

```text
name_exact
qualified_name_exact
alias_exact
name_tokens
description_tokens
business_term_tokens
synonym_tokens
tag_tokens
schema_label_tokens
owner_tokens
domain_tokens
native_kind_tokens
provider_tokens
documentation_tokens (bounded)
```

Exact fields are boosted through ranking, not tokenized away.

---

## 8. Semantic vector input

Default semantic document:

```text
canonical name
authorized business-facing aliases
description
business terms/synonyms
domain
tags
schema labels allowed by policy
short documentation summary
```

Do not include:

- raw secrets;
- protected row content;
- unbounded source code;
- hidden aliases;
- hidden lineage.

Input fingerprint:

```text
hash(model_id + model_version + normalized_allowed_input)
```

Change requires re-embedding.

---

## 9. Candidate set construction

Candidate sources:

```text
C_exact    exact identity/name/alias
C_lex      lexical top K
C_sem      semantic top K
C_graph    relevant graph neighbors
C_biz      business knowledge mappings
```

Union and de-duplicate by canonical identity.

Default pre-ranking candidate budget:

```text
exact: all exact matches (bounded by security)
lexical: top 500
semantic: top 500
graph: top 200
business: top 200
merged hard cap: 1000 canonical candidates
```

Site policy may tune budgets.

---

## 10. Feature normalization

Each ranking feature is 0..1.

### Lexical

Backend relevance normalized within the candidate batch plus explicit exact-field features.

### Semantic

Vector similarity converted to 0..1 according to metric/provider adapter.

### Business

Score from exact/approved term/metric/domain relationship.

### Trust

Certification/contract/governance state.

### Quality

Current critical/overall quality state with freshness of quality result.

### Freshness

Resource freshness relative to known SLA or configured heuristics.

### Usage

Log-scaled canonical usage across selected window.

### Context

User/team/domain/project relevance subject to privacy policy.

### Lineage

Relationship to already selected/relevant resources/assets.

### Documentation

Description/owner/steward/usage docs completeness.

Unknown values use neutral/missing treatment defined by profile; they are not zero unless the profile explicitly
says so.

---

## 11. Default scoring

Weighted sum from published RankingProfile plus boosts/penalties.

Store with each result explanation:

```text
profile_id
profile_revision
component_scores
boosts
penalties
final_score
tie_break_reason?
```

This permits deterministic support reproduction.

---

## 12. Result snippets

Snippets may show text that contributed to match.

They must:

- come from permitted fields;
- escape active content;
- mark matched terms;
- avoid hidden neighboring values;
- state source field when useful.

---

## 13. Facets

Facet counts are computed only over visible result universe.

High-cardinality facets use search/paging.

Required default facets:

```text
Entity type
Provider
Domain
Certification
Quality
Freshness
Access
Owner
Environment
Native kind
Updated
```

ScratchBird adds optional `Access surface` technical facet.

---

## 14. Autocomplete

Autocomplete sources:

```text
visible exact names
visible aliases
business terms
metrics
domains
saved/recent queries
```

Minimum prefix length default: 2 characters.

Hidden assets cannot appear as suggestions.

---

## 15. Business-term expansion

A query matching an approved term expands to mapped entities.

Expansion edge itself is a ranking feature/evidence.

Deprecated terms may redirect to replacement with visible explanation.

---

## 16. Usage aggregation

Usage event:

```text
event_id
actor_scope (privacy controlled)
time
canonical_resource_refs[]
access_surface_refs[]
consumer_type
consumer_ref?
success/failure
duration?
rows/bytes?
```

Canonical resource counts de-duplicate within one event.

Per-surface counters remain separate.

---

## 17. Popularity scoring

Default usage feature uses log scaling:

```text
u = min(1, log1p(weighted_events) / log1p(popularity_saturation))
```

Default event weights:

```text
successful query             1.0
dashboard/report view        0.5
API request                  0.25
ETL/CDC production use       1.5
ML training/evaluation use   1.5
search result selection      0.25
favorite/collection add      0.5
AI context use               0.25
failed query                 0.05
```

Default `popularity_saturation` = 1000 weighted events in the active ranking window.

These are ranking defaults; administrators may version custom profiles.

---

## 18. Freshness scoring

If an active contract/SLA defines freshness:

```text
freshness_score = 1 while within target
decays linearly to 0 across configured breach grace window
```

Default grace window: one SLA target interval.

If no SLA exists, provider's last-update/refresh evidence may use configured provider heuristic.

If freshness is unknown, show unknown and use neutral ranking treatment.

---

## 19. Quality feature

Default:

```text
critical failing => 0
critical unknown/stale => 0.4
critical pass + noncritical failures => 0.7
all applicable current checks pass => 1.0
no quality definition => neutral 0.5
```

The separate critical-quality penalty still applies where defined by RankingProfile.

---

## 20. Trust/certification feature

Default:

```text
CERTIFIED current               1.0
CERTIFIED_WITH_CONDITIONS       0.85
IN_REVIEW                       0.6
UNREVIEWED                      0.5
EXPIRED                         0.4
REJECTED                        0.2
REVOKED                         0.0
```

A resource without certification uses neutral 0.5, not failed 0.

---

## 21. Documentation completeness

Default component:

```text
description present       0.30
owner present             0.20
steward present           0.10
business term mapped      0.15
usage/how-to doc          0.15
examples                  0.10
```

Sum = 1.

---

## 22. Context personalization

Allowed context signals:

- current project/domain;
- user's declared team/role;
- recent user selections;
- team aggregate use above privacy threshold;
- explicit favorites.

Do not infer sensitive employment/organizational attributes from query content.

Users/admins can disable personalization.

`Why this result?` identifies personalization when it affected ranking materially.

---

## 23. Recommendations

Recommendation generation is security-filtered first.

Frequently-used-together uses canonical co-occurrence events.

Similar semantics uses visible metadata embeddings.

Replacement recommendations require explicit relationship/deprecation evidence; similarity alone is insufficient.

---

## 24. Data Product search behavior

Data Products receive:

- business relevance from member terms/metrics;
- certification/trust signals;
- product freshness/quality summaries;
- member usage;
- product usage.

Product result does not hide problems in members; Inspector expands trust evidence.

---

## 25. Profile statistics storage

Discovery stores statistics metadata and method, not necessarily raw samples.

```text
metric
value
method
sample_size?
captured_at
resource_revision?
classification
```

Stats expire according to source/profile policy.

---

## 26. Search-to-analysis decision

For a selected entity set:

1. determine common provider/instance;
2. determine authorized access surfaces;
3. determine compatible query/BI/semantic model actions;
4. determine transaction scope;
5. present only valid paths.

### ScratchBird same engine / multiple compatibility surfaces

Prefer SBsql when cross-surface query is requested and authorized.

### Separate external providers

Present multi-source/federated/AI orchestration only if the corresponding platform capability exists.

---

## 27. Access request policy evaluation

Before submit:

- target is requestable;
- requester may request;
- requested rights are available;
- duration within bounds;
- reason present;
- policy may auto-approve or route.

After approval, provider grant planning is a separate step.

Provisioning failure changes request to `FAILED` and preserves approval evidence; it does not claim access active.

---

## 28. Index source failure

States:

```text
healthy
degraded
auth_required
failed
disabled
stale
```

Documents from a failed source may remain searchable as stale according to retention policy.

UI shows source freshness.

---

## 29. Full reconcile

Full reconcile phases:

```text
snapshot source configuration
discover entities
canonicalize
security-label
enrich
validate
build candidate revision
compare counts/deletions
publish atomically
retire previous revision
```

Unexpected deletion threshold may require approval before publish.

Default deletion safety threshold: 20% of documents in a mandatory source scope.

---

## 30. ScratchBird system catalog suppression

By default, system/catalog projections have:

```text
business_search = false
technical_search = true if authorized
recommendations = false
data_product_candidate = false
```

A business user searching `customer` should not see `pg_catalog.*` before the actual customer data.

---

## 31. Canonical result display

For ScratchBird:

```text
Customer
ScratchBird table
Canonical object
Matched via PostgreSQL compatibility: public.customer
```

`Matched via` appears only when alias influenced match.

The default row is not duplicated for MySQL/Firebird aliases.

---

## 32. Curation evidence

Every manual curation change records:

```text
actor
time
target
field/relationship
before
after
reason
source evidence
```

Git may version project-backed knowledge assets; operational audit remains separate.

---

## 33. AI enrichment mechanics

DDI requests enrichment through AI Interface:

```text
DDI selects permitted metadata context
  ↓
AI Interface applies model/egress policy
  ↓
model returns suggestion
  ↓
DDI stores EnrichmentSuggestion
  ↓
curator accepts/rejects
  ↓
authoritative BusinessKnowledge asset changes
```

DDI never sends data directly to a model behind the AI Interface policy layer.

---

## 34. Search API result

Structured result:

```text
query_id
index_revision
ranking_profile/revision
total_visible_estimate?
facets
results[]
warnings
next_cursor
```

Each result:

```text
canonical_ref
entity_class
name
snippet
provider/native_kind
trust summary
access summary
score
explanation
visible aliases[]
actions[]
```

---

## 35. Cursor/pagination

Use opaque cursor over stable query/index revision.

If active index revision changes, existing cursor may:

- continue against retained revision until expiry; or
- return explicit stale cursor diagnostic.

Never silently restart page 2 against a different ranking revision.

---

## 36. Search analytics privacy

Search logs may contain sensitive intent.

Policy controls:

- raw query retention;
- anonymization;
- minimum aggregation threshold;
- admin access;
- export.

AI analysis of search logs requires AI Interface data policy.

---

## 37. DDI availability without AI

The following MUST work with AI disabled:

- keyword search;
- structured/faceted search;
- glossary/domain;
- deterministic ranking;
- quality/trust/freshness;
- lineage/relationships;
- usage ranking;
- data products;
- access request;
- lexical recommendations;
- semantic search when an embedding service is separately configured.

Generative "Ask" and AI enrichment are disabled with a clear reason.

---

## 38. Test reproducibility

A ranking bug report should be reproducible using:

```text
query text
principal/security test identity
index revision
ranking profile revision
candidate debug IDs (authorized support view)
component scores
boosts/penalties
final order
```

Support tooling must not leak hidden result identities to an unauthorized reporter.

---

## 39. Release acceptance

Release proof includes:

- security-trim tests;
- canonical ScratchBird alias tests;
- ranking arithmetic tests;
- stale source tests;
- access request state tests;
- Data Product/Glossary versioning;
- index atomic publish/recovery;
- autocomplete/facet leak tests;
- AI-off operation;
- AI-enrichment review flow.


---

<!-- SOURCE: 04_AI_DISCOVERY_CROSS_MODULE_CONTRACT.md -->

# AI Interface + Data Discovery — Cross-Module Integration Contract

## 1. Separation

The AI Interface and Data Discovery modules are tightly integrated but independent.

```text
Data Discovery
  ├─ search/catalog/trust/access
  └─ registers typed read/propose tools
              ↓
AI Interface
  ├─ model provider / connector / policy / approval
  └─ invokes DDI tools through governed CDEadmin service
```

DDI contains no hidden general-purpose LLM runtime.

AI Interface can operate when DDI is disabled, using ordinary CDEadmin metadata/tools.

## 2. AI -> DDI

Permitted tool families:

```text
search
get entity context
get business term
get metric
get product
get trust signals
get lineage summary
compare visible candidates
request access draft
```

The AI sees only DDI-security-trimmed results plus its own data-egress policy.

## 3. DDI -> AI

DDI may request:

```text
description suggestion
term/synonym suggestion
domain suggestion
sample query draft
candidate comparison explanation
natural-language search interpretation
analysis plan draft
```

Result is a suggestion with model/evidence provenance.

## 4. Search-to-analysis

Natural-language analysis path:

```text
user asks question in DDI
  ↓
DDI retrieves governed candidates
  ↓
optional AI Interface interprets metrics/dimensions
  ↓
user sees interpretation
  ↓
AI Interface drafts query/analysis plan
  ↓
CDEadmin query/BI module validates
  ↓
authorized execution
```

The model never bypasses DDI access state.

## 5. ScratchBird

DDI resolves selected ScratchBird resources to canonical UUIDs and authorized AccessSurfaceRefs.

AI Interface chooses connector:

- one legacy surface task -> compatibility connector may be valid;
- cross-emulation -> SBsql only;
- native functionality -> SBsql preferred;
- MCP tool -> ScratchBird MCP connector.

DDI does not perform client-side hidden cross-root reads to make AI answers more complete.

## 6. Other CDEadmin modules

DDI consumes:

- Lineage;
- Quality;
- Contracts;
- API;
- ETL;
- CDC;
- Migration;
- Tracing;
- Replication;
- ML/Vector;
- Analytics/BI;
- Projects/Git;
- DDN.

AI Interface may invoke all modules only through AI-exposed service/command contracts.

## 7. Common ResourceRef rule

Both modules use canonical ResourceRef.

Any module-specific alias, user-facing name, DDN element, query reference, compatibility catalog row,
or discovery document points back to canonical ResourceRef where a live resource exists.

## 8. No circular authority

- Discovery ranking cannot grant permission.
- AI recommendation cannot grant permission.
- Access approval cannot create a provider grant until provisioning command succeeds.
- Provider grant cannot make hidden aliases discoverable if DDI policy forbids metadata disclosure.
- CDEadmin visibility cannot override database security for data/query operations.


---

<!-- SOURCE: modules/01_AI_INTERFACE_MODULE_ZERO_GREY.md -->

# CDEadmin AI Interface Module — Complete Zero-Grey Specification

**Module ID:** `cdeadmin.ai_interface`  
**Specification:** 1.0  
**Status:** Normative committed first-party module  
**Core principle:** AI is outside every database engine. It is an external client/user.

---

## 0. Product definition

The AI Interface Module provides an integrated CDEadmin work area in which a human can use one or
more external AI/model providers to understand, plan, draft, search, analyze, or operate CDEadmin
and connected databases.

The module is not an AI database engine feature.

The model:

```text
Human
  ↓
CDEadmin AI Interface
  ├─ model connector → external/local AI model runtime
  ├─ CDEadmin capability connector → CDEadmin commands/services
  ├─ dedicated database connector(s) → authenticated database principal(s)
  └─ MCP connector(s) → approved external MCP servers/tools
                                  ↓
                           database / CDEadmin
```

The AI is treated as another user/actor with explicit identity, connections, restrictions, permissions,
budgets, and audit.

---

## 1. Non-negotiable security invariants

1. AI code/model execution does not run inside ScratchBird engine address space as part of query
   semantics.
2. AI does not receive engine-internal authority.
3. AI never silently inherits the interactive human's open database session or credentials.
4. AI uses dedicated connection profiles and a dedicated database principal, or an explicit,
   auditable delegated identity mechanism when a provider supports it.
5. CDEadmin user permission to ask an AI to do something does not grant database permission.
6. Database permission held by the AI principal does not grant the initiating user permission to
   delegate that action.
7. Effective permission is the intersection of all applicable authorities.
8. Approval evidence never expands permissions.
9. Raw secrets are not exposed to the model.
10. Data supplied by databases, comments, documentation, files, API schemas and external tools is
    untrusted content, never policy instructions.
11. Compatibility parser sessions remain sandboxed.
12. SBsql cross-emulation is possible only under engine authorization and explicit AI policy.
13. The AI may propose unsupported actions; the platform must refuse them, not approximate them.
14. The AI cannot approve its own consequential action.
15. AI audit evidence is append-oriented and attributable to model/provider/profile/version.

---

## 2. Effective authorization equation

Every database or CDEadmin action SHALL be evaluated conceptually as:

```text
effective_authority =
  initiating_user.delegation_permission
  ∩ agent_profile.allowed_actions
  ∩ connector_profile.allowed_actions
  ∩ cdeadmin_command_permission
  ∩ environment_policy
  ∩ database_principal_permission
  ∩ resource_scope
  ∩ data_egress_policy (when content leaves CDEadmin)
```

If any term denies the action, the action is denied.

The UI SHALL expose the first concrete denial reason that can be safely disclosed and provide an
`All checks` expandable section.

---

## 3. Actor and identity model

### 3.1 Human initiator

The signed-in CDEadmin user who started or approved a session/action.

### 3.2 AI agent profile

A named project/user/workspace configuration controlling:

- model provider;
- model identifier;
- system/developer instruction set;
- database connectors;
- CDEadmin tool policy;
- data policy;
- approval policy;
- budgets;
- retention;
- permitted autonomous behaviors.

### 3.3 AI database principal

The database identity seen by the target database.

Recommended default:

```text
cdeadmin_ai_<profile or service identity>
```

Actual naming is provider policy.

### 3.4 Delegated identity

A provider MAY support explicit workload/delegated identity. This remains a distinct authenticated
principal/session and must be visible in audit.

CDEadmin SHALL NOT implement invisible database-user impersonation.

---

## 4. Connector classes

The module SHALL implement a versioned `AIConnector` interface with these connector classes.

### 4.1 CDEadmin Capability Connector

Purpose: expose CDEadmin functionality as typed AI tools.

Source of truth:

- `CommandRegistry`;
- read-only platform service descriptors;
- TaskService task controls;
- module-declared AI capabilities.

No React/UI component becomes a tool.

### 4.2 Database Provider Connector

Purpose: query/manage an external database through CDEadmin's provider transport implementation
but with an AI-owned connection profile, credential reference and pool.

This satisfies "own connectors" without duplicating every wire implementation.

The AI connector SHALL NOT borrow the current human session.

### 4.3 ScratchBird SBsql Connector

Purpose: native ScratchBird access through unified SBsql.

Capabilities may include:

- recursive schema discovery;
- normalized native query/DDL/DML;
- cross-compatibility/cross-emulation query where engine grants permit;
- ScratchBird-native functionality not visible in legacy dialects.

Cross-surface access is separately policy-gated.

### 4.4 ScratchBird Compatibility Connector

Purpose: interact as a legacy client through one selected ScratchBird compatibility parser.

Required fields:

```text
dialect/parser profile
connection endpoint
AI principal credential ref
sandbox/workarea identity
visible database/catalog/schema context
read/write policy
```

Cross-emulation is prohibited.

### 4.5 ScratchBird MCP Connector

Purpose: consume the existing ScratchBird external AI/MCP layer.

MCP remains outside the engine. The connector MUST treat MCP tools as remote capabilities with their
own auth, schema, version and approval requirements.

### 4.6 Generic MCP Connector

Purpose: connect to approved third-party/enterprise MCP services.

It SHALL negotiate/record protocol version and SHALL support adapter profiles for older MCP servers.

New implementations SHOULD target the current MCP 2026-07-28 stateless model while retaining
explicit compatibility adapters rather than assuming every server is current.

### 4.7 External tool/service connector

Optional class for approved REST/GraphQL/custom enterprise tools that are registered into the same
policy/approval/audit model.

---

## 5. Connector lifecycle

States:

```text
unconfigured
disabled
validating
ready
degraded
reauthorization_required
permission_failed
version_incompatible
unreachable
revoked
error
```

Transitions are explicit.

`ready` requires:

- configuration structurally valid;
- credential reference resolvable;
- transport test successful;
- capability discovery successful or statically pinned;
- policy validation successful.

A connector MAY be used in `degraded` only for capabilities positively known to remain safe.

---

## 6. Model provider profiles

A model provider profile is independent of database connectors.

Fields:

```text
profile_id
display_name
runtime_class:
  local
  self_hosted
  remote
endpoint_ref?
model_id
credential_ref?
tool_calling_support
structured_output_support
context_limit?
output_limit?
streaming_support
data_residency
retention_statement
approved_classification_max
cost_model?
enabled
```

The module SHALL support multiple profiles.

The model connector receives only the context permitted by `AIDataPolicy`.

---

## 7. AI data-exposure levels

Context items use one of:

```text
IDENTITY_ONLY
METADATA_SUMMARY
SCHEMA
STATISTICS
SAFE_SAMPLE
FULL_ALLOWED_CONTENT
```

Default for a remote model: `METADATA_SUMMARY`.

`FULL_ALLOWED_CONTENT` is not a wildcard. It still obeys resource/column/row security and
classification.

Sensitive columns may be:

```text
excluded
masked
tokenized
aggregated_only
allowed
```

Raw secret fields are always `excluded`.

---

## 8. Model/data egress classes

Every ModelProfile declares the highest allowed classification:

```text
PUBLIC
INTERNAL
CONFIDENTIAL
RESTRICTED
REGULATED
SECRET_REFERENCE_ONLY
```

Actual organizations may rename these through policy mapping, but the implementation needs an ordered
classification lattice.

The data sent to a model must satisfy both:

- content classification <= model profile allowed classification;
- human + AI connector permissions permit that content.

---

## 9. Prompt-injection and untrusted content

The following are always untrusted:

- table/column names and comments;
- document text;
- database rows;
- stored procedures/source;
- API descriptions;
- DDN notes;
- trace/log messages;
- external web/tool results;
- MCP tool descriptions from unapproved servers.

The orchestration layer SHALL mark untrusted data in the model request and SHALL never permit data
text to alter:

- tool allowlist;
- connector selection policy;
- approval requirements;
- secret policy;
- system/developer instructions.

---

## 10. CDEadmin tool publication

Every CDEadmin command may declare:

```text
ai_exposure:
  hidden
  read_only
  draft_only
  executable

ai_risk_class
ai_argument_schema
ai_result_schema
ai_context_cost_hint
```

Default for existing commands with no declaration: `hidden`.

The AI Tool Catalog is generated from these declarations.

---

## 11. Risk classes and default approval policy

Exact default classes:

| Class | Meaning | Default AI behavior |
|---|---|---|
| R0 | metadata/status read | auto allowed if scoped |
| R1 | bounded data read/query | auto allowed if data/egress policy permits |
| R2 | draft generation / local project edit | may execute after showing diff; autosave policy configurable |
| R3 | project publish/deploy preparation, no live mutation | explicit review |
| R4 | reversible live database mutation | explicit approval for each plan/run |
| R5 | destructive/security/admin/privilege mutation | typed confirmation; no standing preapproval by default |
| R6 | production cutover/failover/delete/irreversible or high-impact operation | typed confirmation + high-impact permission; optional dual approval; AI never self-approves |
| R7 | secret material disclosure/export | forbidden to AI model; only secret reference operations may exist |

A site policy may make a class stricter, never weaker than backend authorization.

---

## 12. Query policy

Each database connector has exact query budgets:

```text
allow_read
allow_write
allow_ddl
allow_transaction_control
allow_explain
allow_system_catalog
allow_cross_surface (ScratchBird SBsql only)
max_rows
max_result_bytes
max_statement_seconds
max_statements_per_plan
max_parallel_queries
allowed_namespaces/resources
blocked_namespaces/resources
allow_temp_objects
allow_stored_procedure_call
allow_external_side_effect_functions
```

Default:

```text
allow_read = true
allow_write = false
allow_ddl = false
allow_transaction_control = false
allow_explain = true
allow_cross_surface = false
max_rows = 1000
max_result_bytes = 10 MiB
max_statement_seconds = 30
max_statements_per_plan = 10
max_parallel_queries = 4
```

These are CDEadmin AI defaults, not engine limits.

---

## 13. ScratchBird query-surface selection

The planner SHALL classify requested work:

```text
single compatibility surface
single native SBsql surface
cross compatibility surfaces within one ScratchBird database
external independent engines
```

Rules:

### Single compatibility surface

The AI may use that compatibility connector where the requested syntax/behavior is intentionally
legacy-compatible.

### Native ScratchBird functionality

Prefer SBsql.

### Cross-emulation within one ScratchBird database

Use SBsql only.

The Plan Review SHALL show:

`Cross-surface query — executed through ScratchBird native SBsql`

and list every canonical resource + compatibility alias involved.

### Independent external engines

The AI may orchestrate multiple connectors. It SHALL NOT call the result one atomic transaction
unless an explicit distributed/external transaction capability proves that property.

---

## 14. Planning mechanics

Before a live operation, the AI produces an `AIPlan`.

Plan structure:

```text
plan_id
base_context_revision
model_profile_snapshot
connector_snapshots[]
steps[]
dependencies[]
expected_effects[]
risks[]
required_approvals[]
validation_checks[]
rollback_or_recovery[]
estimated_budgets
```

Each step is typed:

```text
READ_METADATA
READ_DATA
COMPILE_QUERY
EXECUTE_QUERY
CREATE_ASSET_DRAFT
EDIT_ASSET
INVOKE_COMMAND
START_TASK
WAIT_TASK
VALIDATE
REQUEST_APPROVAL
```

The model never emits an opaque "run shell" step unless an explicitly registered enterprise tool
permits such behavior.

---

## 15. Plan validation

Plan validation is deterministic and occurs outside the model.

Checks:

- command/tool exists;
- arguments validate;
- connector capability exists;
- resource is in scope;
- initiating user may delegate;
- AI principal may access;
- risk class correct;
- required approvals present;
- budgets not exceeded;
- connector/version healthy;
- ScratchBird access-surface rules satisfied;
- write target environment visible;
- stale resource/asset revisions detected.

A failed validation step cannot be overridden by model text.

---

## 16. Approval mechanics

Approval binds to:

```text
plan_id
plan_revision/hash
specific step(s)
specific resources
specific environment
specific connector/principal
expiry
approver identity
```

If any bound field changes, approval is invalid.

Standing approvals MAY exist only for bounded R0-R3 operations and explicitly allowed R4 workflows.
R5/R6 standing approval is disabled by default.

---

## 17. Execution mechanics

Execution is performed by CDEadmin services, not the model.

```text
approved plan
  ↓
orchestrator
  ↓
CommandRegistry / QueryService / TaskService
  ↓
AI connector identity
  ↓
backend authorization
```

The model may observe results and propose subsequent steps.

It cannot mark a failed backend action successful.

---

## 18. Session and conversation model

Session modes:

```text
ASK
DISCOVER
ANALYZE
DRAFT
PLAN
OPERATE
```

`OPERATE` does not bypass approval.

A session stores:

- model profile;
- agent profile;
- connector set;
- explicit context refs;
- messages according to retention policy;
- plans;
- approvals;
- tool invocations;
- result/evidence refs.

Raw secrets are never stored.

---

## 19. Context manager

Context may contain:

- ResourceRef;
- AssetRef;
- DataProductRef;
- BusinessTermRef;
- Query/Task/DiagnosticRef;
- selected text/source snippets;
- explicit safe samples.

Every context item visibly shows:

```text
identity
source
exposure level
classification
freshness
token/size estimate
```

Users can remove/limit individual items.

---

## 20. Database result handling

Results are stored/handled by CDEadmin.

Only model-required summaries or approved slices are sent to the model.

Large result flow:

```text
database result
  ↓
CDEadmin protected result store
  ↓
local summarization/statistics/filter
  ↓
approved model context extract
```

This prevents a 1M-row result from being sent blindly to the model.

---

## 21. CDEadmin-wide functional access

The module shall expose all AI-eligible CDEadmin functionality across:

- Data Explorer;
- Projects/Git;
- DDN;
- Data Discovery;
- Query/editors;
- Schema Comparison;
- Migration;
- ETL;
- CDC;
- Lineage;
- Data Quality;
- Contracts;
- API Designer;
- Analytics/Cubes;
- Dashboards;
- Forms;
- Automation;
- Tracing;
- Replication;
- ML/Vector;
- administration/monitoring;
- provider-native actions.

This occurs through capability/tool publication, not screen automation.

---

## 22. Tool result schemas

Every AI tool must return a structured result envelope:

```text
status:
  success
  partial
  refused
  failed
  approval_required
result
diagnostics[]
resource_refs[]
asset_refs[]
task_refs[]
next_allowed_actions[]
audit_ref
```

Raw exceptions are normalized.

---

## 23. Connector health

Health dimensions:

- transport;
- authentication;
- authorization;
- capability discovery;
- version compatibility;
- latency;
- rate limit;
- model provider availability;
- database session pool;
- task execution.

Overall `ready` is not shown if any mandatory dimension is unknown.

---

## 24. Budgets

AgentProfile exact configurable budgets:

```text
max_model_input_tokens
max_model_output_tokens
max_tool_calls_per_turn
max_tool_calls_per_plan
max_database_queries_per_turn
max_database_rows_per_query
max_database_bytes_per_query
max_parallel_tools
max_run_minutes
max_background_tasks
max_estimated_cost_per_turn
max_estimated_cost_per_day
```

`0` means disabled only where field help explicitly says so. It never silently means unlimited.

Unlimited requires explicit `null`/`unlimited` selection and permission.

---

## 25. Cost and quota accounting

Where a model provider supplies usage/cost data, record:

- input tokens;
- output tokens;
- cached tokens where exposed;
- tool calls;
- estimated/actual provider cost;
- database/query resource consumption where available.

Unknown cost is displayed as `unknown`, not `0`.

---

## 26. Logging and audit

Audit records:

```text
session
initiator
AI profile/model
connector/principal
tool/command
redacted arguments
resource/asset targets
approval refs
backend result
task refs
timing
model/tool usage
policy decisions
```

Prompt/content retention is independently configurable.

A site may retain audit metadata while deleting conversational content.

---

## 27. Emergency controls

Administrators need:

- disable AI module globally;
- disable a model provider;
- revoke an agent profile;
- revoke connector credentials;
- disable all write tools;
- disable a specific command from AI;
- kill active AI runs;
- clear cached tool catalogs;
- force reauthorization;
- invalidate standing approvals.

Emergency controls do not delete audit evidence.

---

## 28. Model provider failure

If the model fails after actions have executed:

- actions remain as executed;
- audit remains;
- task continues according to TaskService;
- session marks AI unavailable;
- no automatic rollback unless an already-approved plan step defines it.

---

## 29. MCP mechanics

MCP is a connector protocol, not an engine privilege system.

For current MCP profiles:

- store protocol version;
- support stateless/current flow where available;
- cache tool catalogs only according to advertised/policy TTL;
- treat tool input/output schemas as untrusted until validated;
- use authorization discovery and issuer validation as required by the profile;
- map MCP asynchronous tasks to CDEadmin Task refs when possible;
- preserve remote tool identity/version.

Older ScratchBird/enterprise MCP profiles may be supported through versioned compatibility adapters.

Roots/sampling/logging from older MCP profiles SHALL NOT be treated as mandatory features of a current implementation.

---

## 30. Forms and surfaces

The machine `forms/` and `screens/` directories are normative.

No implementation AI may omit a configuration form because a field "could be in JSON".

Every editable policy has a zero-grey form or CodeEditor/structured source path and validation.

Primary surfaces:

1. AI Workbench
2. Sessions
3. Agent Profiles
4. Model Providers
5. Database Connectors
6. CDEadmin Capability Connector
7. MCP Connectors
8. Tool Catalog
9. Context Manager
10. Plan Review
11. Query Review
12. Action Approval
13. Runs/Tasks
14. Audit
15. Usage/Cost
16. Connector Health
17. AI Administration

---

## 31. AI Workbench exact layout

Left 288px:

```text
New session
Sessions
Pinned contexts
Agent profile
Connectors
Saved plans
```

Center:

```text
34px toolbar
conversation / structured plan / result views
composer footer
```

Right 340px Inspector:

```text
Context
Evidence
Current plan
Permissions
Budgets
Model/connector
```

Bottom drawer:

```text
Plan
Tool Activity
Problems
Tasks
Query Results
Audit
```

The composer SHALL show active AgentProfile, model profile, data-egress status, and whether live
actions are permitted.

---

## 32. Conversation message grammar

Message header:

```text
actor
time
model/profile for AI
status
```

AI answer body may include typed blocks:

```text
Explanation
Evidence
Query Draft
Result Summary
Plan
Warning
Approval Request
Task
Diff
```

A typed block is not a decorative card. Use ruled zero-grey sections.

---

## 33. Query review surface

Required columns/sections:

- connector/access surface;
- dialect;
- query text;
- resolved canonical resources;
- estimated risk;
- read/write classification;
- budgets;
- explain/plan if available;
- cross-surface badge;
- result exposure;
- Execute / Revise / Reject.

For ScratchBird cross-emulation the surface explicitly says `SBsql`.

---

## 34. Action approval surface

Required:

- exact command;
- target environment;
- target resources;
- effect;
- risk class;
- reversibility;
- dependencies;
- validations;
- AI rationale;
- model/provider;
- connector/database principal;
- approval expiration.

R5/R6 requires typed confirmation according to zero-grey destructive-dialog rules.

---

## 35. Agent Profile configuration

Sections:

```text
Identity
Model
Instructions
Database connectors
CDEadmin capabilities
Data exposure
Tool policy
Approval policy
Budgets
Retention
Autonomy
Notifications
```

Autonomy options:

```text
ASK_ONLY
DRAFT_ONLY
READ_ONLY_TOOLS
BOUNDED_EXECUTION
```

Default: `READ_ONLY_TOOLS`.

`BOUNDED_EXECUTION` still cannot bypass R4+ approvals.

---

## 36. Connector test

Testing is non-destructive by default:

- transport;
- authentication;
- principal identity;
- current database/context;
- metadata read;
- capability list;
- read-only trivial operation if provider defines one.

Write tests require separate explicit test fixture/environment and permission.

---

## 37. Menus

Use existing top-level taxonomy.

`Tools > AI Assistant` opens AI Workbench.

Context actions:

```text
Ask AI about…
Add to AI context
Explain with AI
Draft query with AI
Propose change with AI
```

The last two appear only if the selected AgentProfile permits them.

---

## 38. Accessibility

AI-generated prose must remain selectable/searchable text, not rasterized content.

Plan/diff/tool activity has table/tree alternatives.

Streaming output SHALL not steal keyboard focus.

Screen readers SHALL receive final/status messages without announcing every streamed token.

---

## 39. Performance

- stream model output without re-rendering the entire workbench;
- cap visible conversation DOM and virtualize older messages;
- tool/result payloads remain references when large;
- connector discovery runs out of render thread;
- cancel stale model request/tool plan where possible;
- TaskService owns continuing background work.

---

## 40. Required tests

Minimum security/functional tests:

1. AI cannot use interactive human DB session.
2. AI principal denied by DB remains denied even if human is DBA.
3. Human without delegation permission cannot cause AI principal to use a privileged write.
4. Approval cannot change connector/resource scope.
5. Raw secrets never enter model request.
6. Prompt injection in table comment cannot add a tool.
7. Compatibility connector cannot access sibling ScratchBird workarea.
8. Authorized SBsql connector can cross emulation scopes in one engine when explicitly enabled.
9. SBsql connector denied by engine privilege remains denied.
10. Remote model restricted to metadata cannot receive row samples.
11. Read-only query budget truncates/refuses over-limit results correctly.
12. R6 action cannot execute without required typed approval.
13. Plan revision invalidates old approval.
14. Tool failure returns structured failure, not success.
15. Closing UI does not hide continuing Task.
16. Revoking connector prevents next tool call and invalidates cached authorization as required.
17. MCP version incompatibility is reported explicitly.
18. CDEadmin command not declared AI-eligible is absent from Tool Catalog.
19. Cost unavailable displays unknown.
20. Audit export redacts sensitive values.

---

## 41. Low-capability AI prohibitions

The coding/design AI MUST NOT:

- embed an LLM into ScratchBird engine code;
- call engine internals directly;
- reuse user credentials for convenience;
- create a "superuser AI" default profile;
- implement cross-emulation by querying compatibility roots separately when SBsql permission is absent;
- mark compatibility catalog visibility as object access;
- expose every CommandRegistry command automatically;
- send full query results to the model automatically;
- store provider API keys in project JSON;
- allow the model to construct arbitrary URLs/tools;
- allow an AI action to approve itself;
- use `unknown` as `allowed`;
- use natural language as authorization evidence;
- hide which model/provider/principal performed an operation.


---

<!-- SOURCE: modules/02_DATA_DISCOVERY_INTELLIGENCE_MODULE_ZERO_GREY.md -->

# CDEadmin Data Discovery & Intelligence Module — Complete Zero-Grey Specification

**Module ID:** `cdeadmin.discovery_intelligence`  
**Specification:** 1.0  
**Status:** Normative committed first-party module  
**Product goal:** compete directly with enterprise data catalog, discovery, semantic-search, marketplace and search-to-analysis products.

---

## 0. Product definition

Data Discovery & Intelligence (DDI) answers:

```text
What data exists?
What does it mean?
Which copy/surface is canonical?
Can I see/use it?
Can I trust it?
Who owns it?
Where did it come from?
What uses it?
How current is it?
How popular is it?
What is the right asset for my task?
How do I get access?
How do I analyze/use it now?
```

It spans all connected providers and CDEadmin project assets.

DDI is not the Data Explorer. Data Explorer is operational/provider navigation. DDI is contextual,
ranked, governed discovery.

---

## 1. Required platform services

DDI adds these platform services:

```text
DiscoveryIndexService
BusinessKnowledgeService
UsageSignalService
DataProductService
AccessRequestService
DiscoveryRankingService
RecommendationService
CurationService
```

They consume:

- MetadataService;
- ResourceIdentityService;
- RelationshipGraphService;
- Lineage;
- Quality;
- Contracts;
- API;
- ETL/CDC/Migration;
- BI/Dashboards/Cubes;
- ML/Vector;
- Projects/Git;
- Query history/usage;
- provider/native metadata;
- security/permissions.

---

## 2. Discoverable entity classes

Canonical discoverable classes:

```text
LIVE_RESOURCE
PROJECT_ASSET
DATA_PRODUCT
BUSINESS_TERM
METRIC
DIMENSION
DOMAIN
GLOSSARY
API
DASHBOARD
REPORT
SEMANTIC_MODEL
CUBE
QUERY
PIPELINE
CDC_STREAM
CONTRACT
QUALITY_RULESET
LINEAGE_VIEW
ML_MODEL
VECTOR_INDEX
DDN_WORKSPACE
PERSON
TEAM
POLICY
```

A provider-specific native kind is retained under the canonical class, never erased.

---

## 3. Discovery document

Every indexed entity creates a versioned `DiscoveryDocument`.

Fields:

```text
document_id
canonical_ref
entity_class
native_kind
provider
connection/environment
name
qualified_names[]
authorized_aliases[]
description
business_terms[]
synonyms[]
domain_refs[]
owner_refs[]
steward_refs[]
tags[]
classification
schema_summary
native_metadata_summary
trust_signals
quality_signals
freshness_signals
usage_signals
lineage_signals
contract_signals
certifications[]
deprecation_state
access_state
search_text
embedding_refs[]
facet_values{}
updated_at
source_revision
index_revision
```

Raw credentials and protected row data are forbidden.

---

## 4. ScratchBird canonicalization

For ScratchBird:

- `canonical_ref` is the durable ScratchBird UUID-backed object.
- SBsql and each compatibility parser appearance are `AccessSurfaceRef`s.
- aliases are searchable only if the searching principal may discover that access surface.
- system/catalog projections are technical system entities, hidden from business search by default.
- one canonical object gets one default result.
- `Show access surfaces` expands aliases.

This rule applies to search, usage, lineage, recommendations, data products, quality, contracts and AI
context.

---

## 5. External legacy database identity

An actual external PostgreSQL server/table is a separate canonical resource from a ScratchBird
PostgreSQL compatibility appearance.

Name similarity never merges them.

Cross-system duplicate detection may recommend "similar/equivalent" but cannot merge provider
identities without explicit curation.

---

## 6. Indexing pipeline

Exact logical pipeline:

```text
source event / scheduled reconcile
  ↓
fetch authorized metadata
  ↓
resolve canonical identity
  ↓
extract native + normalized fields
  ↓
apply security/discoverability labels
  ↓
resolve ScratchBird access surfaces
  ↓
attach business knowledge
  ↓
attach lineage/quality/contract/trust
  ↓
aggregate usage
  ↓
generate lexical document
  ↓
generate semantic embedding if configured/policy permits
  ↓
update graph/recommendation features
  ↓
publish atomic discovery index revision
```

A failed enrichment source does not discard a valid existing document. It marks that signal stale/degraded.

---

## 7. Index source types

Sources:

```text
provider_metadata
provider_system_catalog
cdeadmin_project_assets
query_history
lineage
data_quality
contracts
dashboards/reports
semantic_models/cubes
ETL
CDC
migration
API definitions
ML/vector
DDN
Git metadata
external catalog adapter
business glossary
manual curation
```

Each source has independent health and last-success timestamp.

---

## 8. Index update modes

```text
EVENT_DRIVEN
INCREMENTAL_POLL
FULL_RECONCILE
MANUAL
IMPORT
```

Default strategy:

- event-driven for CDEadmin-authored changes;
- provider incremental polling where provider supplies change markers;
- scheduled reconcile for drift;
- full reconcile only on demand/schedule.

---

## 9. Security trimming

Security trimming occurs before a result is returned.

Access dimensions:

```text
DISCOVER_IDENTITY
DISCOVER_METADATA
VIEW_SCHEMA
VIEW_PROFILE_STATS
VIEW_SAMPLE
QUERY
EXPORT
REQUEST_ACCESS
ADMINISTER
```

A user may have `DISCOVER_IDENTITY` but not `VIEW_SCHEMA`.

Hidden entities MUST NOT leak through:

- result counts;
- facets;
- autocomplete;
- synonyms;
- recommendations;
- relationship counts;
- "similar assets";
- AI context.

---

## 10. ScratchBird alias security trimming

A canonical ScratchBird document can have several aliases.

For each searching principal, `authorized_aliases[]` is calculated per surface.

If a principal can discover only the PostgreSQL compatibility alias:

- that alias may match search;
- SBsql/private aliases are not returned;
- other compatibility aliases are not hinted;
- canonical engine UUID may remain internal unless policy permits showing it.

If the principal has SBsql/global-tree metadata visibility, visible aliases may be expanded.

---

## 11. Search modes

DDI supports:

```text
QUICK
KEYWORD
ADVANCED_FACETED
SEMANTIC
BUSINESS_TERM
FIELD
METRIC
SIMILAR_TO
GRAPH_RELATED
NATURAL_LANGUAGE
```

`NATURAL_LANGUAGE` can operate in two levels:

1. deterministic intent/parser + semantic retrieval without generative AI;
2. AI-enhanced interpretation through the separate AI Interface Module.

DDI core search works when AI is disabled.

---

## 12. Search query model

A query contains:

```text
text
mode
entity_classes[]
providers[]
domains[]
owners[]
certification[]
quality_state[]
freshness[]
access_state[]
classification[]
tags[]
native_kinds[]
environments[]
date/usage filters
sort
ranking_profile
page/cursor
```

Advanced Search uses the standard Query Builder form contract, not raw JSON.

---

## 13. Candidate retrieval

DDI combines:

```text
lexical inverted search
semantic/vector similarity
structured/facet filters
business knowledge expansion
graph-neighborhood retrieval
exact identifier/alias matching
```

Candidate lists are merged by canonical identity before ranking.

ScratchBird compatibility aliases do not create duplicate candidates.

---

## 14. Default ranking formula

Security filtering occurs before scoring.

Base score components, each normalized 0..1:

| Signal | Weight |
|---|---:|
| lexical/name/text relevance | 0.22 |
| semantic/vector relevance | 0.20 |
| business term/synonym/metric relevance | 0.12 |
| certification/governance trust | 0.10 |
| quality health | 0.08 |
| freshness | 0.07 |
| usage/popularity | 0.07 |
| user/team/domain contextual relevance | 0.05 |
| lineage/context relevance | 0.04 |
| documentation/ownership completeness | 0.05 |

Sum = 1.00.

Then apply bounded penalties/boosts:

```text
exact visible qualified-name match        +0.12
exact business-term match                 +0.08
active certified data product             +0.06
deprecated                                -0.30
retired/archived                          -0.45
critical quality failure                  -0.20
freshness SLA breach                      -0.15
no owner/steward                          -0.03
known unstable/breaking schema            -0.08
```

Clamp final score to 0..1.

Ties:

1. exact visible name;
2. certification;
3. quality;
4. freshness;
5. usage;
6. lexical canonical sort by stable ID.

Ranking profiles may change weights, but every active profile must sum to 1.00 and maintain a human-readable
explanation.

---

## 15. Ranking presets

Required presets:

### Balanced
Use default weights.

### Technical
Increase lexical/native metadata/lineage; reduce popularity.

### Business
Increase business-term/data-product/certification/ownership; reduce native metadata.

### Analysis
Increase semantic model/metric/quality/freshness/usage.

### Operational
Increase freshness/health/recent usage/provider-native status.

### ML
Increase vector/feature/model/lineage/quality relevance.

Administrators may create custom profiles with versioned weights.

---

## 16. Explainable ranking

Every result has `Why this result?`

Example:

```text
Matched exact alias: public.customer
Canonical object: ScratchBird customer UUID …
Certified: yes
Quality: 18/18 critical rules pass
Freshness: 12 minutes
Usage: high in Finance
Team relevance: 6 Finance users this month
Penalty: none
```

The explanation shows only information the principal may discover.

---

## 17. Business Knowledge Service

Entities:

```text
BusinessTerm
SynonymSet
Acronym
Domain
BusinessEntity
MetricDefinition
DimensionDefinition
PolicyRef
ClassificationRef
Owner/Steward role
```

Terms can map to multiple resources/assets.

Mappings have:

```text
relationship
confidence
origin
valid_from/to
approved_by
```

AI-suggested mappings remain suggestions until accepted.

---

## 18. Metric model

MetricDefinition:

```text
metric_id
name
description
business_formula
aggregation
grain
dimensions[]
filters[]
time_semantics
semantic_model_ref
implementation_refs[]
owner/steward
certification
version
```

Physical query text is an implementation, not the business identity of the metric.

---

## 19. Data Product model

DataProduct contains:

```text
product_id
name
summary
domain
owner/steward
status
version
resource_refs[]
asset_refs[]
semantic_model_refs[]
metric_refs[]
dashboard/report_refs[]
api_refs[]
contract_refs[]
quality_refs[]
lineage_refs[]
usage_docs
access_policy_ref
SLA/freshness summary
certification
release_notes
```

Statuses:

```text
DRAFT
REVIEW
CERTIFIED
DEPRECATED
RETIRED
```

---

## 20. Data Marketplace

Marketplace is a business-oriented discovery presentation.

Primary filters:

```text
Domain
Product type
Certified
Owner
Freshness
Quality
Access
Updated
Popularity
```

Default sort: ranking profile `Business`.

Use a dense ruled list/table, not card soup.

---

## 21. Trust model

Do not show one unexplained universal Trust Score as the only signal.

Expose:

```text
Certification
Quality
Freshness
Contract
Owner
Steward
Documentation completeness
Schema stability
Deprecation
Usage/popularity
Access
Last validation
```

An internal ranking fitness value may exist, but its components are explainable.

---

## 22. Certification workflow

Certification states:

```text
UNREVIEWED
IN_REVIEW
CERTIFIED
CERTIFIED_WITH_CONDITIONS
REJECTED
EXPIRED
REVOKED
```

Certification record:

- scope;
- reviewer(s);
- criteria;
- evidence;
- date;
- expiration;
- conditions;
- version/revision binding.

A material schema/contract change may mark certification `needs review` according to policy.

---

## 23. Usage Signal Service

Signals:

```text
query execution
dashboard/report view
API call
ETL read/write
CDC source/sink
model training/evaluation
AI context/use
search selection
favorite
access request
project reference
```

Store canonical resource identity plus access surface where relevant.

Popularity windows:

```text
24h
7d
30d
90d
all-time
```

Distinct-user/team counts are privacy-governed.

---

## 24. Usage normalization

One human query that touches the same ScratchBird object through two compatibility aliases should not be
counted as two independent datasets.

Canonical usage aggregates by UUID.

Per-surface usage is retained for operational insight.

---

## 25. Recommendation service

Required recommendation types:

```text
related_by_lineage
frequently_used_together
similar_semantics
same_business_term
same_domain
recommended_replacement
certified_alternative
used_by_team
downstream_dashboard
related_metric
related_api
```

Every recommendation exposes a reason.

---

## 26. Safe preview

Preview tabs:

```text
Schema
Profile
Sample
Lineage
Quality
Usage
Contract
Queries/Examples
Access Surfaces
```

Sample default maximum: 100 rows/documents.

Preview never ignores provider row/column/domain security.

Remote AI is not needed to preview.

---

## 27. Profiling

Profile metrics where applicable:

```text
row/document count
size
null/missing rate
distinct estimate
min/max
quantiles
common values
histogram/distribution
value length
freshness
last modification
classification
```

Provider exactness flag:

```text
exact
estimated
sampled
provider_reported
unknown
```

Profile statistics are not automatically sent to a remote AI if classification policy blocks them.

---

## 28. Access Request Service

Request:

```text
request_id
requester
target canonical resource/product
requested permission/role
environment
reason
duration/expiry
ticket/reference?
policy result
approvers
status
provider grant plan?
```

States:

```text
DRAFT
SUBMITTED
AUTO_APPROVED
PENDING_APPROVAL
APPROVED
DENIED
PROVISIONING
ACTIVE
EXPIRED
REVOKED
FAILED
```

Grant application is a separate consequential task.

---

## 29. Access discovery behavior

A result may be visible but inaccessible.

Show:

```text
Discoverable
Schema access
Preview access
Query access
Export access
Requestable
```

Do not use a single lock icon with ambiguous meaning.

---

## 30. Search-to-analysis

Actions from a discoverable asset:

```text
Open in Query
Open in SBsql
Open in compatibility dialect (when valid)
Explore in BI
Open semantic model/cube
Create dashboard
Add to ETL
Add to project
Ask AI
View lineage
View quality
Request access
```

Capability/security determines visibility.

For multiple resources within the same ScratchBird engine across compatibility surfaces:

`Open in SBsql` is the single-query path when authorized.

Compatibility dialect actions do not offer cross-root query.

---

## 31. AI-enhanced discovery

If AI Interface is enabled, DDI registers tools such as:

```text
search_data
explain_asset
find_certified_alternative
find_metric
explain_lineage
compare_candidates
draft_analysis_plan
```

The AI module owns model/provider/security/egress mechanics.

DDI does not embed its own hidden LLM.

---

## 32. AI enrichment

Optional enrichment suggestions:

```text
description
business term
synonym
domain
owner candidate
classification candidate
related asset
usage example
sample query
data product membership
```

Every AI suggestion stores:

```text
model/profile
time
source evidence
confidence? (if model/provider supplies meaningful value)
review state
```

No AI-generated governance fact becomes authoritative automatically.

---

## 33. Duplicate/entity reconciliation

Duplicate candidates arise from:

- names;
- schemas;
- fingerprints;
- lineage;
- semantic similarity;
- imported catalog identities.

For ScratchBird aliases sharing UUID, reconciliation is automatic because identity is already canonical.

For independent providers, merging identities is prohibited. The user may record `equivalent_to` or
`replacement_for` relationships instead.

---

## 34. Index backend abstraction

DiscoveryIndexBackend requires:

```text
upsert_document
remove_document
lexical_search
facet_search
semantic_search
get_document
bulk_revision
health
```

Optional:

```text
graph_neighbor_search
hybrid_native_search
```

The backend may be ScratchBird, another database/search service, or a CDEadmin-managed store.

Using ScratchBird's full-text/vector support does not place AI inside the engine; it is retrieval.

Embedding generation remains an external/module service.

---

## 35. Semantic embedding policy

Embedding source text may include:

- allowed name/description;
- business terms;
- permitted schema labels;
- tags;
- documentation excerpts.

It SHALL NOT automatically include protected row content.

Embedding provider may be local/remote and is governed by a Data Egress policy.

Embeddings are versioned by model ID/version and input fingerprint.

---

## 36. Saved searches and collections

Users can save:

- query;
- filters;
- ranking profile;
- display columns;
- scope.

Collections/favorites hold refs, not copied metadata.

A collection may be private, project, team or organization scope according to permissions.

---

## 37. Search feedback

Feedback actions:

```text
Useful
Not relevant
Wrong meaning
Outdated
Duplicate
Should be certified
Missing data
```

Feedback is a ranking/curation signal, not an automatic metadata mutation.

---

## 38. Search analytics

Administrative metrics:

```text
search count
unique searchers
zero-result rate
abandonment
click/select rate
time to useful selection
access-request conversion
top terms
zero-result terms
low-confidence queries
popular assets
deprecated assets still selected
unowned popular assets
quality-failing popular assets
```

Respect privacy thresholds.

---

## 39. Zero-result management

A zero-result query produces:

- spelling/synonym suggestions;
- broadened filter suggestion;
- related business terms;
- access-limited hint only if policy permits revealing that possibility;
- `Report missing data/term`.

It SHALL NOT reveal names of security-hidden assets.

---

## 40. Index administration

Admin can configure:

- sources;
- schedules;
- event subscriptions;
- field weighting;
- synonym dictionaries;
- stop words;
- semantic model/embedding provider;
- ranking profiles;
- retention;
- usage windows;
- curation workflows;
- security trim test;
- reconcile scope.

Configuration changes are versioned.

---

## 41. Index health

Per source:

```text
state
last successful crawl/event
last attempted
documents added/updated/deleted
errors
permission failures
stale count
queue depth
average latency
```

Global index health cannot be green if mandatory sources are unknown.

---

## 42. Index revision and atomicity

A full reconcile builds a candidate revision.

Publish rules:

- validation succeeds;
- security labels present;
- canonical IDs unique;
- required facets valid.

The active search revision switches atomically.

Failed reconcile retains previous good revision and shows degraded/stale status.

---

## 43. Curation queue

Queue items:

```text
missing owner
missing description
unmapped business term
AI suggestion
duplicate candidate
stale certification
quality failure on popular asset
broken lineage
unresolved access surface
zero-result term
```

Queue supports assignment, due date, comments and resolution evidence.

---

## 44. ScratchBird Access Surfaces Inspector

Every ScratchBird canonical object can expose a technical `Access Surfaces` tab.

Columns:

```text
surface
dialect
visible qualified name
sandbox/workarea
discoverable
queryable
mutable
cross-surface
compatibility evidence/version
usage 30d
```

`cross-surface` is `No` for compatibility parsers and `Engine-authorized` for SBsql.

---

## 45. Recursive schema browser

For ScratchBird native discovery, the technical browser supports recursive schema paths without flattening them
to a two-level database/schema model.

Breadcrumbs preserve full recursive path.

Compatibility-surface browsing uses the compatibility root the client sees.

---

## 46. Business glossary

Glossary Explorer supports:

- terms;
- synonyms;
- acronyms;
- domains;
- relationships;
- mapped resources/assets/metrics;
- owner/steward;
- status;
- history.

Term statuses:

```text
DRAFT
REVIEW
APPROVED
DEPRECATED
RETIRED
```

---

## 47. Metric/semantic search

Search recognizes metric identity separately from physical columns.

Query:

`monthly recurring revenue`

should prefer an approved MetricDefinition/DataProduct over a random column named `mrr_tmp` where ranking evidence
supports that choice.

---

## 48. Data 360 detail surface

Header:

```text
name
canonical type/native kind
provider/environment
certification
quality
freshness
access
owner
```

Tabs:

```text
Overview
Schema
Profile
Lineage
Quality
Usage
Contracts
Related
Queries/Examples
Access Surfaces
History
```

Actions are capability-driven.

---

## 49. Field 360

For a field/property:

```text
type/native type
semantic domain
nullability/presence
classification
quality
profile
business term
field lineage
usage
sample permission
```

Nested document/graph/wide-column semantics remain native.

---

## 50. Data Product Editor

Sections:

```text
Identity
Purpose
Domain
Members
Metrics
Semantic models
Dashboards/reports
APIs
Contracts
Quality
Lineage
Access
SLA/Freshness
Ownership
Certification
Release notes
```

Adding a member creates a ref, not a copy.

---

## 51. Marketplace layout

Left:

```text
Domains
Types
Certified
Quality
Freshness
Access
Owner
```

Center dense list:

```text
status mark
name
one-line purpose
domain
certification
quality
freshness
access
usage
```

Right Inspector:

```text
Purpose
Owner
Trust
How to use
Lineage
Access
```

---

## 52. Advanced Search builder

Rows:

```text
Field | Operator | Value
```

Operators depend on field type.

Groups support AND/OR with maximum nesting depth 5.

Raw expert query syntax MAY be offered in CodeEditor but the structured builder remains available.

---

## 53. Ranking configuration workbench

Two-pane comparison:

```text
left: ranking weights/penalties
center: test query results BEFORE
right: test query results AFTER
bottom: score explanation
```

Weights must total 1.00 before save.

Changing ranking config does not affect active profile until `Publish`.

---

## 54. Access request form

Required fields:

```text
target
requested access
environment
reason
duration
project/use case
data handling acknowledgement if required
```

Never ask for a password.

---

## 55. Search visibility test

Admin tool:

- select a user/principal;
- enter query;
- show exactly which documents/facets were admitted/filtered and why;
- no ability to bypass permission by switching to admin mode unless the tester itself is authorized.

This is essential for diagnosing security-trimmed search.

---

## 56. DDI API

DDI exposes a typed internal/public service API according to deployment policy:

```text
search
get_entity
get_related
get_product
get_business_term
get_metric
get_trust
get_access_state
request_access
```

External exposure is optional and separately authenticated.

The AI Interface consumes the internal service, not screen HTML.

---

## 57. DDI MCP publication

If CDEadmin publishes DDI through MCP, publication occurs through the AI Interface/approved MCP server layer with
the same search security trimming.

The DDI module does not create an ungoverned second MCP server by default.

---

## 58. Menus

Existing taxonomy additions:

`Tools > Data Discovery`

Context actions on resources/assets:

```text
Open in Discovery
Add to Data Product
Map Business Term
Request Certification
Find Related Data
Find Certified Alternative
```

No new top-level menu.

---

## 59. Accessibility

Graph/relationship views require table/tree equivalents.

Ranking explanations are text.

Trust/status indicators always have labels, not just colors/icons.

Search suggestions are keyboard navigable and announce result count, not every keystroke result row.

---

## 60. Performance targets and budgets

Default interactive budgets:

```text
quick autocomplete: first result batch <= configured 500ms target when backend healthy
search page: 50 results
maximum direct page size: 200
facets: top 50 values per facet unless paged
graph first hop: 100 nodes soft cap
graph visible: 500 nodes / 1000 edges soft cap
sample preview: 100 rows/documents
profile preview: bounded by provider/profile policy
```

These are product budgets; environments may tighten them.

Do not fake SLA compliance if backend cannot meet them.

---

## 61. Required tests

1. Security-hidden asset absent from results, counts, autocomplete and recommendations.
2. ScratchBird UUID visible through three parser aliases returns one canonical result.
3. Alias query ranks canonical result and explains matched surface.
4. Hidden ScratchBird alias is not disclosed.
5. External PostgreSQL table with same name remains separate from ScratchBird alias.
6. Business term synonym retrieves differently named technical asset.
7. Ranking explanation numerically reconciles with published profile.
8. Weight profile refuses save if weights != 1.00.
9. Deprecated asset receives exact configured penalty.
10. Quality/freshness signals change ranking without changing canonical identity.
11. Data Product member is a ref, not copy.
12. Access request moves through state machine and applies grant only via authorized task.
13. Search-to-analysis offers SBsql for cross-surface ScratchBird selection and refuses compatibility cross-root query.
14. DDI works with AI module disabled.
15. AI enrichment suggestion remains suggestion until accepted.
16. Full reconcile failure retains previous good index revision.
17. System catalog compatibility projections excluded from business search by default.
18. Usage from compatibility surfaces aggregates canonically.
19. `Why this result?` never leaks security-hidden alias/usage.
20. Search visibility test reproduces user trimming accurately.

---

## 62. Low-capability AI prohibitions

The implementation AI MUST NOT:

- index ScratchBird compatibility aliases as separate datasets;
- flatten all database models into table/column;
- make AI mandatory for keyword/business/semantic discovery;
- send raw protected data to an embedding provider automatically;
- reveal security-hidden names through autocomplete/facets/counts;
- use one unexplained "trust score" as the sole trust signal;
- merge external provider resources based on same name;
- silently upgrade imported metadata contracts;
- turn system catalogs into business data products;
- make legacy compatibility dialect capable of cross-root query;
- perform access grant directly from search UI without AccessRequest/Command/Task flow;
- mutate business glossary from AI suggestions without review;
- choose arbitrary ranking weights;
- show duplicate aliases unless `Show access surfaces` is enabled or surface detail is relevant.


---

<!-- SOURCE: FORM_CONTRACT_CATALOG.md -->

# Form Contract Catalogue

Total forms: **53**.

Every form declares exact zero-grey component types, defaults, validation, visibility, enablement, actions, states, keyboard behavior and security notes.


## cdeadmin.ai_interface

- `ai_agent_profile.form.json` — `ai.agent_profile` — Define the model, connector set, permissions and autonomy boundary for an AI agent.
- `ai_approval_policy.form.json` — `ai.approval_policy` — Map risk classes to approval and confirmation requirements.
- `ai_audit_export.form.json` — `ai.audit_export` — Export redacted audit metadata for a bounded scope.
- `ai_background_run.form.json` — `ai.background_run` — Start a bounded multi-step AI run without creating an unbounded autonomous agent.
- `ai_budget_policy.form.json` — `ai.budget_policy` — Bound model/tool/database/cost use.
- `ai_cdeadmin_capability_connector.form.json` — `ai.cdeadmin_capability_connector` — Expose selected CDEadmin services/commands to AI without screen automation.
- `ai_connector_test.form.json` — `ai.connector_test` — Run and display non-destructive connector validation.
- `ai_context_exposure.form.json` — `ai.context_exposure` — Choose an explicit resource/asset and exposure level.
- `ai_data_egress_policy.form.json` — `ai.data_egress_policy` — Control exactly what database/project information may be sent to a model provider.
- `ai_database_connector.form.json` — `ai.database_connector` — Create a dedicated AI-owned database connection profile.
- `ai_emergency_controls.form.json` — `ai.emergency_controls` — Immediately restrict AI access without deleting audit history.
- `ai_high_risk_approval.form.json` — `ai.high_risk_approval` — Confirm an R5/R6 action without allowing the model to self-authorize.
- `ai_instruction_asset.form.json` — `ai.instruction_asset` — Edit organization/project instructions supplied to a model without overriding hard policy.
- `ai_mcp_connector.form.json` — `ai.mcp_connector` — Configure an approved MCP server as an external AI tool connector.
- `ai_model_provider.form.json` — `ai.model_provider` — Configure a local/self-hosted/remote model provider without database authority.
- `ai_new_session.form.json` — `ai.new_session` — Start a bounded AI session with explicit profile and connectors.
- `ai_plan_review.form.json` — `ai.plan_review` — Review validated steps and required approvals before execution.
- `ai_principal_binding.form.json` — `ai.principal_binding` — Bind an Agent/Connector to a dedicated database/CDEadmin identity without impersonating the user.
- `ai_query_policy.form.json` — `ai.query_policy` — Define exact database operation limits for one or more AI connectors.
- `ai_query_review.form.json` — `ai.query_review` — Review a compiled query before model or user executes it.
- `ai_resource_scope.form.json` — `ai.resource_scope` — Constrain a connector/profile to explicitly allowed/blocked database resources.
- `ai_retention_policy.form.json` — `ai.retention_policy` — Control conversation, tool-result and audit retention independently.
- `ai_tool_policy.form.json` — `ai.tool_policy` — Choose which CDEadmin commands/services the AI can see, draft or execute.

## cdeadmin.discovery_intelligence

- `discovery_access_approval.form.json` — `discovery.access_approval` — Approve or deny a request with provider-grant preview.
- `discovery_access_request.form.json` — `discovery.access_request` — Request governed access without asking the user to find an administrator.
- `discovery_advanced_search.form.json` — `discovery.advanced_search` — Build explicit structured filters and boolean groups.
- `discovery_business_term.form.json` — `discovery.business_term` — Define a governed term and map it to data/metrics/assets.
- `discovery_certification_profile.form.json` — `discovery.certification_profile` — Define evidence required to certify a product/resource/metric.
- `discovery_certification_request.form.json` — `discovery.certification_request` — Submit a resource/product/metric for governed certification review.
- `discovery_certification_review.form.json` — `discovery.certification_review` — Review evidence and bind a certification decision to an exact revision.
- `discovery_collection.form.json` — `discovery.collection` — Create/edit a collection of references without copying metadata.
- `discovery_curation_resolution.form.json` — `discovery.curation_resolution` — Resolve a specific discovery curation issue with evidence.
- `discovery_data_product.form.json` — `discovery.data_product` — Create or edit a curated governed data product.
- `discovery_domain_editor.form.json` — `discovery.domain_editor` — Define a business/data domain used for discovery organization and context.
- `discovery_duplicate_review.form.json` — `discovery.duplicate_review` — Record equivalence/replacement relation without merging independent provider identities.
- `discovery_embedding_config.form.json` — `discovery.embedding_config` — Configure semantic retrieval without making generative AI a required dependency.
- `discovery_enrichment_review.form.json` — `discovery.enrichment_review` — Review AI/imported enrichment before it becomes governed metadata.
- `discovery_external_catalog_source.form.json` — `discovery.external_catalog_source` — Import metadata from an external catalog/BI platform as evidence, not canonical provider identity.
- `discovery_index_backend.form.json` — `discovery.index_backend` — Configure lexical/vector/facet index storage without binding DDI to one database engine.
- `discovery_index_source.form.json` — `discovery.index_source` — Configure metadata/asset/usage ingestion into DiscoveryIndexService.
- `discovery_metric.form.json` — `discovery.metric` — Define a business metric separately from any one physical query.
- `discovery_owner_steward.form.json` — `discovery.owner_steward` — Assign accountable business/technical roles to discoverable entities.
- `discovery_preview_policy.form.json` — `discovery.preview_policy` — Set bounded sample/profile behavior by classification/environment.
- `discovery_quick_search.form.json` — `discovery.quick_search` — Run governed enterprise discovery search.
- `discovery_ranking_profile.form.json` — `discovery.ranking_profile` — Configure exact ranking weights, boosts and penalties with before/after testing.
- `discovery_recommendation_policy.form.json` — `discovery.recommendation_policy` — Enable/disable explainable recommendation families and freshness limits.
- `discovery_saved_search.form.json` — `discovery.saved_search` — Persist query/filter/ranking settings, not result copies.
- `discovery_search_feedback.form.json` — `discovery.search_feedback` — Record explicit relevance/metadata feedback without directly mutating ranking or metadata.
- `discovery_synonyms.form.json` — `discovery.synonyms` — Manage governed search synonyms/acronyms without hidden ranking magic.
- `discovery_system_surface_settings.form.json` — `discovery.system_surface_settings` — Control technical visibility of compatibility catalogs without promoting them to business datasets.
- `discovery_usage_policy.form.json` — `discovery.usage_policy` — Control how usage signals contribute to ranking/recommendations without exposing individual behavior.
- `discovery_visibility_test.form.json` — `discovery.visibility_test` — Diagnose security-trimmed search for an authorized test principal.
- `discovery_zero_result_resolution.form.json` — `discovery.zero_result_resolution` — Convert a repeated no-result query into a governed synonym/term/data-gap action.

---

<!-- SOURCE: SCREEN_CONTRACT_CATALOG.md -->

# Screen Contract Catalogue

Total screens: **64**.

Every JSON screen contract validates against the existing CDEadmin Zero-Grey `screen-spec.schema.json`.


## cdeadmin.ai_interface

- `cdeadmin_ai_interface_action_approval.screen.json` — `cdeadmin.ai_interface.action_approval` — Approve a revision-bound consequential action with explicit target and identity.
- `cdeadmin_ai_interface_agent_profile_editor.screen.json` — `cdeadmin.ai_interface.agent_profile_editor` — Edit model, connectors, tool/data/approval/budget/retention policies.
- `cdeadmin_ai_interface_agent_profiles.screen.json` — `cdeadmin.ai_interface.agent_profiles` — List and manage governed AI AgentProfiles.
- `cdeadmin_ai_interface_ai_admin.screen.json` — `cdeadmin.ai_interface.ai_admin` — Globally restrict AI, revoke writes/approvals, and invalidate connectors.
- `cdeadmin_ai_interface_ai_workbench.screen.json` — `cdeadmin.ai_interface.ai_workbench` — Primary conversation, planning, evidence and execution surface.
- `cdeadmin_ai_interface_approval_policy.screen.json` — `cdeadmin.ai_interface.approval_policy` — Map R0-R6 action classes to approvals and expiry.
- `cdeadmin_ai_interface_audit.screen.json` — `cdeadmin.ai_interface.audit` — Search and inspect sessions, tools, plans, approvals and backend outcomes.
- `cdeadmin_ai_interface_budget_policy.screen.json` — `cdeadmin.ai_interface.budget_policy` — Set exact model/tool/query/time/cost budgets.
- `cdeadmin_ai_interface_cdeadmin_tool_catalog.screen.json` — `cdeadmin.ai_interface.cdeadmin_tool_catalog` — Inspect every AI-exposed CDEadmin service/command and effective policy.
- `cdeadmin_ai_interface_connector_health.screen.json` — `cdeadmin.ai_interface.connector_health` — Monitor model/database/MCP/CDEadmin connector health dimensions.
- `cdeadmin_ai_interface_context_manager.screen.json` — `cdeadmin.ai_interface.context_manager` — See exactly what the model may receive and at what exposure level.
- `cdeadmin_ai_interface_data_egress.screen.json` — `cdeadmin.ai_interface.data_egress` — Control metadata/schema/stats/sample/content exposure to models.
- `cdeadmin_ai_interface_database_connector_wizard.screen.json` — `cdeadmin.ai_interface.database_connector_wizard` — Create an AI connection without inheriting the human interactive session.
- `cdeadmin_ai_interface_database_connectors.screen.json` — `cdeadmin.ai_interface.database_connectors` — Manage dedicated AI-owned database connections and principals.
- `cdeadmin_ai_interface_mcp_connectors.screen.json` — `cdeadmin.ai_interface.mcp_connectors` — Manage approved remote/local MCP services with versioned profiles.
- `cdeadmin_ai_interface_mcp_tool_browser.screen.json` — `cdeadmin.ai_interface.mcp_tool_browser` — Inspect discovered remote tools, schemas, risk and allow/deny status.
- `cdeadmin_ai_interface_model_provider_editor.screen.json` — `cdeadmin.ai_interface.model_provider_editor` — Configure model runtime, credential reference and data policy metadata.
- `cdeadmin_ai_interface_model_providers.screen.json` — `cdeadmin.ai_interface.model_providers` — Manage external/local model runtime profiles.
- `cdeadmin_ai_interface_plan_review.screen.json` — `cdeadmin.ai_interface.plan_review` — Review deterministic validated AI plan steps before live execution.
- `cdeadmin_ai_interface_query_review.screen.json` — `cdeadmin.ai_interface.query_review` — Review dialect, canonical resource resolution, budgets and result exposure.
- `cdeadmin_ai_interface_retention_policy.screen.json` — `cdeadmin.ai_interface.retention_policy` — Set conversation/tool/audit retention independently.
- `cdeadmin_ai_interface_run_monitor.screen.json` — `cdeadmin.ai_interface.run_monitor` — Monitor AI orchestration runs and continuing CDEadmin/database tasks.
- `cdeadmin_ai_interface_scratchbird_access.screen.json` — `cdeadmin.ai_interface.scratchbird_access` — Configure SBsql vs compatibility parser vs MCP with sandbox/cross-surface rules.
- `cdeadmin_ai_interface_session_history.screen.json` — `cdeadmin.ai_interface.session_history` — Browse, reopen, archive and inspect saved AI sessions.
- `cdeadmin_ai_interface_tool_policy.screen.json` — `cdeadmin.ai_interface.tool_policy` — Allow/deny CDEadmin commands by module, risk and profile.
- `cdeadmin_ai_interface_usage_cost.screen.json` — `cdeadmin.ai_interface.usage_cost` — Show model tokens, tool calls, query use and known/unknown cost.

## cdeadmin.discovery_intelligence

- `cdeadmin_discovery_intelligence_access_inbox.screen.json` — `cdeadmin.discovery_intelligence.access_inbox` — Review submitted requests and provisioning status.
- `cdeadmin_discovery_intelligence_access_request.screen.json` — `cdeadmin.discovery_intelligence.access_request` — Request explicit discover/schema/preview/query/export rights.
- `cdeadmin_discovery_intelligence_access_surfaces.screen.json` — `cdeadmin.discovery_intelligence.access_surfaces` — Show canonical ScratchBird object plus visible SBsql/compatibility surfaces without duplication.
- `cdeadmin_discovery_intelligence_advanced_search.screen.json` — `cdeadmin.discovery_intelligence.advanced_search` — Structured facets/boolean groups/ranking profile.
- `cdeadmin_discovery_intelligence_certification_review.screen.json` — `cdeadmin.discovery_intelligence.certification_review` — Bind certification decision to exact revision/evidence.
- `cdeadmin_discovery_intelligence_certifications.screen.json` — `cdeadmin.discovery_intelligence.certifications` — Review certification status/requests/expirations.
- `cdeadmin_discovery_intelligence_collections.screen.json` — `cdeadmin.discovery_intelligence.collections` — Organize refs to useful products/resources/terms without copying metadata.
- `cdeadmin_discovery_intelligence_curation_queue.screen.json` — `cdeadmin.discovery_intelligence.curation_queue` — Resolve missing owners/descriptions/terms, stale certification and quality issues.
- `cdeadmin_discovery_intelligence_data_360.screen.json` — `cdeadmin.discovery_intelligence.data_360` — Complete contextual view of one canonical data resource/asset.
- `cdeadmin_discovery_intelligence_discovery_admin.screen.json` — `cdeadmin.discovery_intelligence.discovery_admin` — Configure defaults, recommendation/usage privacy, system-surface visibility and backend.
- `cdeadmin_discovery_intelligence_discovery_home.screen.json` — `cdeadmin.discovery_intelligence.discovery_home` — Search/browse data, terms, products and recent/recommended governed assets.
- `cdeadmin_discovery_intelligence_domains.screen.json` — `cdeadmin.discovery_intelligence.domains` — Browse business domains and ownership.
- `cdeadmin_discovery_intelligence_duplicate_review.screen.json` — `cdeadmin.discovery_intelligence.duplicate_review` — Record equivalence/replacement without merging independent provider identities.
- `cdeadmin_discovery_intelligence_enrichment_review.screen.json` — `cdeadmin.discovery_intelligence.enrichment_review` — Review AI/imported suggestions before governed metadata changes.
- `cdeadmin_discovery_intelligence_field_360.screen.json` — `cdeadmin.discovery_intelligence.field_360` — Context for one field/property including native semantics and field lineage.
- `cdeadmin_discovery_intelligence_glossary.screen.json` — `cdeadmin.discovery_intelligence.glossary` — Browse governed terms, synonyms, acronyms and mappings.
- `cdeadmin_discovery_intelligence_index_health.screen.json` — `cdeadmin.discovery_intelligence.index_health` — Monitor revisions, queues, errors and source freshness.
- `cdeadmin_discovery_intelligence_index_sources.screen.json` — `cdeadmin.discovery_intelligence.index_sources` — Configure and inspect all index/enrichment sources.
- `cdeadmin_discovery_intelligence_marketplace.screen.json` — `cdeadmin.discovery_intelligence.marketplace` — Business-focused dense list of governed data products.
- `cdeadmin_discovery_intelligence_metric_360.screen.json` — `cdeadmin.discovery_intelligence.metric_360` — Business semantic identity, formula, implementations, quality and usage.
- `cdeadmin_discovery_intelligence_metric_editor.screen.json` — `cdeadmin.discovery_intelligence.metric_editor` — Author metric semantics independently of physical implementation.
- `cdeadmin_discovery_intelligence_product_detail.screen.json` — `cdeadmin.discovery_intelligence.product_detail` — Consume a curated product with members, trust, access and usage guidance.
- `cdeadmin_discovery_intelligence_product_editor.screen.json` — `cdeadmin.discovery_intelligence.product_editor` — Author governed product membership and release metadata.
- `cdeadmin_discovery_intelligence_profile_stats.screen.json` — `cdeadmin.discovery_intelligence.profile_stats` — View exact/estimated/sample/provider-reported statistics.
- `cdeadmin_discovery_intelligence_ranking_profiles.screen.json` — `cdeadmin.discovery_intelligence.ranking_profiles` — List draft/published/retired ranking configurations.
- `cdeadmin_discovery_intelligence_ranking_tuner.screen.json` — `cdeadmin.discovery_intelligence.ranking_tuner` — Edit exact weights/penalties and compare before/after results.
- `cdeadmin_discovery_intelligence_related_graph.screen.json` — `cdeadmin.discovery_intelligence.related_graph` — Explore related/lineage/business/usage connections from a canonical entity.
- `cdeadmin_discovery_intelligence_safe_preview.screen.json` — `cdeadmin.discovery_intelligence.safe_preview` — Bounded provider-authorized sample without bypassing row/column security.
- `cdeadmin_discovery_intelligence_saved_searches.screen.json` — `cdeadmin.discovery_intelligence.saved_searches` — Manage saved query/filter/ranking definitions.
- `cdeadmin_discovery_intelligence_search_analytics.screen.json` — `cdeadmin.discovery_intelligence.search_analytics` — Measure whether users find useful data and identify metadata gaps.
- `cdeadmin_discovery_intelligence_search_results.screen.json` — `cdeadmin.discovery_intelligence.search_results` — Ranked security-trimmed canonical results with explainable relevance.
- `cdeadmin_discovery_intelligence_search_to_analysis.screen.json` — `cdeadmin.discovery_intelligence.search_to_analysis` — Choose how to use selected assets: query, SBsql, BI, AI, ETL, dashboard.
- `cdeadmin_discovery_intelligence_semantic_index.screen.json` — `cdeadmin.discovery_intelligence.semantic_index` — Configure embedding backend/model/content/egress.
- `cdeadmin_discovery_intelligence_synonyms.screen.json` — `cdeadmin.discovery_intelligence.synonyms` — Govern synonyms/acronyms used by search expansion.
- `cdeadmin_discovery_intelligence_term_editor.screen.json` — `cdeadmin.discovery_intelligence.term_editor` — Edit definition, synonyms, domain and mappings.
- `cdeadmin_discovery_intelligence_usage_popularity.screen.json` — `cdeadmin.discovery_intelligence.usage_popularity` — Explain canonical usage and per-surface usage over time.
- `cdeadmin_discovery_intelligence_visibility_test.screen.json` — `cdeadmin.discovery_intelligence.visibility_test` — Reproduce security trimming for an authorized principal.
- `cdeadmin_discovery_intelligence_zero_result_terms.screen.json` — `cdeadmin.discovery_intelligence.zero_result_terms` — Curate searches that found nothing without leaking hidden data.

---

<!-- SOURCE: SVG_CATALOG.md -->

# Normative SVG Catalogue

- `00_scratchbird_canonical_identity.svg` — Canonical UUID identity vs compatibility/SBsql/MCP access surfaces.
- `01_sbsql_cross_surface_security.svg` — Normative cross-surface authorization decision.
- `10_ai_workbench.svg` — AI Workbench
- `11_ai_connectors.svg` — AI Connectors
- `12_ai_scratchbird_connector.svg` — ScratchBird AI Connector
- `13_ai_policy_editor.svg` — AI Policy Editor
- `14_ai_tool_catalog.svg` — AI Tool Catalog
- `15_ai_plan_review.svg` — AI Plan Review
- `16_ai_query_review.svg` — AI Query Review
- `17_ai_action_approval.svg` — AI Action Approval
- `18_ai_audit_health.svg` — AI Audit and Health
- `20_discovery_search.svg` — Discovery Search
- `21_discovery_advanced_search.svg` — Advanced Discovery Search
- `22_discovery_data360.svg` — Data 360
- `23_discovery_marketplace.svg` — Data Marketplace
- `24_data_product.svg` — Data Product
- `25_glossary.svg` — Business Glossary and Metrics
- `26_access_certification.svg` — Access and Certification
- `27_discovery_usage.svg` — Discovery Usage
- `28_discovery_index_admin.svg` — Discovery Index Administration
- `29_discovery_ranking.svg` — Discovery Ranking Tuner
- `30_discovery_curation.svg` — Discovery Curation
- `31_discovery_analytics.svg` — Discovery Search Analytics
- `33_scratchbird_access_surfaces.svg` — ScratchBird Access Surfaces
- `34_discovery_to_analysis.svg` — Discovery to Analysis
- `02_ai_discovery_architecture.svg` — Module/platform/database authority relationship.
- `10_ai_workbench_light.svg` — AI Workbench — Light
- `20_discovery_search_light.svg` — Discovery Search — Light

---

<!-- SOURCE: references/SOURCE_EVIDENCE_SNAPSHOT.md -->

# Source / Standards Evidence Snapshot

**Checked:** 2026-09-10

## ScratchBird repository evidence

Current public ScratchBird documentation/code was inspected at commit
`f8f1f17c6d4c375e06e9b5a1a5212aaf2c52529b`.

Relevant paths:

- `docs/documentation/draft/book_assembly/output/v5-compatibility-and-reference-parsers.md`
  - one parser = one source dialect + wire protocol;
  - parser is untrusted;
  - parser resolves visible names to ScratchBird durable UUID identity;
  - accepted work lowers to SBLR and is rechecked;
  - engine owns identity, MGA transactions, storage and security;
  - compatibility is over one ScratchBird engine.
- `project/src/parsers/compatibility/common/compatibility_dialect.cpp`
  - compatibility evidence includes `catalog_projection_does_not_grant_base_object_access=true`;
  - `sbsql_global_tree_visibility_inherited=false`;
  - `sbsql_global_tree_visibility="sbsql_only"`.
- `docs/documentation/draft/AI_Integration_Guide/mcp_tools_and_control_surface.md`
  - current ScratchBird external AI/MCP layer exposes capability/discovery, schema metadata,
    query compile/execute, vector/hybrid retrieval, audit/governance, diagnostics, remote MCP,
    registry/routing tool families;
  - mutations require security/approval evidence according to the documented draft tool contract.

Repository:
https://github.com/scratchbird-software-inc/ScratchBird

## Model Context Protocol

Current public MCP specification release checked: `2026-07-28`.

Important implementation facts for the generic MCP connector:

- stateless protocol core;
- optional server discovery;
- header-based method/tool routing;
- cacheable list results;
- authorization hardening;
- Tasks extension;
- JSON Schema 2020-12 tool schemas;
- roots, sampling, and logging are deprecated for new implementations under the current release.

Sources:
https://blog.modelcontextprotocol.io/posts/2026-07-28/
https://modelcontextprotocol.io/

## Precedence

These external/current references do not override the product rules in this package.

The user-provided/current ScratchBird architecture rules and CDEadmin zero-grey/platform specifications
are controlling for product behavior. External protocol standards control only the interoperability
adapter that claims to implement them.
