.. _menu_bar:

*****************
Command menus
*****************

CDEadmin's top-level menus contain project-wide commands. Engine-specific
administration is supplied by the selected Object Explorer node and is not
hard-coded into the global menu structure.

File
====

The *File* menu manages CDEadmin projects, workspace files, imports/exports,
recent work, preferences and application exit where appropriate to the host.

Connectors
==========

The *Connectors* menu lists the connector types included in the current build.
Each item is a per-user checked preference. Checked connectors are visible in
the Object Explorer; unchecked connectors are hidden. The menu does not create
arbitrary connector implementations at runtime.

Object
======

The *Object* menu is populated from commands valid for the selected engine,
endpoint, database or native object. It can include Properties, Refresh,
Connect, Query, Open Data, Create, Alter or Drop only when the exact provider
admits them. The same commands appear in the node's context menu.

Tools
=====

The *Tools* menu opens project-owned facilities such as Data Studio, query
workspaces, ERD/whiteboard/dataflow diagrams, model conversion, semantic-model
design, dashboards, reports, data pump, scheduler, comparison and diagnostics.
Tools that require a connection ask for or inherit a typed provider target.

Window
======

The *Window* menu controls docking, grouping, tear-off windows, monitor
placement, workspace restoration and layout reset. A torn-off workspace retains
its command, security and provider context and can be docked again.

Help
====

The *Help* menu opens local documentation, command search, keyboard help,
diagnostics, provider/driver version information, project status and *About
ScratchRobin CDE Admin*. The About view states that CDEadmin is an independent
hard fork based on pgAdmin 4 9.17 and retains the upstream copyright and
PostgreSQL Licence attribution.

Command contract
================

Every menu entry is a command-registry projection with a stable command ID,
icon, label, security permission, visibility predicate, enablement predicate,
shortcut/macro binding and audit behavior. Menus can be redesigned without
changing command authority.
