# ScratchRobin CDE Admin command icons

The SVG files in this directory are the curated command-action subset used by
the CDEadmin semantic command registry. They were copied without artwork
changes from the `@hugeicons/core-free-icons` 4.3.0 export in the CDEadmin icon
design workspace.

Hugeicons Free Icons are Copyright (c) 2025 Hugeicons and licensed under the
MIT License. The complete license is retained in `HUGEICONS_LICENSE.md`.

The SVGs contain no scripts, external references, linked images, or embedded
raster data. They use `currentColor`, allowing CDEadmin themes and user
accessibility preferences to control command-icon contrast.

## Semantic mapping

The application maps stable keys such as `action.connect`, `action.backup`,
`action.execute`, and `action.settings` to these presentation assets. Several
keys intentionally share a visual metaphor: create/add, edit/alter/rename,
delete/drop, save/commit, and disconnect/detach. The semantic keys remain
distinct so a theme or provider can replace one presentation later without
changing command definitions, security policies, shortcuts, or macros.

The authoritative key-to-asset mapping is in
`web/pgadmin/static/js/cdeadmin_ui/icons/registry.js`.
