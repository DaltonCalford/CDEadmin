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

## Compatibility strategy

Public components may adapt proven existing controls while imports migrate,
but the semantic CDEadmin contract is authoritative. The complete set of 63
named Zero-Grey components is registered in `components.js`; the machine
contract, executable implementation, behavioural tests, and screen
specifications must remain mutually consistent.

## Presentation

`foundations/presentation.js` owns reviewed profiles, custom-preference
normalization, contrast protection, semantic CSS variables, reduced motion,
and safe-mode persistence. Presentation settings are user preferences; display
and window placement remain device-local.

The emergency accessibility safe mode is toggled with Ctrl+Shift+0 or by
dispatching a `cdeadmin:accessibility-safe-mode` window event. Safe mode uses a
high-contrast theme, the low-vision sizing profile, reduced motion, and ignores
unsafe custom presentation values. It does not delete the user's saved profile.

## Visual QA identity

The shared `Theme` boundary installs the opt-in visual identity authority on
every themed CDEadmin page, including authentication pages and the workbench.
The login page switch persists the device-local QA mode under
`cdeadmin.qa.visual-identities.enabled.v1`. When enabled, every rendered HTML
and SVG element receives a short, monotonically assigned integer
`data-cdeadmin-qa-id`; dynamically
inserted content, React portals, open shadow roots, and accessible same-origin
frame documents are observed for their entire lifetime. A pointer hover or
keyboard focus displays the exact identifier in a non-interactive overlay.
While that hint is active, Ctrl/Cmd+Alt+C copies the identifier and displays a
short success confirmation; ordinary Copy remains untouched.
Disabling the mode removes the instrumentation and observers immediately.

Feature code may set `data-cdeadmin-qa-key` to give an important control or
surface stable internal semantics independent from its numeric QA reference.
Keys must be globally namespaced (`module.surface.control`), must describe the
control rather than its presentation, and must never contain a username, object
name, query, credential, or other user/provider data. Numeric identities are
never reused during an enabled QA session. QA identity is diagnostic
metadata only: it does not replace a DOM `id`, command ID, resource identity,
accessible name, permission check, or provider object identity.

## Design-system components

The public surface includes actions, fields and choices, layout, navigation,
status and feedback, the six normative dialog patterns, the virtualized data
grid, and the query/code editor. Existing compatibility widgets delegate into
this boundary so Classic behavior remains available during migration.

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
shown. The normative presentation taxonomy is File, Edit, View, Navigate, Run,
Data, Project, Tools, Window, and Help. Menu bindings and registered command
surfaces populate that structure without making menu position part of command
identity. Existing menu declarations pass through `CommandMenuAdapter`, giving
legacy actions stable command identities during the migration.

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
a dedicated request header and only its digest is stored. Cross-window movement
is admitted only through this authority. A host that does not supply the
necessary coordination capability rejects detach or move with an explicit
reason; it never serializes live UI state or credentials as a fallback.

## Platform controlling architecture

`platform/ModuleRegistry` owns lifecycle and atomically installs surfaces,
commands, activities, Inspector pages, toolbox pages, and status providers.
Module and dependency identifiers are stable namespaced IDs and module/service
versions use semantic versioning. Failed activation removes every partial
contribution. Active modules cannot be unregistered without deactivation.

The core service set is registered by `platform/PlatformServices`:

- `ProviderRegistry` records provider identity and factory authority without
  inferring capabilities from protocol compatibility.
- `ConnectionSessionService` owns connection/session handles, reconnect policy,
  state transitions, and observable transaction state.
- `NativeOperationService` returns cancellable operation handles before
  streaming results and keeps execution status distinct from finality.
- `MetadataCatalogService` keeps normalized metadata and provider-native
  metadata side by side, with generation-aware cache invalidation.
- `ResourceIdentityService` resolves stable live-resource references and never
  substitutes UI identity for provider authority.
- `CredentialReferenceService` resolves opaque credential references while
  rejecting raw secret material in profiles and project state.
- `LayoutPersistenceService` stores versioned device-local, secret-free layouts
  and applies explicit migrations.
- `RelationshipGraphService`, `TaskExecutionService`, and
  `FederatedSearchService` provide cross-module relationships, cancellable and
  retry-aware background execution, and permission-filtered search.

`WorkbenchContextService` is the shared active-context authority. Dock focus,
surface state, Project Explorer selection, connection/transaction status, and
module contributions flow through this service. Inspector, toolbox, status,
Problems, Output, and Tasks hosts resolve against that context rather than
hard-coding feature modules.

## Workbench shell

`shell/WorkbenchShell` implements the activity rail, live-resource and authored
project navigators, docked work surface, Inspector/Toolbox, bottom drawer, and
status bar. Navigation, Inspector, and drawer dimensions are bounded and stored
device-locally. A new application session deliberately starts with no activity
selected and with both navigator and Inspector closed; stored dimensions remain
available, but stale open-pane state is not restored. Activity commands reveal
the navigator as well as selecting its content. Explorer activities share one
persistent, QA-identifiable navigator drawer. Selecting the active explorer
activity again collapses the drawer to the rail and expands the workbench;
selecting an explorer while it is collapsed slides the same drawer open.
Switching explorer content does not replace the drawer DOM element or its QA
identity. There is no floating navigator-reveal control over the application
menu: the activity rail is the only navigator and workspace switcher. Query Tool,
command-line, schema-diff, and Preferences actions are icon-bearing activity
tabs, and the former vertical workspace toolbar is not rendered or retained.
Unavailable workspace tabs remain visible and disabled. Explicit resource or
project-asset selection opens the Inspector; deselection closes it, and the
Inspector has no detached reveal button. The main dock suppresses its tab strip
when a panel contains one tab and exposes the strip as soon as a second docked
surface is present. The welcome workspace consumes the same authoritative
activity catalog as the rail, presenting every activity as a responsive,
large-icon launcher rather than maintaining a second hard-coded catalog. The
legacy dock remains the panel layout engine behind the semantic shell boundary,
and focus changes are reflected into workbench context.

Live provider resources and authored project assets are intentionally separate.
`ProjectAssetService` provides authenticated membership, optimistic project and
asset versions, immutable asset revisions, safe relative paths, nested secret
rejection, and exact DDN snapshot validation. Project assets may bind to
ResourceRefs but do not become live database objects.

New public widgets require:

- A stable intent-based API without third-party types in public properties.
- Applicable default, focus, disabled, read-only, loading, validation, error,
  permission, and unavailable states.
- Keyboard behavior and a programmatic accessible name.
- Semantic tokens rather than feature-owned colors or fixed sizes.
- Focused regression tests and component-workbench coverage.
- Equivalent desktop and browser behavior or a declared host capability.

## DDN libraries

The versioned distributions under `static/vendor/ddn` are independently
reusable DDN libraries. Product code consumes them only through
`cdeadmin_ui/integrations/ddn`. The adapter validates the exact viewer and
Designer versions, validates source workspaces and snapshots, preserves DDN
session/change-plan authority, and binds persistence to an explicit AssetRef
save authority.

The browser loader fetches the byte-identical global distributions as ordered,
same-origin static assets using the application's generated-resource base. It
therefore works below a mounted application path without asking webpack to
interpret DDN's optional Node/source-module branches; concurrent requests are
deduplicated and failed loads are removed so a subsequent request can retry.

DDN source/workspace state is authoritative. Rendered SVG, scene geometry,
selection decoration, and source maps are projections and navigation evidence;
they are never a second editable model. CDEadmin does not reach into DDN
internal helpers or Designer shadow DOM. Database application remains an
explicit metadata comparison/change-plan workflow outside the DDN library.

The DDN module registers viewer and Designer surfaces plus all toolbar actions
as non-macro workbench commands. Its shared toolbox is populated from the exact
public Designer catalogue and definition creation executes through the public
session controller. Save state is merged into active workbench context and is
enforced by the close policy. No DDN action inspects or modifies library shadow
DOM or treats SVG output as editable source.

## Schema Comparison module

`modules/schema_compare` is the first first-party platform module built on the
controlling architecture. It owns the `cdeadmin.schema_compare.v1` asset and the
six work surfaces defined by the module specification: Compare Setup, Diff
Tree, Object Diff, Mapping Review, Change Plan, and Apply/Export Review. It
contributes those surfaces through the shared shell together with its Activity,
Inspector, bottom-drawer, and status entries; it does not create a private
compound dialog or a second navigation model.

Schema capture and native change rendering are provider-authoritative. A
provider must explicitly register every Schema Comparison adapter operation and
return its support state, provider version, evidence, warnings, and native
details. Protocol similarity, object names, or a successful connection never
imply support. Comparison follows stable resource identity, provider-native
stable identity, accepted mappings, and exact compatible qualified names in
that order. Rename candidates remain user decisions and are never accepted by
heuristic inference.

Change-plan generation always names the target side. Provider-native operations
must be materialized and validated before export or apply. Apply additionally
requires explicit provider support, a current successful validation, destructive
operation confirmation where applicable, permission revalidation, task
lifecycle reporting, and a durable audit reference. Assets, exports, tasks,
events, relationships, and search results contain references and redacted
diagnostics only; raw authentication material is rejected at both frontend and
backend persistence boundaries.

## Data Lineage module

`modules/lineage` owns the versioned `cdeadmin.lineage.v1` authored asset and
six independent workbench surfaces: Lineage Explorer, Field Lineage, Impact
Analysis, Timeline/Snapshot Compare, Evidence Inspector, and Ingestion Status.
The graph has an accessible table route that contains both nodes and edges;
interactive traversal is bounded to ten hops and 500 nodes/1,000 edges before
switching to a declared progressive/clustered presentation.

Lineage evidence retains its exact origin, confidence, temporal validity,
provider version, and native details. Reconciliation never upgrades foreign
keys into data-flow edges without independent flow evidence, never treats a
missing adapter as unsupported, and suppresses only inference-only
presentation while retaining the underlying evidence. Created, changed, and
expired edges are mirrored into the shared relationship authority and publish
the module's lifecycle events; removed graph content is also removed from that
shared authority.

A provider claiming Lineage support must implement declared-relationship
discovery, provider-query parsing, native-resource resolution, execution
evidence, and field-lineage capability reporting. Every result carries an
explicit support state, provider version, capabilities, native mechanisms,
version constraints, limitations, warnings, and runtime evidence. Canonical
ResourceRefs route by their declared provider identity; no SQL family,
protocol, or PostgreSQL fallback is inferred.

OpenLineage RunEvents are imported through a cancellable task and retain their
producer, schema URL, original event, and unknown facets for lossless export.
DDN output is a non-authoritative visual projection bound to stable Lineage
identities. Canonical project serialization excludes transient graph scans,
selection, counters, and credentials, while the backend independently checks
the full evidence, field transformation, reference, interval, and secret-free
asset contract.

## Data Quality module

`modules/quality` owns the versioned `cdeadmin.quality.v1` authored asset and
seven independent workbench surfaces: Quality Overview, Rule Set Designer,
Data Scope/Slice Designer, Validation Run Results, Profiler, Quality History,
and Schedule & Actions. Its rule model distinguishes quality dimension, rule
family, evaluation mode, threshold, severity, sampling policy, provider-native
details, and failure action. The runtime does not reduce provider-specific
quality mechanisms to a generic SQL query.

Live work uses a five-operation provider contract: rule-family discovery,
execution preparation, provider-native rule execution, bounded profiling, and
bounded violation-sample retrieval. Every response must include explicit
support state, provider/version evidence, capabilities, native mechanisms,
version constraints, limitations, warnings, and runtime evidence. Empty rule
discovery must carry an explicit native reason; unknown or partial support is
never reported as successful support.

Validation, profiling, and baseline capture use the shared cancellable,
retry-aware task authority. Rule failures and provider execution errors remain
distinct. Run results retain data revision, evaluation mode, provider version,
diagnostics, and stable result/task references. Violation samples are limited
to 100 rows, kept outside authored assets, hidden without
`quality.view_samples`, and cannot be exported without the separate
`quality.export_samples` permission.

Profiler suggestions carry a source-sample reference and source revision and
require an explicit acceptance command before becoming authored rules.
Baselines retain exact resource/revision evidence and compute deterministic
metric drift using explicit tolerances. Great Expectations import/export is
loss-aware: supported expectations map to typed rules, unknown source content
is preserved for round-trip, and CDEadmin rules with no safe inverse mapping
are declared as unmapped. Data Contract links use the shared relationship
authority, while project saves use optimistic revisions and recursive secret
rejection at both frontend and backend boundaries.

## Data Contract Manager module

`modules/data_contract` owns the versioned `cdeadmin.contract.v1` authored
asset and seven independent workbench surfaces: Contract Explorer, Contract
Editor, Schema & Resource Binding, Quality & SLA, Team/Roles/Support,
Compliance, and Version Diff. The canonical model retains logical
objects/properties, physical bindings, quality obligations, service levels,
serving interfaces, ownership/access roles and authoritative definitions; a
document, graph, vector or time-series object is not coerced into a relational
table merely to fit the shared UI.

ODCS import and export are version-declared and loss-aware. Import retains the
source apiVersion, kind and safe unknown top-level content, preserves native
object/property terminology, and never auto-activates an imported contract.
Export identifies the ODCS version and CDEadmin profile and recursively rejects
credential material. The source editor and canonical preview remain distinct,
and canonical project serialization excludes selection, task state, live
observations and other volatile runtime evidence.

Live contract operations use an explicit four-operation provider contract for
resource mapping, schema comparison, SLA observation and classification
validation. Every response carries a support state, provider version,
capability lists, native mechanisms, version constraints, limitations,
warnings and runtime evidence. Missing, read-only, partial and unsupported
responses fail closed. Metadata comparison and compliance never overwrite the
authored definition; structurally valid and live-compliant are independent
states, and unresolved documentation evidence remains unknown rather than
being reported as successful.

Lifecycle transitions are audited, reverse transitions require a reason, and
activation rechecks both structural validity and `contract.activate` authority.
Import, compliance and metadata comparison use shared tasks; contract/resource,
quality and authoritative-definition links use stable references and the shared
relationship graph. Project saves use optimistic versions and retain dirty
authored state on conflict. Provider support is not activated by this module:
each engine must register and verify its own versioned adapter without a SQL,
PostgreSQL or protocol-family fallback.

## ETL Designer module

`modules/etl` owns the versioned `cdeadmin.etl.v1` pipeline asset and seven
independently openable surfaces: Pipeline Designer, Mapping Editor, Schema
Propagation, Preview, Deployment, Run Monitor and Schedule. Its canonical
graph stores typed batch/stream ports, provider-native and semantic field
types, explicit lossy conversions, edge guarantees, parameter and credential
references, deployment bindings, schedules, tests and a visual layout that is
separate from execution meaning.

The graph editor supports semantic drag/drop and a keyboard/button alternative,
complete pipeline/node/port/edge/error-policy authoring, provider resource and
credential-reference pickers, and an accessible data-grid graph alternative.
Incompatible port modes are rejected before graph mutation. Bounded preview
defaults to 100 and never executes a sink write; stream preview requires an
explicit time or partition scope.

All live work passes through a five-operation ETL adapter contract. Each node
must have an explicit ResourceRef or deployment binding that identifies its
execution authority: a transform never borrows a neighboring provider merely
because an edge connects them. Deployment validates exact source/sink
capabilities and records provider pushdown location and reason. Runs report the
weakest stage guarantee, support explicit retry/reject/dead-letter/stop policy,
emit observed lineage evidence, and create a new audited attempt when resuming
from a checkpoint without replaying completed stages.

ETL project saves use optimistic revisions and independent backend schema,
reference, graph, bound and secret validation. Durable schema, quality,
resource and schedule relationships use the shared relationship graph. No
database provider is activated for ETL by this module alone; unregistered or
unbound execution remains unknown and fails closed.

## CDC Designer module

`modules/cdc` owns the deterministic `cdeadmin.cdc.v1` stream definition and
seven independently openable surfaces: CDC Designer, Source & Capture, Event
Mapping, Schema Evolution, Run Monitor, Event Inspector and Replay. The asset
keeps provider ResourceRefs, CredentialRefs, normalized and exact native
capture mechanism, start cursor, snapshot policy, filters, mappings,
redactions, sink serialization, delivery proof, evolution decisions, alerts
and visual layout separate from runtime counters and checkpoints.

The source workflow recognizes log-based, logical-replication, change-stream,
provider-stream, trigger, polling, MGA-history and external-connector capture
without assuming every provider exposes a transaction log. Initial snapshot
and continuous stream checkpoints remain distinct. An incremental snapshot
requires an explicit positive batch size. The exactly-once label is rejected
unless the definition contains source-capture, transport and sink-application
proof; live start separately validates the source mechanism, privileges,
position and sink through exact provider adapters.

All five CDC task types use the shared Task service. Start composes provision,
optional snapshot and stream tasks with explicit dependencies; pause, resume
and stop use provider controls. Schema validation and replay remain independent
tasks. Replay requires distinct range boundaries, a target ResourceRef,
idempotency/dedup and schema-compatibility assessments, and a second explicit
confirmation for production. It never rewinds the live stream.

Event envelopes preserve absent provider fields rather than fabricating nulls.
Samples are bounded to 1,000 events; key, before, after, changed fields,
headers and metadata are redacted unless `cdc.view_payloads` is independently
granted, after which configured field-path redactions still apply. Breaking and
unknown schema changes always resolve to pause-and-review. Observed lag can
emit configured threshold events.

CDC project saves use optimistic revisions and independent backend validation
of references, capture policies, proof, evolution safety, alert thresholds and
recursive secret exclusion. Authored source/sink relationships are mirrored
to the shared relationship graph with provenance. No provider is activated for
CDC by the platform module: absent, partial, read-only, unknown or unsupported
adapter evidence is visible and live execution fails closed without a generic
SQL, PostgreSQL or protocol-family fallback.

## Replication Topology module

`modules/replication` owns the deterministic `cdeadmin.replication.v1` asset
and six independently openable surfaces: Topology Explorer, Participant
Inspector, Replication Link Inspector, Lag History, Failover Planner and
Events. The canonical asset separates saved topologies, alert policies,
failover plans, visual layouts and snapshot references from live discovery,
task state and transient failover reviews.

Every participant retains both an exact provider-native role/state and one of
the intentionally broad normalized roles: writer-capable, read-only replica,
peer, arbiter/voter, router, witness or unknown. The normalized role supports
cross-provider navigation but never replaces provider authority. Positions are
opaque typed provider values. Lag is accepted only when provider-reported or
calculated by the registered adapter with its unit and calculation evidence;
the module never subtracts unrelated native position strings.

Provider admission requires capability discovery, topology discovery,
position reading, lag calculation/reporting, failover validation, command
preparation and prepared-command execution. Each result carries explicit
support state, provider version, capabilities, native mechanisms, evidence,
warnings, limitations and runtime evidence. Partial and read-only discovery
remain useful for safe inspection, while live mutation fails closed. Missing
or unknown provider support never receives a generic SQL or PostgreSQL
fallback.

Topology drag and keyboard movement update only the authored visual layout;
they cannot change live membership, leadership or replication roles. Link
pause/resume passes through provider command preparation and execution.
Failover planning requires preconditions, provider-native commands, expected
topology, verification, rollback and a declared data-loss risk. Validation must
independently prove quorum safety, candidate eligibility, position safety and
data-loss assessment. Arming binds the exact plan revision to a confirmation,
environment and connection. Execution uses dependent shared tasks and is
accepted only after provider rediscovery verifies the expected roles and link
states; low lag alone is never treated as promotion safety.

The backend independently validates topology relationships, normalized
vocabularies, finite lag/estimate values, complete failover runbooks, visual
layout membership, optimistic asset versions and recursive secret exclusion.
Authored topology/participant/link relationships are mirrored to the shared
relationship graph, while commands, assets, resources, plans and events are
available through permission-filtered federated search.

## Distributed Tracing module

`modules/tracing` owns the deterministic `cdeadmin.tracing.v1` authored asset
and six independently openable surfaces: Trace Search, Trace Waterfall, Span
Inspector, Service / Resource Map, Query Correlation and Ingestion & Sampling.
The asset contains saved searches/views, trace-source configurations, sampling
and sensitivity policies, and an optional retention-policy reference. Runtime
traces, source counters, selections, search pages and service-map aggregates
never enter canonical project source.

Trace and span IDs retain their OpenTelemetry hexadecimal identity. Parent
relationships are accepted only when explicitly supplied; timing overlap is
never converted into parentage. Span status, events and non-parent links stay
distinct, future span-kind values retain their native value, and unknown
attributes remain inspectable. SQL text, bind values, document payloads,
authorization material and configured sensitive keys are recursively redacted
before persistence. Raw credentials are forbidden; sources use
`CredentialRef` values.

OTLP HTTP/gRPC, internal CDEadmin, provider-native and imported-file sources
are represented without assuming that every provider supports native tracing.
A provider adapter must cover native ingestion, database-operation
correlation, sensitive-attribute redaction and bounded trace-store queries.
Every result carries explicit support state, version, evidence, capabilities,
mechanisms, warnings, limitations and native details. Missing or unsupported
adapters fail closed; partial query evidence remains visibly partial.

Large search, observed service-map aggregation and OTLP export use the shared
Task service. Search pages are bounded and expose their actual searchable time
range plus accepted, dropped, rejected and redacted counts. Service maps are
always labeled with the observed time window and include a table alternative;
they are not presented as timeless topology. Query, task and resource links
are mirrored to the shared relationship graph with trace evidence. Retention
expiry deletes telemetry only, never the authored asset or referenced project
metadata.

All eight commands use the shared command boundary and the five tracing
permissions keep ordinary viewing, sensitive viewing, source configuration,
export and administration independent. Backend validation independently
checks authored schemas, references, source-specific requirements, bounded
page sizes, sampling rates, optimistic revisions and recursive secret
exclusion.

## AI Interface and Discovery Intelligence modules

`modules/ai_interface` is the activated, versioned AI authority. Its connector
registry admits only complete connector implementations and creates a
dedicated AI principal/session; it never reuses a human provider session. Ten
project asset types preserve model, connector, agent, instruction, policy and
plan definitions with optimistic revisions. All 46 commands pass through the
Command Registry, deterministic permission/risk authorization, approval,
budget, egress and audit boundaries. Provider queries are compiled and
reviewed by their exact provider authority. Result handles are bounded and
opaque. Missing model, connector, tool or provider capabilities remain
unavailable and are not advertised.

`modules/discovery_intelligence` is the activated Data Discovery and
Intelligence boundary. It composes the eight specified services and nine
versioned project asset types behind 50 commands and 13 independent
permissions. The 30 form contracts and 38 screen contracts use the shared
contract workspace. Command-bearing forms are canonicalized before execution:
screen context must provide existing asset IDs, project IDs, revisions,
profiles and protected actor scope, while secret controls become
`CredentialRef` values before entering the Command Registry. The adapter does
not fabricate identity or provider semantics.

Discovery index publication validates a complete candidate revision and
switches it atomically; a failed candidate cannot replace or contaminate the
last good document catalogue. Search applies provider/security admission
before ranking, facets, autocomplete, graph traversal, analytics or AI
exposure. Ranking is deterministic and explanation-bearing. Business terms,
domains, metrics, data products, certifications, usage, recommendations,
saved searches, collections, access requests, curation evidence and feedback
retain their distinct contracts rather than being flattened into generic
tables.

Index-source adapters are registered by exact source identity. Provider grant
planning/provisioning, provider mutations, certification evidence, visibility
testing, analysis launchers and AI enrichment are enabled only when their real
authorities are supplied. The default access policy creates a manual-review
request; it is not a simulated provider grant. Discovery remains fully usable
without AI. When both modules are active, only bounded Discovery read/propose
tools are published to the AI tool catalogue and a second egress check applies
before returned data enters model context.

ScratchBird identities and access surfaces can be canonicalized and displayed
by Discovery without treating compatibility listeners as separate engines.
This does not activate or claim a native ScratchBird provider; that provider
remains outside the current reference-engine completion stage.
