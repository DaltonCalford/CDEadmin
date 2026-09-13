#!/usr/bin/env python3
"""Exercise sequence editor plans on one uniquely named disposable sequence."""

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
    ADMINISTRATION, _create_client, _resources, _route_arguments,
    _sequence_state,
)


def run(profiles_path):
    document = json.loads(profiles_path.read_text())
    profile = next(dict(item) for item in document['profiles']
                   if item['engine'] == 'firebird')
    profile.setdefault('host', document.get('host', '127.0.0.1'))
    client = _create_client(SimpleNamespace(acquire_secret=None))
    from firebird import driver
    connection = driver.connect(
        password=profile['password'], **_route_arguments(profile, driver))
    name = 'CDEADMIN_SEQ_QA_' + uuid.uuid4().hex[:12].upper()
    target = {'resource_id': 'sequence:' + name, 'resource_kind': 'sequence',
              'display_name': name, 'display_path': [name]}
    checks = []
    created = False
    result = {'checks': checks, 'disposable_sequence': name}

    def apply(operation, draft, commit=True):
        plan = ADMINISTRATION.plan({
            'resource_kind': 'sequence', 'operation_id': operation,
            'target_resource': target, 'draft': draft,
            '_provider_route': profile,
        })
        ADMINISTRATION.apply(client, plan, connection=connection)
        if commit:
            connection.commit()

    def state():
        cursor = connection.cursor()
        try:
            result = _sequence_state(cursor, name)
            if not result['available']:
                raise AssertionError('Sequence observation unavailable')
            return result['current_value']
        finally:
            cursor.close()

    def next_value():
        cursor = connection.cursor()
        try:
            cursor.execute(f'SELECT NEXT VALUE FOR "{name}" FROM RDB$DATABASE')
            return str(cursor.fetchone()[0])
        finally:
            cursor.close()

    def inspect():
        return next(item['native'] for item in _resources(connection, {
            'route': profile, 'resource_id': target['resource_id']})
            if item['resource_id'] == target['resource_id'])

    try:
        apply('create', {'name': name, 'start': '10', 'increment': 2})
        created = True
        assert state() == state() == '8'
        assert next_value() == '10'
        checks.append('inspect-does-not-consume-value')
        before = inspect()
        assert before['initial_value'] == '10'
        assert before['increment'] == 2
        assert before['state']['current_value'] == '10'
        assert before['owner']
        apply('alter', {'description': "Owner's QA note"})
        after = inspect()
        assert after['description'] == "Owner's QA note"
        assert after['increment'] == before['increment']
        assert after['initial_value'] == before['initial_value']
        assert after['state'] == before['state']
        assert "Owner''s QA note" in after['ddl']
        checks.append('comment-edit-preserves-position-increment-start')
        apply('alter', {'description': 'not committed'}, commit=False)
        connection.rollback()
        assert inspect()['description'] == "Owner's QA note"
        checks.append('comment-alter-rollback')
        long_comment = '  Résumé 序列\n' + ('comment line\n' * 800) + '  '
        apply('alter', {'description': long_comment})
        observed_comment = inspect()['description']
        assert observed_comment == long_comment, repr(observed_comment[:60])
        checks.append('long-comment-whitespace-round-trip')
        apply('alter', {'increment': -3})
        assert state() == '10'
        assert next_value() == '7'
        checks.append('increment-edit-does-not-restart')
        apply('alter', {'restart': '9223372036854775807'})
        assert next_value() == '9223372036854775807'
        assert inspect()['state']['current_value'] == '9223372036854775807'
        checks.append('int64-restart-and-observation-exact')
        apply('alter', {'restart_initial': True})
        assert next_value() == '10'
        checks.append('restart-original-start')
        apply('alter', {'clear_description': True})
        assert inspect()['description'] is None
        checks.append('clear-comment')
        result.update(status='passed', engine_version=connection.info.version)
        return result
    finally:
        connection.rollback()
        try:
            if created:
                apply('drop', {})
                cursor = connection.cursor()
                try:
                    cursor.execute('SELECT COUNT(*) FROM RDB$GENERATORS '
                                   'WHERE RDB$GENERATOR_NAME = ?', (name,))
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
