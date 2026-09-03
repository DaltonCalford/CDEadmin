#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Exact Neo4j 2026.04.0 live qualification gate.

The password is read only from ``CDEADMIN_NEO4J_PASSWORD``. Test objects use
a unique prefix and are removed in ``finally`` blocks. Results distinguish
provider defects from edition- or privilege-dependent unavailable features.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.neo4j.client import Neo4jClient  # noqa: E402


EXPECTED_SERVER = '2026.04.0'
EXPECTED_DRIVER = '6.3.0'


class Lease:
    def __init__(self, value):
        self.value = bytearray(value.encode('utf-8'))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        for index in range(len(self.value)):
            self.value[index] = 0

    def use(self, callback):
        return callback(memoryview(self.value))


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int, default=7687)
    value.add_argument('--database', default='neo4j')
    value.add_argument('--username', default='neo4j')
    value.add_argument(
        '--tls-mode', choices=('disabled', 'system-ca', 'self-signed'),
        default='disabled',
    )
    value.add_argument('--direct', action='store_true')
    value.add_argument('--output', type=Path)
    return value


def observed(label, callback, required=True):
    try:
        detail = callback()
        return {
            'gate': label, 'status': 'passed', 'required': required,
            'detail': detail,
        }
    except Exception as exc:
        return {
            'gate': label,
            'status': 'failed' if required else 'unavailable',
            'required': required,
            'error_type': type(exc).__name__,
            'error': str(exc)[:1000],
        }


def consume(result):
    rows = [record.data() for record in result]
    result.consume()
    return rows


def main(argv=None):
    args = parser().parse_args(argv)
    password = os.environ.get('CDEADMIN_NEO4J_PASSWORD')
    if password is None:
        parser().error('CDEADMIN_NEO4J_PASSWORD is required')

    import neo4j

    def acquire(*_args):
        return Lease(password)

    route = {
        'host': args.host, 'port': args.port, 'database': args.database,
        'username': args.username,
        'credential_reference_id': 'live-gate-credential',
        'principal_reference': 'live-gate-principal',
        'routing': not args.direct, 'tls_mode': args.tls_mode,
    }
    adapter = Neo4jClient(acquire)
    prefix = 'cdeadmin_' + uuid.uuid4().hex[:12]
    label = prefix + '_node'
    rel_type = prefix + '_rel'
    index_name = prefix + '_index'
    constraint_name = prefix + '_constraint'
    database_name = prefix + '_database'
    role_name = prefix + '_role'
    gates = []

    gates.append(observed('driver-version', lambda: {
        'expected': EXPECTED_DRIVER,
        'observed': neo4j.__version__,
        'match': neo4j.__version__ == EXPECTED_DRIVER,
    } if neo4j.__version__ == EXPECTED_DRIVER else (_ for _ in ()).throw(
        RuntimeError(
            f'expected driver {EXPECTED_DRIVER}, observed {neo4j.__version__}'
        )
    )))
    identity = None
    identity_gate = observed(
        'runtime-identity', lambda: adapter.runtime_identity(
            {'route': route}, None
        )
    )
    gates.append(identity_gate)
    if identity_gate['status'] == 'passed':
        identity = identity_gate['detail']
        if identity.get('version') != EXPECTED_SERVER:
            identity_gate.update({
                'status': 'failed',
                'error': f'expected {EXPECTED_SERVER}, observed '
                         f'{identity.get("version")}',
            })

    driver, normalized = adapter._connect({'route': route})
    graph_session = driver.session(database=args.database)
    system_session = driver.session(database='system')
    try:
        def graph_crud():
            consume(graph_session.run(
                f'CREATE (a:`{label}` {{name: $first}}), '
                f'(b:`{label}` {{name: $second}}) '
                f'CREATE (a)-[r:`{rel_type}` {{weight: 1}}]->(b) '
                'RETURN elementId(a) AS a, elementId(b) AS b, '
                'elementId(r) AS r',
                {'first': 'alpha', 'second': 'beta'},
            ))
            consume(graph_session.run(
                f'MATCH (n:`{label}` {{name: $name}}) '
                'SET n.updated = true RETURN n.updated AS updated',
                {'name': 'alpha'},
            ))
            rows = consume(graph_session.run(
                f'MATCH (a:`{label}`)-[r:`{rel_type}`]->(b:`{label}`) '
                'RETURN a.name AS first, b.name AS second, r.weight AS weight'
            ))
            if rows != [{'first': 'alpha', 'second': 'beta', 'weight': 1}]:
                raise RuntimeError('graph CRUD readback differed')
            return {'records': len(rows), 'parameterized_values': True}

        gates.append(observed('graph-crud', graph_crud))

        def schema():
            consume(graph_session.run(
                f'CREATE RANGE INDEX `{index_name}` IF NOT EXISTS '
                f'FOR (n:`{label}`) ON (n.name)'
            ))
            consume(graph_session.run(
                f'CREATE CONSTRAINT `{constraint_name}` IF NOT EXISTS '
                f'FOR (n:`{label}`) REQUIRE n.constraint_key IS UNIQUE'
            ))
            rows = consume(graph_session.run(
                'SHOW INDEXES YIELD name WHERE name IN $names RETURN name',
                {'names': [index_name, constraint_name]},
            ))
            if len(rows) < 2:
                raise RuntimeError('schema objects were not observed')
            return {'observed_names': sorted(row['name'] for row in rows)}

        gates.append(observed('schema-administration', schema))

        def resource_and_result():
            resources = adapter.list_resources({'route': route})
            handle = adapter.open_session({'route': route})
            try:
                token = adapter.execute(handle, {
                    'source': f'MATCH (n:`{label}`) RETURN n',
                    'parameters': {},
                })
                result = adapter.describe_result(token)
                if result['result_kind'] != 'graph':
                    raise RuntimeError('graph result kind was not preserved')
                transaction = adapter.describe_transaction(handle)
                if transaction['common_finality_inference']:
                    raise RuntimeError('common finality inference was enabled')
                return {
                    'resource_kinds': sorted({
                        item['resource_kind'] for item in resources
                    }),
                    'graph_records': len(result['payload']['graphs']),
                }
            finally:
                handle.close()

        gates.append(observed(
            'provider-resource-result-transaction', resource_and_result
        ))

        def bounded_stream_cancellation():
            handle = adapter.open_session({'route': route})
            try:
                token = adapter.execute(handle, {
                    'source': (
                        'UNWIND range(1, 100000) AS item '
                        'RETURN item'
                    ),
                    'parameters': {},
                })
                page = adapter.describe_result(token)
                records = page['payload']['graphs']
                if len(records) != 500 or page['complete']:
                    raise RuntimeError(
                        'bounded streaming did not retain continuation'
                    )
                if not adapter.cancel(token):
                    raise RuntimeError('live result cancellation was refused')
                transaction = adapter.describe_transaction(handle)
                if transaction['common_finality_inference']:
                    raise RuntimeError(
                        'cancellation inferred transaction finality'
                    )
                return {
                    'page_records': len(records),
                    'provider_retained_before_cancel': True,
                    'cancellation_requested': True,
                    'common_finality_inference': False,
                }
            finally:
                handle.close()

        gates.append(observed(
            'bounded-stream-cancellation', bounded_stream_cancellation
        ))

        def database_lifecycle():
            consume(system_session.run(
                f'CREATE DATABASE `{database_name}` IF NOT EXISTS'
            ))
            rows = consume(system_session.run(
                'SHOW DATABASE $name YIELD name, currentStatus '
                'RETURN name, currentStatus', {'name': database_name},
            ))
            if not rows:
                raise RuntimeError('created database was not observed')
            return rows

        gates.append(observed(
            'database-lifecycle', database_lifecycle, required=False
        ))

        def role_lifecycle():
            consume(system_session.run(
                f'CREATE ROLE `{role_name}` IF NOT EXISTS'
            ))
            rows = consume(system_session.run(
                'SHOW ROLES YIELD role WHERE role = $role RETURN role',
                {'role': role_name},
            ))
            if not rows:
                raise RuntimeError('created role was not observed')
            return rows

        gates.append(observed(
            'role-security-lifecycle', role_lifecycle, required=False
        ))
    finally:
        for statement in (
            f'DROP INDEX `{index_name}` IF EXISTS',
            f'DROP CONSTRAINT `{constraint_name}` IF EXISTS',
            f'MATCH (n:`{label}`) DETACH DELETE n',
        ):
            try:
                consume(graph_session.run(statement))
            except Exception:
                pass
        for statement in (
            f'DROP DATABASE `{database_name}` IF EXISTS DESTROY DATA',
            f'DROP ROLE `{role_name}` IF EXISTS',
        ):
            try:
                consume(system_session.run(statement))
            except Exception:
                pass
        graph_session.close()
        system_session.close()
        adapter._forget_driver(driver)
        adapter.close()

    report = {
        'schema': 'cdeadmin.neo4j-live-gate.v1',
        'server_expected': EXPECTED_SERVER,
        'driver_expected': EXPECTED_DRIVER,
        'route': {
            'host': args.host, 'port': args.port,
            'database': args.database, 'tls_mode': args.tls_mode,
            'routing': normalized.get('routing', True),
        },
        'identity': identity,
        'gates': gates,
        'required_passed': all(
            item['status'] == 'passed'
            for item in gates if item['required']
        ),
        'optional_unavailable': [
            item['gate'] for item in gates
            if item['status'] == 'unavailable'
        ],
        'transaction_finality_interpreted_by_common_code': False,
    }
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + '\n', encoding='utf-8')
    print(output)
    return 0 if report['required_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
