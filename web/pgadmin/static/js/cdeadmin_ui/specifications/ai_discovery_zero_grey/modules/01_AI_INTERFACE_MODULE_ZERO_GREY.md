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
