#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Inject local registration failures after real owned database mutations."""

import argparse
import json
import subprocess
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, _create_client, _route_arguments,
)
from pgadmin.cdeadmin.security.secrets import SecretLease
from pgadmin.cdeadmin.workspace.service import ProviderWorkspaceService


def run(profiles, container):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    path = str(PurePosixPath(route['database']).parent /
               ('cde_registration_' + uuid.uuid4().hex + '.fdb'))
    password = route.pop('password')
    route.update(credential_reference_id='owned-registration-secret',
                 principal_reference='owned-registration-principal')
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    owned = False
    result = {'complete': False, 'cases': [], 'failures': [],
              'fixture_removed': False, 'fixture_database': path,
              'authorization_qualification': False}
    applied = []
    invalidated = []
    context = SimpleNamespace(endpoint_id='owned-registration-gate')

    def absent():
        process = subprocess.run(
            ['docker', 'exec', container, 'test', '!', '-e', path],
            capture_output=True, check=False)
        return process.returncode == 0

    def fail_registration(*_args):
        raise RuntimeError('injected private registration failure')

    workspace = object.__new__(ProviderWorkspaceService)
    workspace.endpoint_service = SimpleNamespace(
        retain_created_database=fail_registration,
        delete_database_target=fail_registration)
    workspace.resource_service = SimpleNamespace(
        invalidate=lambda value: invalidated.append(value.endpoint_id))

    def apply(plan, operation):
        def native(_payload):
            applied.append(operation)
            return {'provider_result': ADMINISTRATION.apply(client, plan)}
        workspace._prepare_visual_admin_call = lambda *_args: (
            context, native, {})
        return workspace.apply_visual_admin(SimpleNamespace(), {})

    try:
        assert absent(), 'Owned fixture path was not confirmed absent'
        plan = ADMINISTRATION.plan({
            '_provider_route': route, 'resource_kind': 'database',
            'operation_id': 'create', 'target_resource': None,
            'draft': {'database_path': path}})
        created = apply(plan, 'create')
        owned = True
        assert not absent(), 'Created file not observed in the test container'
        assert created['provider_result']['accepted'] is True
        assert created['workspace_follow_up'][0]['state'] == 'failed'
        assert not created['workspace_follow_up'][0][
            'automatic_mutation_retry']
        assert 'injected private' not in repr(created)
        native_route = {**route, 'database': path}
        connection = driver.connect(password=password,
                                    **_route_arguments(native_route, driver))
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT RDB$GET_CONTEXT('SYSTEM', "
                               "'ENGINE_VERSION') FROM RDB$DATABASE")
                result['engine_version'] = cursor.fetchone()[0]
                assert result['engine_version'] == '5.0.4'
                cursor.execute('CREATE TABLE PRESERVED (V INTEGER)')
            connection.commit()
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO PRESERVED VALUES (42)')
            connection.commit()
        finally:
            connection.close()
        assert applied == ['create']
        result['cases'].append({'case': 'create-registration-failure',
                                'native_database_usable': True,
                                'native_replays': 0})
        plan = ADMINISTRATION.plan({
            '_provider_route': native_route, 'resource_kind': 'database',
            'operation_id': 'drop', 'target_resource': {
                'resource_kind': 'database', 'display_name': path,
                'display_path': [path], 'extensions': {'cdeadmin': {
                    'database_target_id': str(uuid.uuid4())}}},
            'draft': {'confirmation': path}})
        dropped = apply(plan, 'drop')
        assert dropped['provider_result']['accepted'] is True
        assert dropped['workspace_follow_up'][0]['state'] == 'failed'
        assert not dropped['workspace_follow_up'][0][
            'automatic_mutation_retry']
        assert absent(), 'Native DROP did not remove the owned fixture file'
        owned = False
        result['fixture_removed'] = True
        assert applied == ['create', 'drop']
        assert invalidated == [context.endpoint_id] * 2
        result['cases'].append({'case': 'drop-registration-failure',
                                'native_file_absence_verified': True,
                                'native_replays': 0})
    except Exception:
        result['failures'].append({'traceback': traceback.format_exc()})
    finally:
        if owned:
            try:
                connection = driver.connect(
                    password=password,
                    **_route_arguments({**route, 'database': path}, driver))
                connection.drop_database()
                result['fixture_removed'] = absent()
            except Exception:
                result['failures'].append({'stage': 'cleanup',
                                          'traceback': traceback.format_exc()})
    result['complete'] = (len(result['cases']) == 2 and
                          result['fixture_removed'] and not result['failures'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--container', default='cdeadmin-demo-firebird')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.profiles, args.container)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
