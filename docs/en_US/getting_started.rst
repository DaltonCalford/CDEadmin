.. _getting_started:

************************
Getting started
************************

ScratchRobin CDE Admin can run as a browser application, behind a production
web server, in a container, or through its desktop runtime. A packaged desktop
build contains its Python application and browser runtime; an end user does not
need a separate Python or browser installation.

Development startup
===================

From a source checkout, install the Python dependencies and build the web
assets. Node.js uses the Yarn version pinned by ``web/package.json`` through
Corepack; a global Yarn installation is not required.

.. code-block:: bash

   python3 -m venv venv
   source venv/bin/activate
   python3 -m pip install -r requirements.txt
   make install-node
   make bundle
   python3 web/setup.py
   python3 web/CDEadmin.py

The development server normally listens at ``http://127.0.0.1:5051``. Local
paths, logging and other overrides belong in ``web/config_local.py``. Do not
reuse pgAdmin state locations.

First sign-in
=============

Server mode asks for the first CDEadmin administrator account during setup.
That account administers CDEadmin users, groups, permissions, connector
visibility and personal state. Database-engine users and roles remain native
objects owned by their respective providers.

Connectors
==========

Connector roots are hidden by default and can be enabled per user from the
*Connectors* command menu. Enabling a connector displays the engine root; it
does not create a server or prove that a local engine is running.

The normal hierarchy is:

``Connectors -> engine -> server or instance -> database -> native objects``

Create or edit a server/instance with the exact engine form. Then register or
create a database target using that engine's database form. Some engines have a
default database catalog; others, such as Firebird, require an explicit database
path and can still expose server-level service operations without one.

Reference-engine demonstrations
===============================

The project includes on-demand fixtures under
``tools/reference_engine_demos``. Start only the engine being tested because
the complete distributed portfolio is too large to run simultaneously on a
normal development workstation.

.. code-block:: bash

   cd tools/reference_engine_demos
   python3 demo_estate.py list
   python3 demo_estate.py start firebird
   python3 demo_estate.py seed firebird
   python3 demo_estate.py verify firebird

See :doc:`connecting`, :doc:`user_interface`,
:doc:`cdeadmin_architecture`, and :doc:`implementation_status` before treating
a connector as release-qualified.

.. toctree::
   :maxdepth: 2

   deployment
   login
   mfa
   user_management
   change_ownership
   change_user_password
   restore_locked_user
   ldap
   kerberos
   oauth2
   webserver
