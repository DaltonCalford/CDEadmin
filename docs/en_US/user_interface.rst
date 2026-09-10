.. _user_interface:

***********************
User interface
***********************

CDEadmin uses an engine-neutral application shell around provider-specific
content. The top-level menu contains project commands; engine, server, database
and object operations come from the context of the selected explorer node.

Main regions
============

The interface contains:

* the ScratchRobin application identity, command toolbar and project menus;
* a configurable controller rail for the Object Explorer, projects, diagrams,
  dashboards, scheduler and other tools;
* a branching Object Explorer with state, engine and object icons;
* dockable, resizable single-task workspaces; and
* status, notification and long-running-operation surfaces.

Commands
========

Menus and buttons are views over the command registry. Each command has a
stable identifier, label, icon, permission requirement, visibility rule and
enablement rule. This permits keyboard shortcuts, macros, security groups and
future menu layouts to invoke the same audited command.

Provider context
================

Selecting an engine, endpoint, database or object changes the available
commands. CDEadmin asks that provider for its exact operations and does not add
another engine's commands. Firebird backup and validation, Redis TTL operations,
MongoDB aggregation pipelines and Neo4j graph plans therefore remain distinct
native experiences.

Forms and workspaces
====================

Each create, edit, inspect, maintenance or security task opens as one form.
Forms can be docked, moved, torn off into another window, or grouped as tabs by
the workspace manager. A provider form is not a large compound page containing
unrelated tabs from every engine.

Data Studio selects a specialized view for the provider model: relational
grid, document, graph, key-value, time-series, vector, search or analytical.
Query workspaces likewise use the provider's supported languages rather than
assuming SQL.

Properties
==========

*Properties* is an informational workspace. Depending on the exact object, it
may contain summary metadata, creation DDL or native definition, dependencies,
objects that depend on it, privileges, columns, constraints, indexes, triggers,
parameters and runtime state. Unsupported sections are absent, not empty
imitations of another engine.

Accessibility and personalization
=================================

The design system separates widget behavior from appearance. User preferences
can govern colors, contrast, fonts, text size, spacing, target size, tab size,
panel dimensions, handedness and interface language. Keyboard navigation,
visible focus, reduced motion and scalable layouts are required acceptance
conditions, not theme extras.

.. toctree::
   :maxdepth: 2

   menu_bar
   toolbar
   tabbed_browser
   tree_control
   preferences
   keyboard_shortcuts
   search_objects
