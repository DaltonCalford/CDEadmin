# Low-Capability AI Handoff — AI Interface + Data Discovery & Intelligence

## Read exactly in this order

1. Existing CDEadmin Platform Architecture/UIUX Specification.
2. Existing CDEadmin Zero-Grey Component/Interaction Specification and machine tokens.
3. `00_README_AND_PRECEDENCE.md`.
4. `01_SCRATCHBIRD_CANONICAL_IDENTITY_ACCESS_SECURITY_MODEL.md`.
5. `02_AI_CONNECTOR_AUTHORIZATION_EXECUTION_MECHANICS.md` for AI work.
6. `03_DISCOVERY_INDEX_SEARCH_RANKING_MECHANICS.md` for Discovery work.
7. `04_AI_DISCOVERY_CROSS_MODULE_CONTRACT.md`.
8. The relevant module specification under `modules/`.
9. Its machine manifest, operations, schemas, screen contracts and form contracts.
10. The relevant SVG plate(s).

## ScratchBird rule you are forbidden to reinterpret

ScratchBird is one engine/storage/filesystem/MGA transaction/security authority. Compatibility
parsers are external access surfaces rooted in sandboxed recursive-schema workareas. They expose the
legacy catalog/API/ABI view expected by the selected legacy client. One ScratchBird object may have
many authorized compatibility names, but it remains one canonical UUID-backed object.

SBsql is ScratchBird's native unified/normalized language. Only an authenticated, authorized SBsql
session may issue one query across ScratchBird compatibility branches. A compatibility parser cannot
leave its sandbox. CDEadmin and AI MUST NOT work around that by querying hidden branches separately
and joining the results client-side.

## AI rule

AI is outside the database engine. Treat the AI/model runtime as another user/client. It has its own
model connector, database connection profiles, database principal, CDEadmin tool policy, scopes,
approvals, budgets and audit. It never inherits an interactive user's database session implicitly.

## Zero-guess rule

If a decision is not stated:

`SPEC-GAP: <precise missing decision>`

Do not choose a component-library default, Windows convention, pgAdmin behavior, random color,
permission interpretation, ranking weight, connector behavior or ScratchBird visibility rule.

## Completion rule

A screen is not a module. Completion requires all documented forms, states, commands, connectors,
permissions, schemas, tasks, audit paths, recovery paths, machine contracts, accessibility behavior
and tests.
