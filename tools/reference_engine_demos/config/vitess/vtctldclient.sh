#!/bin/sh
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

# Execute the exact vtctldclient shipped in the checked-in Vitess demo
# estate. Keeping the client inside the image guarantees that its version
# matches vtctld and avoids a workstation-level Vitess installation.

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

exec docker compose \
  -p cdeadmin-demo-vitess \
  -f "$script_dir/docker-compose.yml" \
  exec -T vtctld /vt/bin/vtctldclient "$@"
