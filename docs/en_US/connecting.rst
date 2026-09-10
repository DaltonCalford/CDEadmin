.. _connecting:

*******************************
Connecting to an engine
*******************************

CDEadmin connections are provider-owned. There is no global PostgreSQL-style
server form and no assumption that a server has a default database.

Explorer hierarchy
==================

An engine root contains server or instance endpoints. An endpoint can contain
zero or more database targets, and each target contains the native objects
discovered by that provider. Embedded engines use the same hierarchy even when
the endpoint ultimately resolves to a local file.

Endpoint identity
=================

An endpoint form can define:

* a descriptive connection name;
* any valid local or remote IP address or DNS name;
* the provider's listener port or embedded location;
* one or more ordered routes where the engine supports them;
* the default principal and authentication method;
* whether the default credential should be encrypted and saved; and
* provider-specific TLS, discovery, routing, timeout, compression, pooling,
  consistency and session defaults.

Only controls supported by the exact provider are shown. A connection to
``127.0.0.10`` or ``mymachine.example`` is no less valid than one to
``127.0.0.1``.

Credentials
===========

The password field has an explicit reveal control so the user can verify typed
input. Saved secrets use CDEadmin's protected credential store and are not
written into route metadata. *Connect as a different user* creates a transient
principal for the current provider session and does not overwrite or save the
endpoint's default credential.

Database targets
================

Database registration and database creation are separate provider tasks. A
Firebird database target, for example, includes an explicit server-side path or
alias; MySQL-family and PostgreSQL-family providers discover named databases
from a connected server catalog. Providers may also expose attach/detach or
file-based operations instead of server-side creation.

Connection lifecycle
====================

CDEadmin verifies the selected route and provider identity before opening a
database branch. Multi-route failover is bounded to the pre-session connection
phase. Once a provider session exists, CDEadmin does not replay mutations or
infer native transaction completion.

Availability and qualification
==============================

A visible connector means that the provider is included in this build. It does
not mean an engine is installed locally. A grey or unavailable local endpoint
means discovery could not verify the listener. Consult
:doc:`implementation_status` for the distinction between implemented contracts,
prior live evidence and complete release qualification.

.. toctree::

   server_dialog
   connect_to_server
   connect_error
   master_password
   import_export_servers
