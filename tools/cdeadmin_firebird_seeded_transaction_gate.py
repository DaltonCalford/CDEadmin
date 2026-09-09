#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Verify seeded Firebird objects and two-session transaction behavior."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cdeadmin_relational_provider_live_verify import (  # noqa: E402
    PROFILES,
    PROVIDER_FACTORIES,
    _context,
    _permissions,
    _verified_context,
)
from pgadmin.cdeadmin.security import (  # noqa: E402
    EndpointSecretService,
    SecretReference,
)


SEEDED_OBJECTS = {
    'table': {'CUSTOMERS', 'ASSETS', 'WORK_ORDERS'},
    'view': {'OPEN_WORK_ORDERS'},
    'index': {'IX_WORK_ORDERS_STATUS_PRIORITY'},
    'sequence': {'INSPECTION_NUMBER'},
}

TABLE_CASES = (
    {
        'table': 'CUSTOMERS',
        'id_column': 'CUSTOMER_ID',
        'marker': 2147483001,
        'insert_source': (
            'INSERT INTO CUSTOMERS '
            '(CUSTOMER_ID, NAME, CITY, REGION) VALUES (?, ?, ?, ?)'
        ),
        'insert_parameters': (
            2147483001, 'CDEadmin transaction customer', 'Toronto', 'QA'
        ),
        'value_column': 'NAME',
        'initial_value': 'CDEadmin transaction customer',
        'updated_value': 'CDEadmin committed customer',
    },
    {
        'table': 'ASSETS',
        'id_column': 'ASSET_ID',
        'marker': 2147483002,
        'insert_source': (
            'INSERT INTO ASSETS '
            '(ASSET_ID, CUSTOMER_ID, TAG, ASSET_TYPE, STATUS) '
            'VALUES (?, ?, ?, ?, ?)'
        ),
        'insert_parameters': (
            2147483002, 1, 'CDEADMIN-TX-ASSET', 'qualification', 'staged'
        ),
        'value_column': 'STATUS',
        'initial_value': 'staged',
        'updated_value': 'committed',
    },
    {
        'table': 'WORK_ORDERS',
        'id_column': 'WORK_ORDER_ID',
        'marker': 2147483003,
        'insert_source': (
            'INSERT INTO WORK_ORDERS '
            '(WORK_ORDER_ID, ASSET_ID, TECHNICIAN, SUMMARY, STATUS, '
            'PRIORITY) VALUES (?, ?, ?, ?, ?, ?)'
        ),
        'insert_parameters': (
            2147483003, 101, 'CDEadmin QA',
            'Provider transaction qualification', 'staged', 1
        ),
        'value_column': 'STATUS',
        'initial_value': 'staged',
        'updated_value': 'committed',
    },
)


def _load_profile(path):
    document = json.loads(path.read_text(encoding='utf-8'))
    profiles = document.get('profiles', [])
    matches = [item for item in profiles if item.get('engine') == 'firebird']
    if len(matches) != 1:
        raise RuntimeError(
            'exactly one Firebird demo connection profile is required'
        )
    result = dict(matches[0])
    result.setdefault('host', document.get('host'))
    if not result.get('host'):
        raise RuntimeError('Firebird demo host is unavailable')
    return result


def _session_reference(session):
    return hashlib.sha256(
        session['session_id'].encode('utf-8')
    ).hexdigest()[:16]


def _execute(provider, session, sequence, source, parameters=()):
    sequence[0] += 1
    operation = provider.execute({
        'session_id': session['session_id'],
        'execution_id': f'firebird-seeded-transaction-{sequence[0]}',
        'source': source,
        'parameters': parameters,
    })
    result = provider.describe_result(operation)
    if not result['complete']:
        raise RuntimeError('seeded Firebird result is incomplete')
    return result['extensions']['firebird']['payload']


def _control(provider, session, action):
    presentation = provider.control_transaction({
        'session_id': session['session_id'], 'action': action,
    })['provider_payload']
    if presentation.get('driver_observation_only') is not True:
        raise RuntimeError('Firebird transaction observation is not opaque')
    if presentation.get('finality_interpreted_by_common_code') is not False:
        raise RuntimeError('common code interpreted transaction finality')
    return presentation


def _observe(provider, session, sequence, source, parameters=()):
    rows = _execute(provider, session, sequence, source, parameters).get(
        'rows'
    ) or []
    if not rows or not rows[0]:
        raise RuntimeError('seeded Firebird observer returned no value')
    value = rows[0][0]
    _control(provider, session, 'rollback')
    return value


def _verify_table(provider, writer, observer, sequence, case):
    table = case['table']
    identifier = case['id_column']
    marker = case['marker']
    value_column = case['value_column']
    count_source = f'SELECT COUNT(*) FROM {table} WHERE {identifier} = ?'
    value_source = (
        f'SELECT {value_column} FROM {table} WHERE {identifier} = ?'
    )
    update_source = (
        f'UPDATE {table} SET {value_column} = ? WHERE {identifier} = ?'
    )
    delete_source = f'DELETE FROM {table} WHERE {identifier} = ?'

    _execute(provider, writer, sequence, delete_source, (marker,))
    _control(provider, writer, 'commit')
    if int(_observe(
            provider, observer, sequence, count_source, (marker,))) != 0:
        raise RuntimeError(f'{table} disposable marker cleanup failed')

    _execute(
        provider, writer, sequence, case['insert_source'],
        case['insert_parameters'],
    )
    insert_before_commit = int(_observe(
        provider, observer, sequence, count_source, (marker,)
    ))
    if insert_before_commit != 0:
        raise RuntimeError(f'{table} insert was visible before commit')
    insert_commit_observation = _control(provider, writer, 'commit')
    insert_after_commit = str(_observe(
        provider, observer, sequence, value_source, (marker,)
    ))
    if insert_after_commit != case['initial_value']:
        raise RuntimeError(f'{table} insert commit was not visible')

    _execute(
        provider, writer, sequence, update_source,
        (case['updated_value'], marker),
    )
    update_before_rollback = str(_observe(
        provider, observer, sequence, value_source, (marker,)
    ))
    if update_before_rollback != case['initial_value']:
        raise RuntimeError(f'{table} update was visible before commit')
    update_rollback_observation = _control(provider, writer, 'rollback')
    update_after_rollback = str(_observe(
        provider, observer, sequence, value_source, (marker,)
    ))
    if update_after_rollback != case['initial_value']:
        raise RuntimeError(f'{table} update rollback did not restore state')

    _execute(
        provider, writer, sequence, update_source,
        (case['updated_value'], marker),
    )
    update_commit_observation = _control(provider, writer, 'commit')
    update_after_commit = str(_observe(
        provider, observer, sequence, value_source, (marker,)
    ))
    if update_after_commit != case['updated_value']:
        raise RuntimeError(f'{table} update commit was not visible')

    _execute(provider, writer, sequence, delete_source, (marker,))
    delete_before_rollback = int(_observe(
        provider, observer, sequence, count_source, (marker,)
    ))
    if delete_before_rollback != 1:
        raise RuntimeError(f'{table} delete was visible before commit')
    delete_rollback_observation = _control(provider, writer, 'rollback')
    delete_after_rollback = int(_observe(
        provider, observer, sequence, count_source, (marker,)
    ))
    if delete_after_rollback != 1:
        raise RuntimeError(f'{table} delete rollback did not restore state')

    _execute(provider, writer, sequence, delete_source, (marker,))
    delete_commit_observation = _control(provider, writer, 'commit')
    delete_after_commit = int(_observe(
        provider, observer, sequence, count_source, (marker,)
    ))
    if delete_after_commit != 0:
        raise RuntimeError(f'{table} delete commit was not visible')

    return {
        'table': table,
        'primary_key': identifier,
        'provider_finality_authority': True,
        'common_finality_interpreted': False,
        'insert': {
            'observer_count_before_commit': insert_before_commit,
            'observer_value_after_commit': insert_after_commit,
            'provider_observation': insert_commit_observation,
        },
        'update': {
            'observer_value_before_rollback': update_before_rollback,
            'observer_value_after_rollback': update_after_rollback,
            'observer_value_after_commit': update_after_commit,
            'rollback_observation': update_rollback_observation,
            'commit_observation': update_commit_observation,
        },
        'delete': {
            'observer_count_before_rollback': delete_before_rollback,
            'observer_count_after_rollback': delete_after_rollback,
            'observer_count_after_commit': delete_after_commit,
            'rollback_observation': delete_rollback_observation,
            'commit_observation': delete_commit_observation,
        },
        'disposable_row_removed': True,
    }


def _inspect_seeded_objects(provider, request):
    resources = provider.list_resources(request)
    inspected = {}
    for kind, expected_names in SEEDED_OBJECTS.items():
        matches = {
            str(item.get('display_name', '')).strip().upper(): item
            for item in resources if item.get('resource_kind') == kind
        }
        missing = expected_names.difference(matches)
        if missing:
            raise RuntimeError(
                f'Firebird seeded {kind} objects are missing: '
                f'{", ".join(sorted(missing))}'
            )
        inspected[kind] = []
        for name in sorted(expected_names):
            resource = matches[name]
            descriptor = provider.inspect_resource({
                **request, 'resource_id': resource['resource_id'],
            })
            inspected[kind].append({
                'display_name': name,
                'resource_id': resource['resource_id'],
                'descriptor_kind': descriptor['resource_kind'],
            })
    return inspected


def run(profile_path):
    connection = _load_profile(profile_path)
    password = str(connection.pop('password', ''))
    if not password:
        raise RuntimeError('Firebird demo credential is unavailable')
    profile = PROFILES['firebird']
    context = _context(profile)
    secrets_service = EndpointSecretService()
    reference_id = str(uuid.uuid5(
        uuid.UUID(context.endpoint_id), 'seeded-demo-password'
    ))
    secrets_service.register_resolver(
        'firebird.seeded-demo', lambda *_args: password.encode('utf-8')
    )
    secrets_service.register_reference(SecretReference(
        reference_id=reference_id,
        endpoint_id=context.endpoint_id,
        endpoint_mode=context.mode,
        secret_kind='database_password',
        storage_kind='packaged_demo_profile',
        resolver_id='firebird.seeded-demo',
        locator='packaged-demo:firebird',
        allowed_purposes=frozenset({'connect'}),
        authority_scope='legacy_engine_auth',
    ))
    route = {
        'route_id': 'firebird-seeded-demo-transaction',
        'host': connection['host'],
        'port': int(connection['port']),
        'user': connection['user'],
        'database': connection['database'],
        'role': 'RDB$ADMIN',
        'credential_reference_id': reference_id,
        'principal_reference': 'packaged-firebird-demo',
        'connection_timeout': 10,
    }
    request = {
        'route': route,
        'capability_generation': 'firebird-seeded-demo-transaction',
    }
    factory = PROVIDER_FACTORIES['firebird']
    provider = factory(context, _permissions(context, secrets_service))
    sequence = [0]
    try:
        discovered = provider.discover_endpoint(request)
        if discovered['verified_runtime']['version'] != profile.exact_version:
            raise RuntimeError('exact Firebird demo runtime was not verified')
        provider.close()
        context = _verified_context(context, discovered)
        provider = factory(context, _permissions(context, secrets_service))
        inspected = _inspect_seeded_objects(provider, request)
        writer = provider.open_session(request)
        observer = provider.open_session(request)
        initial_sessions = {
            'writer': _session_reference(writer),
            'observer': _session_reference(observer),
        }
        table_results = []
        for case in TABLE_CASES:
            table_results.append(_verify_table(
                provider, writer, observer, sequence, case
            ))

        provider.close()
        provider = factory(context, _permissions(context, secrets_service))
        reconnected_writer = provider.open_session(request)
        reconnected_observer = provider.open_session(request)
        reconnected_sessions = {
            'writer': _session_reference(reconnected_writer),
            'observer': _session_reference(reconnected_observer),
        }
        durable_cleanup = {}
        for case in TABLE_CASES:
            durable_cleanup[case['table']] = int(_observe(
                provider, reconnected_observer, sequence,
                f"SELECT COUNT(*) FROM {case['table']} WHERE "
                f"{case['id_column']} = ?", (case['marker'],),
            ))
        if any(durable_cleanup.values()):
            raise RuntimeError(
                'Firebird disposable rows remained after reconnect'
            )
        _control(provider, reconnected_writer, 'rollback')
        return {
            'schema': 'cdeadmin.firebird-seeded-transaction-gate.v1',
            'engine_id': 'firebird',
            'interface_id': 'firebird-native',
            'reference_version': profile.exact_version,
            'runtime_identity': discovered['verified_runtime'],
            'database_identifier': connection['database'],
            'seeded_objects_inspected': inspected,
            'editable_seeded_tables': [
                item['table'] for item in table_results
            ],
            'table_transaction_results': table_results,
            'initial_session_references': initial_sessions,
            'reconnected_session_references': reconnected_sessions,
            'durable_cleanup_after_reconnect': durable_cleanup,
            'provider_finality_authority': True,
            'common_finality_interpreted': False,
            'credential_values_exported': False,
            'secret_access_events': len(secrets_service.audit_events()),
            'passed': True,
        }
    finally:
        provider.close()
        password = ''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--profiles', type=Path,
        default=Path(os.environ.get(
            'CDEADMIN_REFERENCE_PROFILES',
            ROOT / 'tools/reference_engine_demos/runtime/'
            'connection_profiles.json',
        )),
    )
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(argv)
    result = run(options.profiles)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'engine_id': result['engine_id'],
        'reference_version': result['reference_version'],
        'seeded_object_count': sum(
            len(items) for items in result['seeded_objects_inspected'].values()
        ),
        'editable_seeded_tables': result['editable_seeded_tables'],
        'passed': result['passed'],
        'credential_values_exported': False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
