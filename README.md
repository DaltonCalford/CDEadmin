# CDEadmin

CDEadmin is an independent hard fork of pgAdmin 4 9.17. It is being developed
as a multi-engine, multi-model administration and development environment for
ScratchBird and for independently operated database engines.

CDEadmin is not pgAdmin 4 and is not affiliated with or endorsed by the
pgAdmin Development Team. The upstream copyright, PostgreSQL Licence, source
history, and applicable notices are retained. See [NOTICE](NOTICE),
[LICENSE](LICENSE), and [Hard-fork status](docs/en_US/cdeadmin_hard_fork.rst).

## Why this is a hard fork

The project is no longer a PostgreSQL-only administration tool with additional
connection adapters. Its product architecture and administration model now
diverge materially from upstream pgAdmin 4:

- A provider contract represents engine identity, capabilities, metadata,
  object lifecycles, query languages, data editing, health, and operations.
- ScratchBird is a native engine. Its legacy-protocol endpoints are handled by
  the same provider as the corresponding native reference engine; there is no
  special “emulation mode” in the UI.
- Relational, document, graph, key-value, analytic, search, columnar, temporal,
  and distributed/control-plane workloads can have purpose-built workspaces.
- Visual administration is capability-driven instead of assuming a
  PostgreSQL object hierarchy or SQL grammar.
- Cross-model queries, semantic models, cubes, operational workflows, and
  audit records are first-class product concepts.
- CDEadmin uses separate state, package, cookie, desktop-store, update, and
  signing namespaces so it can coexist with pgAdmin 4.

The current implementation contains provider and administration work for
multiple engine families. Feature availability is governed by provider
capability and activation gates; a listed engine should not be interpreted as
fully production-ready unless its gates pass.

## Product identity and compatibility names

All active user-facing product surfaces must say **CDEadmin** and use generic,
engine-neutral artwork. Engine names such as PostgreSQL, MongoDB, Firebird, or
ClickHouse remain where they describe an actual engine, its features, or its
documentation.

Some inherited identifiers remain temporarily for compatibility, including
the Python package `pgadmin`, JavaScript object `pgAdmin`, compatibility
launcher `web/pgAdmin4.py`, selected route/module IDs, migration names, and
some
`PGADMIN_` configuration variables. Renaming these without a migration layer
would break imports, extensions, deployments, or user state. They are legacy
implementation interfaces, not the product name. New project-owned code must
use `CDEadmin`, `cdeadmin`, or `CDEADMIN_` as appropriate.

Run the identity and attribution gate with:

```bash
python3 tools/cdeadmin_product_identity.py --source .
python3 -m unittest tools.tests.test_cdeadmin_product_identity
```

## Architecture

CDEadmin retains the proven Flask/Python server, React client, and Electron
desktop runtime inherited from pgAdmin 4. The CDEadmin provider layer extends
that foundation with:

- engine/provider registration and capability discovery;
- connection-profile and driver contracts;
- provider-defined object catalogs and safe operation descriptors;
- form/workspace schemas for visual administration;
- SQL and non-SQL query execution contracts;
- data-grid editing and provider-specific mutation semantics;
- semantic-model and analytic workspace contracts;
- distributed control-plane operations, approvals, progress, cancellation,
  redaction, persistence, and audit evidence.

PostgreSQL support remains a core engine provider, but it does not define the
global product identity or constrain other providers to PostgreSQL semantics.

## Source tree

- `web/` — Flask application, React UI, provider implementations, tests.
- `web/pgadmin/cdeadmin/` — CDEadmin-owned provider, administration,
  workspace, and product-identity code.
- `runtime/` — Electron desktop runtime.
- `pkg/` — packaging inputs.
- `docs/` — product and inherited engine documentation.
- `tools/` — validation, activation-gate, and engineering utilities.

The historical `pgadmin` directory name is part of the compatibility boundary
described above.

## Prerequisites

- Python 3.9 or later
- Node.js 20 or later
- Yarn through Corepack
- The Python and native drivers required by the providers being exercised
- Live reference engines only for the relevant integration/activation tests

Enable Yarn and create a Python environment:

```bash
corepack enable
python3 -m venv venv
source venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m pip install -r web/regression/requirements.txt
```

Provider-specific drivers and reference-engine versions are documented by
their activation plans and tests. Do not silently substitute an engine or
driver version when collecting conformance evidence.

## Building web assets

```bash
make install-node
make bundle
```

On Windows, run `yarn install` and `yarn run bundle` from `web/`.

## Development configuration and startup

Local overrides belong in `web/config_local.py`. CDEadmin defaults should use
separate paths and ports from pgAdmin 4. The product identity contract reserves
port 5051 and `cdeadmin` state namespaces for this purpose.

Initialize the configuration database and run the inherited compatibility
launcher:

```bash
python3 web/setup.py
python3 web/CDEadmin.py
```

`CDEadmin.py` is the canonical launcher. `pgAdmin4.py` remains as a temporary
compatibility entry point for inherited imports and deployments.

## Tests

Run the narrow test suites associated with changed providers and UI modules,
then the broader Python and JavaScript suites appropriate to the change. The
identity gate is mandatory for changes to branding, packaging, About, runtime,
or documentation.

Generated reports, workplans, and test evidence for this programme are kept
outside the repository under `~/Sandbox/pgadmin4_work_area/`. Product code is
changed in this repository. ScratchBird source and private specifications are
read-only inputs and must never be modified by CDEadmin work.

## Documentation

Build the documentation with:

```bash
python3 -m pip install Sphinx sphinxcontrib-youtube
make docs
```

The output is written to `docs/en_US/_build/html/`. Much of the inherited
object-level documentation still describes the PostgreSQL provider. Such pages
are valid engine-specific documentation, but must not present PostgreSQL or
pgAdmin as CDEadmin’s global product identity.

## Packaging status

CDEadmin package identifiers and state namespaces have been reserved, but
independent signing keys and update endpoints are unassigned. Packages are not
approved for release until product, legal, security, and release-engineering
gates have been completed. Never reuse pgAdmin signing identities, update
feeds, package identifiers, or artwork.

## Licence and attribution

CDEadmin retains and modifies code from pgAdmin 4. pgAdmin 4 is Copyright (C)
2013–2026, The pgAdmin Development Team, and is distributed under the
PostgreSQL Licence. CDEadmin remains distributed under that licence.

Do not remove upstream copyright headers from inherited files. New or
substantially rewritten files should identify CDEadmin while retaining the
upstream notice whenever they contain upstream-derived material. Consult
[NOTICE](NOTICE) for the complete fork statement and compatibility policy.

Upstream project: <https://www.pgadmin.org/>

Upstream source: <https://github.com/pgadmin-org/pgadmin4>

Questions, defects, and security reports about CDEadmin must be handled by the
CDEadmin project’s own channels. Do not send CDEadmin issues to pgAdmin’s
support or security contacts.
