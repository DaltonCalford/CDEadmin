#!/usr/bin/env python3
"""Test visual parameter records using disposable Firebird routines."""

import argparse
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(ROOT / 'web/pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    ADMINISTRATION, _create_client, _route_arguments,
)
from pgadmin.cdeadmin.visual_admin import (  # noqa: E402
    ProviderVisualAdministration,
)


def run(profiles_path):
    document = json.loads(profiles_path.read_text())
    profile = next(dict(item) for item in document['profiles']
                   if item['engine'] == 'firebird')
    profile.setdefault('host', document.get('host', '127.0.0.1'))
    from firebird import driver
    client = _create_client(SimpleNamespace(acquire_secret=None))
    connection = driver.connect(
        password=profile['password'], **_route_arguments(profile, driver))
    prefix = 'CDE_ROUTINE_' + uuid.uuid4().hex[:12].upper()
    created = []
    result = {'checks': [], 'status': 'failed', 'prefix': prefix}

    def apply(kind, action, name, sign='-', commit=True):
        target = {'resource_kind': kind, 'resource_id': kind + ':' + name,
                  'display_name': name, 'display_path': [name]}
        draft = {'parameters': [
            {'name': 'P_A', 'type': 'INTEGER'},
            {'name': 'P_B', 'type': 'INTEGER'}]}
        if kind == 'function':
            draft.update(returns='INTEGER',
                         body=f'BEGIN RETURN P_A {sign} P_B; END')
        else:
            draft.update(return_parameters=[{'name': 'R', 'type': 'INTEGER'}],
                         body=f'BEGIN R = P_A {sign} P_B; SUSPEND; END')
        if action == 'create':
            draft['name'] = name
        if action == 'drop':
            draft = {}
        else:
            form = ADMINISTRATION._form(kind, action)
            ADMINISTRATION._routine_record_controls(form)
            for field in form['fields']:
                key = field['field_id']
                if key in draft:
                    draft[key], error = (
                        ProviderVisualAdministration._validate_field(
                            field, draft[key]))
                    assert error is None, error
        plan = ADMINISTRATION.plan({
            'resource_kind': kind, 'operation_id': action,
            'target_resource': target, 'draft': draft,
            '_provider_route': profile})
        ADMINISTRATION.apply(client, plan, connection=connection)
        if commit:
            connection.commit()

    def value(kind, name):
        cursor = connection.cursor()
        try:
            source = (f'SELECT "{name}"(9, 4) FROM RDB$DATABASE'
                      if kind == 'function' else
                      f'SELECT R FROM "{name}"(9, 4)')
            cursor.execute(source)
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    try:
        for kind in ('function', 'procedure'):
            name = prefix + ('_F' if kind == 'function' else '_P')
            apply(kind, 'create', name)
            created.append((kind, name))
            assert value(kind, name) == 5
            result['checks'].append(kind + '-create-parameter-order')
            connection.commit()
            apply(kind, 'alter', name, '+')
            assert value(kind, name) == 13
            result['checks'].append(kind + '-alter-commit')
            connection.commit()
            apply(kind, 'alter', name, '*', commit=False)
            connection.rollback()
            assert value(kind, name) == 13
            result['checks'].append(kind + '-alter-rollback')
            connection.commit()
        result.update(status='passed', engine_version=connection.info.version)
        return result
    finally:
        try:
            if connection.main_transaction.is_active():
                connection.rollback()
            for kind, name in reversed(created):
                apply(kind, 'drop', name)
            cursor = connection.cursor()
            try:
                for kind, name in created:
                    table, column = (
                        ('RDB$FUNCTIONS', 'RDB$FUNCTION_NAME')
                        if kind == 'function' else
                        ('RDB$PROCEDURES', 'RDB$PROCEDURE_NAME'))
                    cursor.execute(f'SELECT COUNT(*) FROM {table} '
                                   f'WHERE {column} = ?', (name,))
                    assert cursor.fetchone()[0] == 0
                result['cleanup_verified'] = True
            finally:
                cursor.close()
        finally:
            connection.close()
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, default=ROOT /
                        'tools/reference_engine_demos/runtime/'
                        'connection_profiles.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.profiles)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
