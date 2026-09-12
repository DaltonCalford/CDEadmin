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
