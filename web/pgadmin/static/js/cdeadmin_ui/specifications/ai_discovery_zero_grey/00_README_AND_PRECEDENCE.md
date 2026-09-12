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
