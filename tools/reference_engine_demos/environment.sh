#!/usr/bin/env bash
# Source this file before starting CDEadmin when using the bundled FDB client.
CDEADMIN_DEMO_ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export CDEADMIN_DEMO_ROOT
export CDEADMIN_REFERENCE_PROFILES="$CDEADMIN_DEMO_ROOT/runtime/connection_profiles.json"
export CDEADMIN_TIKV_HELPER_PATH="$CDEADMIN_DEMO_ROOT/runtime/bin/cdeadmin-tikv-helper"
export CDEADMIN_FIREBIRD_CLIENT_LIBRARY="$CDEADMIN_DEMO_ROOT/runtime/firebird/lib/libfbclient.so.5.0.4"
export CDEADMIN_MARIADB_CLIENT_BINARY="$CDEADMIN_DEMO_ROOT/runtime/mariadb/bin/mariadb"
export CDEADMIN_MARIADB_DUMP_BINARY="$CDEADMIN_DEMO_ROOT/runtime/mariadb/bin/mariadb-dump"
export CDEADMIN_MARIADB_UPGRADE_BINARY="$CDEADMIN_DEMO_ROOT/runtime/mariadb/bin/mariadb-upgrade"
export CDEADMIN_MARIADB_BINLOG_BINARY="$CDEADMIN_DEMO_ROOT/runtime/mariadb/bin/mariadb-binlog"
export CDEADMIN_MARIADB_ADMIN_BINARY="$CDEADMIN_DEMO_ROOT/runtime/mariadb/bin/mariadb-admin"
if [ -n "${LD_LIBRARY_PATH:-}" ]; then
    LD_LIBRARY_PATH="$CDEADMIN_DEMO_ROOT/runtime/foundationdb/lib:$LD_LIBRARY_PATH"
else
    LD_LIBRARY_PATH="$CDEADMIN_DEMO_ROOT/runtime/foundationdb/lib"
fi
export LD_LIBRARY_PATH
