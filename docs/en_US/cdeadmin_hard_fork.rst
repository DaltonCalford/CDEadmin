***************************
CDEadmin hard-fork status
***************************

CDEadmin is an independent hard fork of pgAdmin 4, based on pgAdmin 4 9.17.
It is not pgAdmin 4 and is not affiliated with or endorsed by the pgAdmin
Development Team.

Major divergences
=================

CDEadmin extends the inherited Flask, React and Electron application into a
provider-driven administration environment. Its major divergences include:

* native ScratchBird support;
* direct management of independent relational, document, graph, key-value,
  analytic, search, temporal and distributed database engines;
* capability-defined object catalogs, forms, actions and data editors;
* SQL and non-SQL query workspaces;
* semantic models, cubes and analytical structures;
* cross-model query workflows; and
* distributed-system control-plane operations with validation, approval,
  progress, cancellation, persistence, redaction and audit evidence.

ScratchBird legacy-protocol endpoints receive no special product treatment.
They are presented through the same provider contract as the corresponding
native reference engine.

Product identity
================

Active product UI and documentation use the name **CDEadmin** and generic,
engine-neutral artwork. A database product name appears only when identifying
that engine or its engine-specific feature surface.

The inherited Python package ``pgadmin``, JavaScript object ``pgAdmin``,
launcher ``pgAdmin4.py``, selected route and migration IDs, and some
``PGADMIN_`` variables remain as compatibility interfaces. They are not the
product name. They must be renamed only through an explicit compatibility and
migration plan. New CDEadmin-owned interfaces use CDEadmin names.

Attribution and licence
=======================

The upstream pgAdmin 4 code is Copyright (C) 2013-2026, The pgAdmin
Development Team and is distributed under the PostgreSQL Licence. CDEadmin
retains that licence, upstream copyright headers, source history, and
applicable third-party notices.

CDEadmin modifications are copyright their respective contributors.

The repository ``NOTICE`` and ``LICENSE`` files are authoritative for
redistribution. Upstream pgAdmin artwork, signing identities, package names,
update feeds and distribution endpoints are not CDEadmin assets and must not
be used by a CDEadmin release.

Release status
==============

The hard-fork product identity is established. Independent signing identities
and update endpoints are not yet assigned, so distribution remains blocked on
the product, legal, security and release-engineering gates recorded by the
CDEadmin product-identity contract.
