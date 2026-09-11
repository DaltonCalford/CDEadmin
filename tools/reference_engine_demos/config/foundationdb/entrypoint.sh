#!/usr/bin/env bash

set -euo pipefail

cluster_file=/var/fdb/fdb.cluster
data_root=/var/fdb/data
pids=()

shutdown() {
  if ((${#pids[@]})); then
    kill -TERM "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
}

trap shutdown TERM INT EXIT

for worker in 0 1 2 3; do
  port=$((54500 + worker))
  if ((worker == 0)); then
    data_directory=${data_root}
  else
    data_directory=${data_root}/worker-${worker}
  fi
  log_directory=${data_root}/logs/worker-${worker}
  mkdir -p "${data_directory}" "${log_directory}"
  machine_id=$(printf '%016x' "$((worker + 1))")
  /usr/bin/fdbserver \
    --cluster-file "${cluster_file}" \
    --listen-address "0.0.0.0:${port}" \
    --public-address "127.0.0.1:${port}" \
    --datadir "${data_directory}" \
    --logdir "${log_directory}" \
    --machine-id "${machine_id}" \
    --memory 512MiB \
    --cache-memory 64MiB \
    --storage-memory 128MB &
  pids+=("$!")
done

# The exact FoundationDB backup agent is part of the disposable reference
# topology. Backup containers are mounted at the same absolute host/container
# path so the provider CLI and agent observe identical file:// URLs.
/usr/bin/backup_agent \
  --cluster-file "${cluster_file}" \
  --memory 256MiB &
pids+=("$!")

# A terminated worker invalidates the process-control fixture. Stop the
# remaining workers so Docker reports the estate as unhealthy instead of
# leaving a deceptively partial cluster running.
wait -n "${pids[@]}"
exit 1
