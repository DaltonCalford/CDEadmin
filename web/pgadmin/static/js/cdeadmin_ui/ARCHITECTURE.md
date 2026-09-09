# CDEadmin UI boundary

Application features and database providers consume the public exports from
`sources/cdeadmin_ui`. The implementation libraries used to render those
components are private to this directory.

## Dependency direction

```text
feature/provider → cdeadmin_ui public API → adapter/compatibility component
```

Provider code must not import Material UI, docking, grid, select, editor,
chart, or drag/drop libraries directly. A specialized provider visualization
is registered through a CDEadmin renderer contract and still consumes shared
tokens, status, actions, workspace chrome, and accessibility behavior.

## Compatibility phase

The initial public components adapt the existing shared controls. This keeps
the Classic appearance and behavior stable while imports migrate. Adapter
implementations can then change without changing feature or provider APIs.

## Presentation

`foundations/presentation.js` owns reviewed profiles, custom-preference
normalization, contrast protection, semantic CSS variables, reduced motion,
and safe-mode persistence. Presentation settings are user preferences; display
and window placement remain device-local.

The emergency accessibility safe mode is toggled with Ctrl+Shift+0 or by
dispatching a `cdeadmin:accessibility-safe-mode` window event. Safe mode uses a
high-contrast theme, the low-vision sizing profile, reduced motion, and ignores
unsafe custom presentation values. It does not delete the user's saved profile.

## New components

The initial public surface includes actions, fields and choices, layout,
status and feedback, labelled dialogs, the virtualized data grid, and the
query/code editor. Existing compatibility widgets delegate into this boundary
incrementally so Classic behavior remains available throughout migration.

Compatibility adapters and heavy optional renderers use documented
`cdeadmin_ui` sub-entry points. This includes menus, tables, grids, workspace
navigation, the code/document editors, and future graph, chart, topology, and
model canvases. Keeping these out of the core barrel prevents unrelated entry
bundles from acquiring their transitive dependencies while still preventing
features from importing the underlying libraries.

`data/DataGrid` is the tabular-data boundary. It normalizes provider type
metadata into accessible typed cells, enforces read-only columns, carries
row/change-state semantics, and provides versioned layout persistence plus safe
CSV, TSV, and JSON interchange. Only column order and validated widths may be
stored in a local layout record; rows, queries, filters, and credentials are
never layout state. Provider-native document, graph, vector, key-value,
time-series, and search editors remain separate views and may use the grid for
summaries without flattening their native model into the grid contract.

Backend provider workspaces expose
`cdeadmin.provider-grid-workspace.v1` at bootstrap and
`cdeadmin.provider-grid-result.v1` on result and editable-data pages. These
contracts carry stable columns, native engine types, sensitivity/export state,
selection mode, persisted-layout scope, client-versus-provider interaction
authority, mutation lifecycle, native-view admission, and runtime activation
evidence. New built-in providers are rejected by the exhaustive adoption gate
unless their profile passes this boundary. Runtime verification remains a
separate observed gate and cannot be asserted by frontend code.

Tree nodes and actions use semantic icon keys from `cdeadmin_ui/icons`, such as
`engine.firebird`, `object.collection`, and `action.refresh`. Provider code must
not depend on an icon library component or presentation CSS class. The icon
registry maps stable keys to the current Classic classes or reviewed SVG assets,
and supports collision-checked provider additions with license and attribution
metadata. Native object variants may use taxonomy keys such as
`access.index.vector.hnsw`; unresolved variants fall back to their nearest
registered family rather than being presented as a SQL table.

`navigation/TreeActions` is the provider action boundary. It converts the
existing node-specific menus into immutable action descriptors and lets a
provider register additional executable actions by provider, engine, and
object type. Descriptors carry enabled reasons, confirmation semantics,
shortcuts, intent, and semantic icons. The Object Explorer resolves this model
at the point where a user opens a node menu, so providers do not need to import
the menu implementation.

## Commands and menu presentation

`commands/CommandRegistry` is the execution authority shared by menus,
context actions, toolbars, shortcuts, the future command palette, and recorded
macros. A command has a stable namespaced ID, version, handler, semantic icon,
permissions, optional security-group restrictions, visibility and enabled
predicates, confirmation intent, and an argument validator. Permission and
enabled checks are repeated at invocation time. They supplement rather than
replace authorization on backend endpoints.

Macros contain only ordered command IDs and JSON-safe arguments. They cannot
execute source text, cannot carry credential-like fields, cannot invoke a
command that opts out of macro use, and do not bypass command or backend
authorization. Commands that need credentials must refer to an authorized
connection profile; secrets remain in the existing credential authority.

`commands/MenuStructure` deliberately separates commands from where they are
shown. The current File, Connectors, Object, Tools, and Help arrangement is a
provisional compatibility structure, not a product API. Menu bindings populate
that structure and can later be remapped to a new menu renderer without
rewriting provider behavior. Existing menu declarations pass through
`CommandMenuAdapter`, giving legacy actions stable command identities during
the migration.

Application-menu commands use the curated, theme-aware Hugeicons Free command
set under `static/img/command_icons`. Legacy command definitions receive a
semantic action icon from their stable ID, name, label, and description; an
explicit `iconKey` always wins. The same resolved key is carried by browser,
context, macro, and desktop menu descriptors, while `command.default` provides
a visible fallback for commands that do not yet have a specialized metaphor.

The Connectors surface is populated from active release profiles. Each logical
engine has an explicit `connector.visibility.<engine>.set` command and a
per-user, default-off visibility preference. Visibility controls the Object
Explorer only: it does not install a driver, prove local availability, create a
connection, or grant access. Command customizations may change presentation or
further restrict visibility/enabled state, but cannot override permissions,
security-group restrictions, or runtime predicates.

## Tool identity and host windows

`workspace/ToolDescriptor` defines the versioned, immutable identity used to
reconstruct a tool. Descriptors contain only stable IDs, opaque backend
handles, placement, visible status summaries, and capability declarations.
They reject credentials, tokens, query/document content, form parameters, and
launch URLs. `ToolRegistry` owns restore, checkpoint, close-policy, capability,
and migration hooks by stable tool kind; layouts never serialize React nodes as
the durable tool identity.

`WorkspaceHost` is the only frontend boundary allowed to distinguish browser
and desktop capabilities. Browser coordination uses a non-authoritative
same-origin notification channel when available. Electron operations are
exposed through the constrained preload bridge and validate window ownership,
IDs, placements, and display targets in the main process. Native display
placement is reported as best-effort on Wayland.

`WorkspaceTransferClient` connects this host boundary to the authenticated,
owner-scoped backend workspace authority. Workspace, window, secret-free tool
descriptor, checkpoint-reference, and short-lived move state are durable.
Transfers use optimistic placement revisions and the sequence prepare,
destination restore, acknowledge, commit or abort. The bearer proof travels in
a dedicated request header and only its digest is stored. Cross-window drag
still remains disabled until the browser/Electron coordinators use this
authority and prove source-retention and crash-recovery behavior.

New public widgets require:

- A stable intent-based API without third-party types in public properties.
- Applicable default, focus, disabled, read-only, loading, validation, error,
  permission, and unavailable states.
- Keyboard behavior and a programmatic accessible name.
- Semantic tokens rather than feature-owned colors or fixed sizes.
- Focused regression tests and component-workbench coverage.
- Equivalent desktop and browser behavior or a declared host capability.
