.. _tree_control:

*********************
Object Explorer tree
*********************

The Object Explorer is a true branching tree. Connector, endpoint, database,
container and object nodes render branch guides, expand/collapse controls,
engine/object icons, connection state, optional check marks, labels and status
badges. Indentation or a greater-than character alone is not a valid tree
rendering.

Hierarchy
=========

The required hierarchy is:

``Connectors -> engine type -> server/instance -> database -> native objects``

A database must never appear as a peer of the server on which it resides. An
endpoint may legitimately have no database child until a database is registered
or discovered. The object levels below a database are defined by the engine:
schemas and tables for one provider, collections for another, graph labels for
another, and key spaces or analytical structures elsewhere.

Node state
==========

Endpoint icons distinguish unavailable, disconnected, connecting, connected,
degraded and failed states without relying on color alone. Database and object
badges may show read-only, system, temporary, materialized, partitioned,
replicated, invalid or other provider-declared states.

Context commands
================

The configured primary context button (right or left according to handedness)
opens commands supplied for the exact selected node. Engine roots control
connector visibility and endpoint creation. Endpoints control connection and
server/cluster tasks. Database nodes control registration, properties,
backup/restore, validation, security and database lifecycle where supported.
Object nodes control their exact inspect, create, alter, data and drop tasks.

Unsupported commands are not borrowed from PostgreSQL or another provider.
Destructive commands require provider-specific validation, preview and
confirmation.

Interaction
===========

Keyboard navigation, expand/collapse, selection, filtering, incremental search,
refresh, drag/drop and accessible node descriptions are required. Dragging an
eligible object to a query or modeling workspace transfers a typed object
reference; the receiving provider decides how it can be represented.
