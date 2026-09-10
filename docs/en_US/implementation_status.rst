.. _implementation_status:

*****************************
Implementation status
*****************************

Status date
===========

This page records the 10 September 2026 development checkpoint for CDEadmin
0.1.0-dev. It is a bounded status statement and not a production-readiness
claim.

Implemented checkpoint
======================

* Product identity, independent state namespaces and retained upstream
  attribution are established.
* The explorer exposes 24 engine connector roots and separate server/instance,
  database and native-object levels.
* Twenty-six non-ScratchBird provider profiles have exact capability,
  graphical-operation and activation records. YugabyteDB YSQL/YCQL and
  OpenSearch native/SQL-PPL are separate profiles.
* Provider contracts cover relational, document, graph, key-value, search,
  analytical, time-series, vector, columnar and distributed models.
* The previous consolidated checkpoint recorded 494 declared concepts and
  1,982 provider-owned graphical operations with no structural gaps.
* Portable, on-demand reference-engine fixtures contain lifecycle,
  configuration, seed and verification material. They intentionally do not
  start every heavyweight engine together.
* Browser audit evidence exists for selected relational providers, including
  Firebird, DuckDB, SQLite and MySQL.
* The Firebird 5.0.4 provider has live-qualified server-scope attachment,
  database attachment, native object discovery and transaction-aware row
  editing. Its Properties metadata now includes native creation definitions,
  parameters, dependencies, dependents and privileges. A disposable live
  round-trip recreated and rediscovered representative domains, tables,
  constraints, indexes, views, sequences, triggers, procedures, functions,
  packages, exceptions, roles and external functions.

Work still required
===================

Structural coverage does not establish that every browser interaction is
correct. The remaining work includes:

* exhaustive browser execution of every provider object, single-task form,
  field, context menu and material success/error state;
* screenshot catalogues for every engine and relevant viewport, contrast,
  font-size, keyboard, focus, docking and tear-off condition;
* complete positive and negative live qualification for every supported
  authentication, TLS, certificate, discovery, routing, failover, timeout,
  compression, consistency, pooling and reconnection mode;
* large-result streaming, pagination, plan, export and comparison testing;
* final provider-specific informational Properties coverage outside the
  completed Firebird pass;
* release packaging, signing, update infrastructure and independent security,
  legal and release-engineering approval; and
* native ScratchBird support, deliberately scheduled after reference-engine
  browser completion.

Interpretation rule
===================

An engine is supported only to the extent declared by its exact provider and
proven by its current gates. CDEadmin does not add capabilities that the native
engine lacks. Passing a structural form gate must never be reported as passing
the complete end-user or release gate.
