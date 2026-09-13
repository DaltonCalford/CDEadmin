#!/usr/bin/env bash
set -euo pipefail
lifecycle_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec "$lifecycle_dir/_engine_lifecycle.sh" foundationdb "$@"
