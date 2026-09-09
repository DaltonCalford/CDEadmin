# CDEadmin reference-engine demonstration estate

This directory contains the portable lifecycle, configuration, seed, and
verification materials for every CDEadmin reference engine except native
ScratchBird. It creates the same field-service sample in each engine through
that engine's native client and data model. No database files, downloaded
images, credentials from a real system, logs, or generated binaries are
committed.

## Prerequisites

- Linux with Docker Engine and the Docker Compose v2 plugin
- Python 3.12 or a project-supported newer Python
- Go (used to build the repository's native TiKV helper)
- a C compiler and `make` (used to build the pinned SQLite reference CLI)
- MariaDB Connector/C development files (`libmariadb-dev` on Debian/Ubuntu)
  for the provider's native MariaDB Python driver
- enough free resources for the engine being tested

The estate is intentionally on-demand. Starting all distributed and analytical
products together requires substantial memory; normal development should start
and test one engine at a time.

## Bootstrap

From this directory, preferably in the same virtual environment as CDEadmin:

```bash
python3 -m pip install -r requirements.txt
python3 bootstrap.py
```

Or allow the bootstrap command to install the Python clients:

```bash
python3 bootstrap.py --install-python
```

Bootstrap performs these reproducible client-side steps:

1. downloads SQLite 3.53.0 source, verifies its pinned SHA-256, and builds its
   CLI under ignored `runtime/` state;
2. tests and builds the TiKV client-go helper from the CDEadmin repository;
3. extracts the matching FoundationDB CLI and C client library from the pinned
   FoundationDB image;
4. extracts the Firebird 5.0.4 client library required by Firebird 4/5 service
   parameter blocks from the pinned Firebird image;
5. extracts the exact MariaDB 12.2.2 `mariadb`, `mariadb-dump`,
   `mariadb-upgrade`, `mariadb-binlog`, and `mariadb-admin` clients. The first
   three back the currently admitted logical backup, logical restore, and
   read-only server upgrade-requirement forms. The remaining tools are staged
   for later constrained provider workflows and are not advertised as active
   administration capabilities.

It also renders `runtime/connection_profiles.json` from the portable template.
Source `environment.sh` before starting CDEadmin when FoundationDB, Firebird,
or MariaDB administration tools are being tested so the extracted native
clients are discoverable.

Register all rendered profiles for an existing CDEadmin account with:

```bash
python3 register_demo_profiles.py --user user@example.com --dry-run
python3 register_demo_profiles.py --user user@example.com
```

Registration is idempotent, creates an online SQLite backup of the CDEadmin
configuration first, and enables every logical connector root for that user.
It never stores the demonstration passwords without the user's active master
password. Password-bearing engines therefore prompt on first connection; the
user can then choose whether to save the credential through CDEadmin's normal
encrypted-secret workflow.

## Lifecycle and data

List supported profiles and inspect current state:

```bash
python3 demo_estate.py list
python3 demo_estate.py status
```

Start, seed, verify, and stop one reference engine:

```bash
python3 demo_estate.py start firebird
python3 demo_estate.py seed firebird
python3 demo_estate.py verify firebird
python3 demo_estate.py stop firebird
```

Run the destructive Firebird database-service form gate only against the
packaged disposable demo instance:

```bash
source environment.sh
CDEADMIN_FIREBIRD_DEMO_PASSWORD='CDEadminDemo-2026!' \
  python3 verify_firebird_services.py \
    --database-root /var/lib/firebird/data \
    --client-library runtime/firebird/lib/libfbclient.so.5.0.4 \
    --container cdeadmin-demo-firebird \
    --output evidence/firebird-service-forms-live.json
```

The verifier creates unique database and backup filenames, exercises the
normal-state service operations, drops every disposable database, removes its
backup files, and never writes the credential to evidence. Shadow activation
and nbackup fixup require separate fault-state fixtures and are intentionally
not asserted by this normal-state gate.

Run those two destructive fault-state fixtures separately. This creates a real
manual shadow and a real stalled filesystem-copy header, invokes the same
provider service callbacks used by CDEadmin, verifies both reopened databases,
and proves that nbackup fixup rejects a normal database:

```bash
source environment.sh
CDEADMIN_FIREBIRD_DEMO_PASSWORD='CDEadminDemo-2026!' \
  python3 verify_firebird_fault_services.py \
    --database-root /var/lib/firebird/data \
    --client-library runtime/firebird/lib/libfbclient.so.5.0.4 \
    --container cdeadmin-demo-firebird \
    --output evidence/firebird-fault-services-live.json
```

For browser-level completion evidence, use an isolated desktop configuration
clone and disposable server filenames. Omitting `--operation` runs all 19
normal-state service forms; repeating it selects an ordered diagnostic subset.
The orchestrator continues across operation failures, captures every rendered
state and native post-state, and removes all exact disposable files:

```bash
source environment.sh
CDEADMIN_FIREBIRD_DEMO_PASSWORD='CDEadminDemo-2026!' \
  python3 ../cdeadmin_firebird_ui_completed_orchestrator.py \
    --source-config-db /path/to/qa/runtime/cdeadmin.db \
    --desktop-user qa.cdeadmin@example.com \
    --database-root /var/lib/firebird/data \
    --client-library runtime/firebird/lib/libfbclient.so.5.0.4 \
    --container cdeadmin-demo-firebird \
    --evidence-root /path/to/evidence/firebird-completed \
    --manifest-output /path/to/evidence/screenshot_manifest.csv \
    --output /path/to/evidence/firebird-ui-completed-gate.json \
    --server-log /path/to/evidence/firebird-ui-server.log \
    --operation-log-root /path/to/evidence/firebird-completed/logs
```

The `start` and `stop` commands also accept `embedded`, `local`, or `all`.
`local` means the four relatively direct fixtures—Firebird, Redis, MongoDB,
and Cassandra—not operating-system services. To create and verify every sample
sequentially while stopping heavyweight engines between checks:

```bash
python3 demo_estate.py prepare all
```

Generated database files are stored under ignored `data/`; native round-trip
evidence is stored under ignored `evidence/`; downloaded or built client assets
and rendered profiles are stored under ignored `runtime/`. Named Docker volumes
retain server data across ordinary `stop`/`start` operations. These scripts do
not delete volumes.

## Profiles and ports

All network listeners bind only to `127.0.0.1`, except the TiKV peer address
that must be reachable by both its Docker containers and the host-native helper.
The rendered connection-profile file is the authoritative import/reference
artifact for the local estate. Demonstration credentials are deliberately fixed
and must never be reused outside this isolated local fixture.

OpenSearch native, SQL, and PPL profiles share one OpenSearch deployment.
YugabyteDB YSQL and YCQL profiles share one YugabyteDB deployment. They remain
separate profiles because CDEadmin exercises different protocols and object
models. Embedded SQLite and DuckDB create project-local files. Vitess uses the
checked-in Compose topology under `config/vitess/`.

## Version and licensing policy

`engine_versions.json` records the pinned reference release for each product.
Images and upstream source are downloaded by the user from their publishers;
they are not redistributed in this repository. Their respective upstream
licenses and image terms apply. The Vitess Compose topology is adapted from the
Vitess 23.0.3 local example and remains subject to the Apache License 2.0; see
`VITESS_NOTICE.md`.

This fixture excludes native ScratchBird by design. A future native fixture can
be added only when its engine and driver work has completed; legacy-interface
emulation must continue to be exercised as the corresponding reference engine,
without special-case behavior in CDEadmin.
