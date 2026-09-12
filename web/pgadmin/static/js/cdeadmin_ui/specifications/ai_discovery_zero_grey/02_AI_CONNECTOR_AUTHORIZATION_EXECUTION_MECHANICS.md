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
