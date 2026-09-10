.. _server_dialog:

***********************************
Provider server and database forms
***********************************

CDEadmin does not use one shared server or database form for every engine.
Shared controls provide accessible layout and secret handling; the selected
provider owns form identity, fields, defaults, validation, preview and
execution.

Server or instance form
=======================

The server form identifies an engine endpoint independently of a database. It
supports a descriptive name, arbitrary host name or IP address, listener port,
default principal, protected default credentials and any provider-supported
route, TLS, discovery, timeout, compression, consistency, pooling and session
options. Password inputs include a reveal control.

Where an engine supports server/service APIs, an endpoint can be verified and
used for server-level operations without a default database. For Firebird this
includes service-manager identity and selected database lifecycle operations.

Database form
=============

A database has its own provider task for registration, creation, editing,
alteration, detachment/removal or drop. Firebird requires a server-side database
filename or alias and exposes native page size, character set and related
creation options. Catalog-based engines expose their own names, ownership,
encoding, collation, placement or other native fields. Embedded engines expose
filesystem and attachment details.

No database form displays unrelated tabs from MongoDB, Neo4j, PostgreSQL or any
other provider. One task opens one resizable form. The workspace manager may
later dock multiple task forms into a tab group at the user's request.

Credentials and alternate users
===============================

The endpoint's default user can be saved with encrypted credentials. *Connect
as a different user* supplies a transient identity for a connection attempt
without replacing or persisting the default. Secret values do not appear in
route JSON, logs or retained evidence.

Validation
==========

Validation is fail-closed. Unknown fields, missing native requirements,
unavailable drivers, incompatible server identity, unsafe paths or unsupported
operations prevent execution. A common PostgreSQL connection attempt is never
used as a fallback for another provider.
