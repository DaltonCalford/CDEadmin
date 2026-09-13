#!/usr/bin/env bash
# Execute one reference-engine lifecycle from any working directory.
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "internal error: an engine ID is required" >&2
    exit 64
fi

engine_id=$1
action=${2:-start}
launcher_name="${engine_id}.sh"
lifecycle_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
demo_root=$(CDPATH= cd -- "$lifecycle_dir/.." && pwd)

# Native provider tools and libraries are part of the individual fixture's
# contract. Sourcing this for every action is harmless for pure Docker engines.
source "$demo_root/environment.sh"

case "$action" in
    start|startup)
        exec python3 "$demo_root/demo_estate.py" start "$engine_id"
        ;;
    stop|shutdown)
        exec python3 "$demo_root/demo_estate.py" stop "$engine_id"
        ;;
    restart)
        python3 "$demo_root/demo_estate.py" stop "$engine_id"
        exec python3 "$demo_root/demo_estate.py" start "$engine_id"
        ;;
    status)
        exec python3 "$demo_root/demo_estate.py" status "$engine_id"
        ;;
    seed)
        exec python3 "$demo_root/demo_estate.py" seed "$engine_id"
        ;;
    verify)
        exec python3 "$demo_root/demo_estate.py" verify "$engine_id"
        ;;
    test)
        python3 "$demo_root/demo_estate.py" start "$engine_id"
        exec python3 "$demo_root/demo_estate.py" verify "$engine_id"
        ;;
    help|-h|--help)
        echo "Usage: $launcher_name [start|startup|stop|shutdown|restart|status|seed|verify|test]"
        ;;
    *)
        echo "unsupported action '$action'" >&2
        echo "Usage: $launcher_name [start|startup|stop|shutdown|restart|status|seed|verify|test]" >&2
        exit 64
        ;;
esac
