.. _deployment:

*******************
Deployment
*******************

CDEadmin supports source/development execution, a packaged desktop runtime,
production WSGI/web-server hosting and containers. Independent CDEadmin
packages, signing keys and update feeds are required; pgAdmin package IDs,
repositories, keys and update services must not be reused.

The desktop runtime bundles the Python application and an embedded browser
engine. Packaged users therefore do not need a separate Python or browser
installation. Server deployments may use CDEadmin's own application server for
development or place the WSGI application behind Apache or another supported
production server.

Current builds are development builds and are not release-approved. See
:doc:`implementation_status` and :doc:`project_governance`.

.. toctree::
   :maxdepth: 2

   config_py
   desktop_deployment
   server_deployment
   container_deployment
