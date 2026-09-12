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
