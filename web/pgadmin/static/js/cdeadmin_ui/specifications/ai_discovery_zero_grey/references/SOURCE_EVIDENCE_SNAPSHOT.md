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
