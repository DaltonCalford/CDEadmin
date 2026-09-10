.. _cdeadmin_architecture:

*************************************
ScratchRobin CDE Admin architecture
*************************************

ScratchRobin CDE Admin is a provider-driven administration platform. Shared
code owns presentation, security boundaries, persistence, orchestration and
audit behavior. A provider owns the exact semantics of an engine. Common code
must not infer that an engine accepts PostgreSQL SQL, object names, metadata,
transactions, maintenance operations, or connection behavior.

Product layers
==============

The principal layers are:

* the Python/Flask application and authenticated HTTP boundary;
* the React browser client and Electron desktop host;
* the command registry, which binds stable command identifiers to labels,
  icons, permissions, visibility and enablement rules;
* connector and endpoint services for user-owned connection profiles, routes,
  credentials, discovery, failover and session state;
* exact engine providers for dialects, metadata, metrics, object models,
  graphical forms, queries, data mutation and administration;
* shared workspaces for object exploration, data, queries, properties,
  operations, control planes and semantic analysis; and
* fail-closed structural, browser and live-engine qualification gates.

Engine authority
================

An exact provider declares only capabilities implemented by the corresponding
engine. Unsupported capabilities are not simulated, inherited from another
engine or exposed through a generic raw-command substitute. ScratchBird legacy
interfaces will use the same provider experience as the corresponding original
engine. Native ScratchBird will have its own provider when that stage begins.

Connection hierarchy
====================

The explorer hierarchy is ``Connectors -> engine -> server/instance ->
database -> native objects``. An endpoint identifies a network or embedded
engine boundary. A database target is separate and may be absent, named, or a
filesystem path according to the engine. This distinction permits Firebird
server/service operations without pretending that a default database exists.

Connection profiles support arbitrary IP addresses or DNS names, ports,
multiple routes, a default principal, encrypted saved credentials, password
visibility while editing, and a transient *connect as another user* principal.
Provider contracts add only the authentication, TLS, discovery, routing,
compression, consistency, pooling and session controls that the exact engine
supports.

Provider-owned objects and forms
================================

Every admitted mutation has a provider-owned form contract. Layout controls may
be reused, but field identity, validation, preview and execution remain owned by
the selected provider. Forms are single-task windows and can be docked or
grouped by the workspace manager; unrelated tasks are not forced into a common
compound form.

Properties are informational. Providers may expose summary fields, creation
DDL or native definition, forward and reverse dependencies, privileges,
columns, constraints, indexes, triggers, parameters, runtime state and other
engine-specific observations. A section appears only when the provider can
obtain it honestly.

Workspaces
==========

The Object Explorer is accompanied by engine-aware workspaces:

* Data Studio uses grids for relational rows and specialized document, graph,
  key-value, time-series, vector, search and analytical views.
* Query workspaces run provider-declared languages such as SQL, CQL, Cypher,
  PPL, InfluxQL and native commands.
* Operations and control-plane workspaces expose health, topology,
  replication, backup/restore, maintenance, security, logs and long-running
  operation state where the engine provides them.
* Semantic workspaces support relational, multidimensional and
  provider-specific analytical models without requiring every engine to
  pretend it implements a common cube model.

Security and transaction authority
==================================

Secrets are stored separately from route metadata and are encrypted through
the CDEadmin credential boundary. Transient alternate principals are not saved
as defaults. Common failover may select a route before a session exists, but it
does not replay mutations. Commit, rollback and transaction/session state are
reported by the provider that owns the native session.

Delivery modes
==============

The same application supports a standalone development web server, deployment
behind a production WSGI/Apache environment, container operation, and a desktop
runtime that embeds the browser engine and Python application. Packaged desktop
users do not need to install Python or a separate browser.
