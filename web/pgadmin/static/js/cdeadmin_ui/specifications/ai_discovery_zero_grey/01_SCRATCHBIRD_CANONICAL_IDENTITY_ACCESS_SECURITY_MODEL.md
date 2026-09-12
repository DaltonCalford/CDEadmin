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
