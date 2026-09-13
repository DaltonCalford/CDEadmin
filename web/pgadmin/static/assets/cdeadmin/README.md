# CDEadmin artwork library

This directory is the canonical source location for artwork used by the
ScratchRobin interface. Runtime features refer to semantic icon keys and must
not import a provider- or command-specific SVG directly.

- `branding/` contains ScratchRobin product artwork.
- `engines/` contains database-engine identities.
- `commands/` contains action and tool glyphs plus their license records.
- `auth/` contains authentication-page artwork.
- `profiles/` contains reviewed, portable packaged interface baselines.

Personal profiles and team or organization profiles distributed through the
preferences provisioning mechanism store semantic assignments, never file
system paths or SVG markup. An assignment is resolved through the validated
icon catalog, preserving attribution, safe fallback behavior, and packaging in
browser and desktop builds.

Legacy feature-local pgAdmin artwork remains a compatibility input until its
own feature is migrated to the semantic `Icon` boundary. It is not a valid
source for new CDEadmin code.
