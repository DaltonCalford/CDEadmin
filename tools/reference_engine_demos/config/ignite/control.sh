#!/usr/bin/env bash

set -euo pipefail

exec docker exec cdeadmin-demo-apache-ignite \
  /opt/ignite/apache-ignite/bin/control.sh "$@"
