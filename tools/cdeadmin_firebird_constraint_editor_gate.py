#!/usr/bin/env python3
"""Verify structured constraint drafts on disposable Firebird demo tables."""

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
    name = 'CDE_QA_' + uuid.uuid4().hex[:12].upper()
    parent, child = name + '_P', name + '_C'
    created = []
    result = {'checks': [], 'tables': [parent, child], 'status': 'failed'}

    def execute(source, parameters=(), fetch=False):
        cursor = connection.cursor()
        try:
            cursor.execute(source, parameters)
            return cursor.fetchall() if fetch else None
        finally:
            cursor.close()

    def apply(kind, operation, draft, target=None, commit=True):
        plan = ADMINISTRATION.plan({
            'resource_kind': kind, 'operation_id': operation,
            'target_resource': target, 'draft': draft,
            '_provider_route': profile})
        ADMINISTRATION.apply(client, plan, connection=connection)
        if commit:
            connection.commit()

    def add_constraint(suffix, properties, commit=True):
        form = ADMINISTRATION._form('constraint', 'create')
        ADMINISTRATION._structured_record_controls(form)
        field = next(item for item in form['fields']
                     if item['field_id'] == 'properties')
        admitted, error = ProviderVisualAdministration._validate_field(
            field, properties)
        assert error is None, error
        apply('constraint', 'create', {
            'name': name + suffix, 'table': child, 'properties': admitted},
            commit=commit)

    try:
        for table in (parent, child):
            apply('table', 'create', {'name': table, 'columns': [
                {'name': 'ID', 'type': 'INTEGER', 'nullable': False,
                 'primary_key': table == parent},
                {'name': 'VALUE', 'type': 'INTEGER'},
            ]})
            created.append(table)
        for suffix, props in (
                ('_PK', {'kind': 'PRIMARY KEY', 'columns': ['ID']}),
                ('_UQ', {'kind': 'UNIQUE', 'columns': ['VALUE']}),
                ('_CK', {'kind': 'CHECK', 'expression': '"VALUE" > 0'}),
                ('_FK', {'kind': 'FOREIGN KEY', 'columns': ['ID'],
                         'references_table': parent,
                         'references_columns': ['ID']})):
            add_constraint(suffix, props)
            result['checks'].append('create' + suffix)
        execute(f'INSERT INTO "{parent}" VALUES (1, 10)')
        execute(f'INSERT INTO "{parent}" VALUES (2, 20)')
        execute(f'INSERT INTO "{child}" VALUES (1, 10)')
        connection.commit()
        for label, values in (('primary-key', (1, 11)), ('unique', (2, 10)),
                              ('check', (2, -1)), ('foreign-key', (3, 30))):
            try:
                execute(f'INSERT INTO "{child}" VALUES (?, ?)', values)
            except driver.DatabaseError as error:
                if error.sqlstate != '23000':
                    raise
                result['checks'].append('enforced-' + label)
            else:
                raise AssertionError(label + ' accepted invalid data')
            finally:
                connection.rollback()
        add_constraint('_RB', {'kind': 'CHECK',
                               'expression': '"VALUE" < 100'}, commit=False)
        if connection.main_transaction.is_active():
            connection.rollback()
        execute(f'INSERT INTO "{child}" VALUES (2, 100)')
        connection.rollback()
        assert execute(f'SELECT "ID" FROM "{child}"', fetch=True) == [(1,)]
        result['checks'].append('constraint-ddl-and-row-rollback')
        result.update(status='passed', engine_version=connection.info.version)
        return result
    finally:
        try:
            if connection.main_transaction.is_active():
                connection.rollback()
            for table in reversed(created):
                apply('table', 'drop', {}, {
                    'resource_kind': 'table', 'display_name': table,
                    'display_path': [table], 'resource_id': 'table:' + table})
            assert not execute(
                'SELECT RDB$RELATION_NAME FROM RDB$RELATIONS '
                'WHERE RDB$RELATION_NAME IN (?, ?)', (parent, child), True)
            result['cleanup_verified'] = True
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
