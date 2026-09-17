#!/usr/bin/env python3
"""Compare Python and isql literal views on disposable Firebird."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


def cases():
    return [
        ('bare', '\'A"B\', \'tail\''),
        ('varchar', 'CAST(\'A"B\' AS VARCHAR(20)), '
         'CAST(\'tail\' AS VARCHAR(20))'),
        ('utf8', '_UTF8\'A"B\', _UTF8\'tail\''),
        ('single_byte', '_ISO8859_1\'A"B\', _ISO8859_1\'tail\''),
    ]


def owned_container(route):
    identifiers = base.docker(
        'ps', '--no-trunc', '--filter', 'label=cdeadmin-owned-gate=' +
        base.OWNER, '--format', '{{.ID}}').decode().split()
    matches = [value for value in identifiers if
               re.fullmatch('[0-9a-f]{64}', value) and
               base.published_port(value) == route['port']]
    if route['host'] != '127.0.0.1' or len(matches) != 1:
        raise ValueError('Literal probe requires one owned loopback container')
    return matches[0]


def verify(connection, client, route, password, result):
    import firebird.driver as native
    from firebird.driver import core
    from pgadmin.cdeadmin.providers.firebird.database_creation import (
        create_database,
    )
    from pgadmin.cdeadmin.providers.firebird.provider import (
        _database_create_arguments, _route_arguments, _resources,
    )
    from pgadmin.cdeadmin.providers.firebird.error_diagnostics import (
        status_codes,
    )

    container = owned_container(route)
    checks = result['literal_view_comparisons'] = []
    result['literal_view_scope'] = 'diagnostic parity, not feature activation'

    for dialect in (1, 3):
        configured = {**route, 'database':
                      f'/var/lib/firebird/data/literal_probe_{dialect}.fdb'}
        args = _database_create_arguments(
            configured, _route_arguments(configured)['database'],
            {'sql_dialect': dialect}, native)
        created = create_database(native, core, password=password, **args)
        created.close()
        handle = native.connect(password=password,
                                **_route_arguments(configured, native))

        def sql(source):
            with handle.cursor() as cursor:
                cursor.execute(source)
                return cursor.fetchall() if cursor.description else []

        def rollback():
            if handle.main_transaction.is_active():
                handle.rollback()

        def isql(source, charset='UTF8'):
            completed = subprocess.run([
                'docker', 'exec', '-i', '--env', 'ISC_USER=SYSDBA',
                '--env', 'ISC_PASSWORD', container, '/opt/firebird/bin/isql',
                '-q', '-ch', charset, '-sql_dialect', '3',
                'localhost:' + configured['database']],
                input=(source + '\nQUIT;\n').encode('utf8'),
                env=dict(os.environ, ISC_PASSWORD=password),
                capture_output=True, timeout=45, check=False)
            return {
                'exit_code': completed.returncode,
                'stdout': completed.stdout.decode('utf8', 'replace').replace(
                    password, '<redacted>'),
                'stderr': completed.stderr.decode('utf8', 'replace').replace(
                    password, '<redacted>'),
            }

        try:
            for creator in ('python', 'isql'):
                for label, expression in cases():
                    row = {'dialect': dialect, 'creator': creator,
                           'case': label, 'passed': False}
                    checks.append(row)
                    name = 'V_' + creator.upper() + '_' + label.upper()
                    query = 'SELECT ' + expression + ' FROM RDB$DATABASE'
                    try:
                        assert sql(query) == [('A"B', 'tail')]
                        rollback()
                        ddl = 'CREATE VIEW ' + name + ' (B, A) AS ' + query
                        if creator == 'python':
                            sql(ddl)
                            handle.commit()
                        else:
                            row['isql_create'] = isql(ddl + ';\nCOMMIT;')
                            assert row['isql_create']['exit_code'] == 0
                        row['metadata'] = sql(
                            'SELECT TRIM(RF.RDB$FIELD_NAME), '
                            'F.RDB$FIELD_TYPE, F.RDB$FIELD_LENGTH, '
                            'F.RDB$CHARACTER_LENGTH, F.RDB$CHARACTER_SET_ID '
                            'FROM RDB$RELATION_FIELDS RF JOIN RDB$FIELDS F '
                            'ON F.RDB$FIELD_NAME = RF.RDB$FIELD_SOURCE '
                            "WHERE RF.RDB$RELATION_NAME = '" + name + "' "
                            'ORDER BY RF.RDB$FIELD_POSITION')
                        resource = next(item for item in _resources(
                            handle, {'route': configured}) if
                            item['resource_kind'] == 'view' and
                            item['display_name'] == name)
                        row['catalog_warnings'] = resource['native'].get(
                            'catalog_warnings', [])
                        assert bool(row['catalog_warnings']) == (
                            label in {'bare', 'utf8'})
                        rollback()
                        try:
                            row['python_rows'] = sql('SELECT * FROM ' + name)
                        except native.DatabaseError as exc:
                            row['python_status_codes'] = list(
                                status_codes(exc))
                            row['python_error'] = str(exc).replace(
                                password, '<redacted>')
                        rollback()
                        row['isql_utf8'] = isql('SELECT * FROM ' + name + ';')
                        row['isql_none'] = isql('SELECT * FROM ' + name + ';',
                                                charset='NONE')
                        succeeded = row.get('python_rows') == [('A"B', 'tail')]
                        assert succeeded == (
                            row['isql_utf8']['exit_code'] == 0)
                        assert succeeded == (
                            row['isql_none']['exit_code'] == 0)
                        if succeeded:
                            assert all(value in row['isql_utf8']['stdout'] for
                                       value in ('A"B', 'tail'))
                        else:
                            assert row['python_status_codes'] == [
                                335544321, 335544914, 335545033]
                            assert 'string right truncation' in (
                                row['isql_utf8']['stderr'])
                        if creator == 'isql':
                            comparison = next(item for item in checks if
                                              item['dialect'] == dialect and
                                              item['creator'] == 'python' and
                                              item['case'] == label)
                            assert row['metadata'] == comparison['metadata']
                        row['passed'] = True
                    except Exception as exc:
                        result['failures'].append({
                            'case': f'literal-{dialect}-{creator}-{label}',
                            'error_type': type(exc).__name__,
                            'message': str(exc).replace(
                                password, '<redacted>')})
                    finally:
                        rollback()
        finally:
            handle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = base.run(extra_checks=verify)
    checks = result.get('literal_view_comparisons', [])
    result['complete'] = (result['complete'] and len(checks) == 16 and
                          all(check['passed'] for check in checks))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
