# CDEadmin artwork library

This directory is the canonical source location for artwork used by the
ScratchRobin interface. Runtime features refer to semantic icon keys and must
not import a provider- or command-specific SVG directly.

- `branding/` contains ScratchRobin product artwork.
- `engines/` contains database-engine identities.
- `commands/` contains action and tool glyphs plus their license records.
- `auth/` contains authentication-page artwork.
- `objects/` contains navigator and database-object artwork.
- `explain/` contains graphical query-plan nodes.
- `controls/` contains shared control glyphs, including `fonticon/` sources.
- `themes/` contains theme previews and other theme-owned raster artwork.
- `backgrounds/` contains workspace backgrounds.
- `tools/` contains tool-specific artwork.
- `profiles/` contains reviewed, portable packaged interface baselines.

Personal profiles and team or organization profiles distributed through the
preferences provisioning mechanism store semantic assignments, never file
system paths or SVG markup. An assignment is resolved through the validated
icon catalog, preserving attribution, safe fallback behavior, and packaging in
browser and desktop builds.

All authored runtime artwork belongs in this library. Generated build output,
documentation illustrations, specification mockups, and third-party package
assets are deliberately outside it. New runtime code must consume semantic
icon keys where the assignment contract applies; direct imports are reserved
for structural artwork that is not an assignable action or object identity.
