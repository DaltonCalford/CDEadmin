# CDEadmin design-system machine contracts

These JSON resources are exact, unmodified copies of the corresponding
machine contracts in `CDEadmin_Zero_Grey_UI_Reference_Package`, integrated on
2026-09-11. They are executable inputs to the CDEadmin design-system layer,
not loose documentation and not feature-owned configuration.

`tokens.js` is the only JavaScript access boundary. Product components consume
semantic tokens and named contracts; they do not import these JSON documents
directly or substitute library-default colours, dimensions, layers, timings,
states, shortcuts, or menu placement.

Integrated authorities:

- `ui-tokens.json` and `cdeadmin-reference-tokens.css`: semantic presentation
  values and the reference CSS-variable projection.
- `component-contracts.json`: the 63 required public component contracts.
- `state-taxonomy.json`: shared loading, error, permission, connection, task,
  transaction, environment, and drag/drop state names.
- `standard-shortcuts.json` and `menu-taxonomy.json`: command presentation.
- `standard-dialogs.json`: About, unsaved, destructive, credential, simple
  input, and wizard behavior.
- `screen-spec.schema.json`: executable screen-spec validation.
- `svg-manifest.json`: the normative 26 reference plates used for visual QA.

The four implemented top-level screens under `../screens` validate against the
screen schema with no recorded specification gaps. Hash and source parity of
these files is checked as part of the implementation verification report.
