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
