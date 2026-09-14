#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Exercise global mapping variants only in a disposable, isolated server."""

import argparse
import itertools
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, _create_client, _resources, _route_arguments,
)
from pgadmin.cdeadmin.providers.firebird import mappings
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def run(options=None):
    import firebird.driver as driver
    client = _create_client(SimpleNamespace(acquire_secret=None))
    name = 'cdeadmin-mapping-qa-' + uuid.uuid4().hex
    password = secrets.token_urlsafe(24)
    environment = dict(os.environ, FIREBIRD_ROOT_PASSWORD=password)
    result = {'schema': 'cdeadmin.firebird-mapping-matrix.v1',
              'status': 'failed', 'checks': [], 'failures': [],
              'task_evidence': {}, 'container': name,
              'container_removed': False, 'credential_values_exported': False}
    connection = None
    created = False
    try:
        subprocess.run([
            'docker', 'run', '-d', '--name', name, '--pull', 'never',
            '--label', 'org.cdeadmin.fixture=firebird-mapping-matrix',
            '-p', '127.0.0.1::3050', '--tmpfs', '/var/lib/firebird/data',
            '-e', 'FIREBIRD_ROOT_PASSWORD', 'firebirdsql/firebird:5.0.4',
        ], env=environment, check=True, capture_output=True, text=True)
        created = True
        port = int(subprocess.check_output(
            ['docker', 'port', name, '3050/tcp'], text=True).strip().rsplit(
                ':', 1)[1])
        route = {'host': '127.0.0.1', 'port': port, 'user': 'SYSDBA',
                 'database': '/var/lib/firebird/data/matrix.fdb'}
        deadline = time.monotonic() + 120
        while True:
            try:
                connection = driver.create_database(
                    password=password, **_route_arguments(route, driver))
                break
            except driver.DatabaseError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
                'FROM RDB$DATABASE')
            result['engine_version'] = cursor.fetchone()[0]
        connection.commit()
        assert result['engine_version'] == '5.0.4'

        def apply(kind, operation, draft, target='CDE_MAPPING', conn=None):
            plan = ADMINISTRATION.plan({
                '_provider_route': route, 'resource_kind': kind,
                'operation_id': operation,
                'target_resource': {'display_name': target}, 'draft': draft})
            ADMINISTRATION.apply(client, plan, connection=conn or connection)
            if kind in mappings.KINDS:
                result['task_evidence'][f'visual_admin.{kind}.{operation}'] = {
                    'statements': [item['source'] for item in
                                   plan['command_preview']['statements']],
                    'live_execution': 'passed',
                }

        def observe(kind):
            with connection.cursor() as cursor:
                rows = list(mappings.catalog_rows(
                    cursor, kind == mappings.KINDS[1]))
            connection.commit()
            return next((row for row in rows if row[0] == 'CDE_MAPPING'), None)

        def case(label, callback):
            try:
                callback()
                result['checks'].append(label)
            except Exception as error:
                result['failures'].append({
                    'case': label, 'type': type(error).__name__,
                    'traceback': traceback.format_exc()})
                if connection.main_transaction.is_active():
                    connection.rollback()
            finally:
                for kind in mappings.KINDS:
                    if observe(kind) is not None:
                        apply(kind, 'drop', {'confirmation': 'CDE_MAPPING'})
                        connection.commit()

        for kind, mode, any_name, to_type in itertools.product(
                mappings.KINDS, mappings.MODES, (False, True),
                ('USER', 'ROLE')):
            def variant():
                value = {
                    'name': 'CDE_MAPPING', 'using_mode': mode,
                    'plugin': 'Srp256' if mode == 'PLUGIN' else '',
                    'source_database': '', 'from_type': 'USER',
                    'from_any': any_name, 'from_name': "QA O'Connor é",
                    'to_type': to_type, 'to_name': '',
                }
                apply(kind, 'create', value)
                connection.rollback()
                assert observe(kind) is None
                apply(kind, 'create', value)
                connection.commit()
                row = observe(kind)
                expected = mappings.metadata(kind, row)
                assert expected['mapping_draft']['using_mode'] == mode
                assert expected['mapping_draft']['from_any'] is any_name
                assert expected['mapping_draft']['to_type'] == to_type
                assert expected['mapping_draft']['from_name'] == (
                    '*' if any_name else "QA O'Connor é")
                # IN is a quoted identifier in native grammar, not a literal.
                if mode != 'SERVERWIDE':
                    value['source_database'] = '/srv/Case sensitive "db".fdb'
                value['to_name'] = 'Mapped Identity é'
                apply(kind, 'alter', value)
                connection.rollback()
                assert observe(kind) == row
                apply(kind, 'alter', value)
                connection.commit()
                changed = observe(kind)
                assert changed[7] == value['to_name'], repr(changed[7])
                assert changed[3] == (value['source_database'] or None)
                comment = ("  QA 'ASCII'\ncomment  " if
                           kind == mappings.KINDS[1] else
                           "  QA 'é'\ncomment  ")
                apply(kind, 'comment', {'description': comment})
                connection.commit()
                row = observe(kind)
                assert row[8] == comment, repr(row[8])
                inspected = next(item for item in _resources(
                    connection, {'route': route}) if
                    item['resource_kind'] == kind and
                    item['display_name'] == 'CDE_MAPPING')
                connection.commit()
                assert inspected['native']['ddl'] == (
                    mappings.metadata(kind, row)['ddl'])
                apply(kind, 'comment', {'description': ''})
                connection.commit()
                assert observe(kind)[8] is None
                apply(kind, 'drop', {'confirmation': 'CDE_MAPPING'})
                connection.rollback()
                assert observe(kind) is not None
                apply(kind, 'drop', {'confirmation': 'CDE_MAPPING'})
                connection.commit()
                assert observe(kind) is None
                apply(kind, 'create_or_alter', value)
                connection.commit()
                apply(kind, 'create_or_alter', value)
                connection.commit()
                assert observe(kind)[7] == value['to_name']
            case(f'{kind}:{mode}:any={any_name}:to={to_type}', variant)

        # Identical names in different scopes must remain distinct objects.
        def scopes():
            for kind in mappings.KINDS:
                apply(kind, 'create', {
                    'name': 'CDE_MAPPING', 'using_mode': 'ANY_PLUGIN',
                    'from_type': 'USER', 'from_name': 'QA_SCOPES'})
                connection.commit()
            resources = _resources(connection, {'route': route})
            connection.commit()
            ids = {item['resource_id'] for item in resources if
                   item['resource_kind'] in mappings.KINDS and
                   item['display_name'] == 'CDE_MAPPING'}
            assert len(ids) == 2
            apply(mappings.KINDS[0], 'drop', {'confirmation': 'CDE_MAPPING'})
            connection.commit()
            assert observe(mappings.KINDS[0]) is None
            assert observe(mappings.KINDS[1]) is not None
        case('scope-separation', scopes)

        user_password = secrets.token_urlsafe(24)
        apply('user', 'create', {'name': 'CDE_MAP_USER',
                                 'password': user_password})
        connection.commit()
        for kind in mappings.KINDS:
            def permissions():
                value = {'name': 'CDE_MAPPING', 'using_mode': 'ANY_PLUGIN',
                         'from_type': 'USER', 'from_name': 'unmatched'}
                apply(kind, 'create', value)
                connection.commit()
                before = observe(kind)
                other = driver.connect(password=user_password,
                                       **_route_arguments({
                                           **route, 'user': 'CDE_MAP_USER'},
                                           driver))
                try:
                    for operation in ('create', 'alter', 'create_or_alter',
                                      'comment', 'drop'):
                        try:
                            apply(kind, operation, {
                                **value, 'confirmation': 'CDE_MAPPING',
                                'description': 'denied'}, conn=other)
                        except RelationalClientError:
                            assert observe(kind) == before
                        else:
                            raise AssertionError(
                                'Unprivileged mutation allowed')
                finally:
                    other.close()
            case(kind + ':permission-denial', permissions)

            def authentication():
                apply(kind, 'create', {
                    'name': 'CDE_MAPPING', 'using_mode': 'ANY_PLUGIN',
                    'from_type': 'USER', 'from_name': 'CDE_MAP_USER',
                    'to_type': 'USER', 'to_name': 'CDE_MAPPED_IDENTITY'})
                connection.commit()
                other = driver.connect(password=user_password,
                                       **_route_arguments({
                                           **route, 'user': 'CDE_MAP_USER'},
                                           driver))
                try:
                    with other.cursor() as cursor:
                        cursor.execute('SELECT CURRENT_USER FROM RDB$DATABASE')
                        assert cursor.fetchone()[0].strip() == (
                            'CDE_MAPPED_IDENTITY')
                finally:
                    other.close()
            case(kind + ':native-user-mapping-authentication', authentication)

        def global_comment_projection():
            kind = mappings.KINDS[1]
            apply(kind, 'create', {
                'name': 'CDE_MAPPING', 'using_mode': 'ANY_PLUGIN',
                'from_type': 'USER', 'from_name': 'CDE_UNMATCHED_FIXTURE'})
            connection.commit()
            text = 'A' * 40000
            with connection.cursor() as cursor:
                cursor.execute('COMMENT ON GLOBAL MAPPING "CDE_MAPPING" IS '
                               + mappings.literal(text))
            connection.commit()
            row = observe(kind)
            result['global_comment_projection'] = {
                'requested_length': len(text), 'observed_length': len(row[8]),
                'exact': row[8] == text}
            assert row[8] == text[:32767]
            metadata = mappings.metadata(kind, row)
            assert 'ddl' not in metadata
            assert metadata['description_completeness'] == (
                'unverified-at-native-projection-limit')
            try:
                apply(kind, 'comment', {'description': text})
            except RelationalClientError:
                assert observe(kind) == row
            else:
                raise AssertionError('Unverifiable comment was accepted')

        case('global-comment-native-projection-limit',
             global_comment_projection)
        if options is not None and (options.browser or
                                    options.browser_mutations):
            for kind in mappings.KINDS:
                apply(kind, 'create', {
                    'name': 'CDE_MAPPING', 'using_mode': 'PLUGIN',
                    'plugin': 'Srp256', 'from_type': 'USER',
                    'from_name': 'CDE_UNMATCHED_FIXTURE'})
                connection.commit()
            with tempfile.TemporaryDirectory(
                    prefix='mapping-profile-',
                    dir=options.output.parent) as runtime:
                profiles = Path(runtime) / 'profiles.json'
                profiles.write_text(json.dumps({
                    'host': '127.0.0.1',
                    'fixture_container': name,
                    'profiles': [{**route, 'engine': 'firebird',
                                  'password': password}]}))
                profiles.chmod(0o600)
                runs = ([('object', 'default', 100),
                         ('object', 'high-contrast', 200)]
                        if options.browser else [])
                if options.browser_mutations:
                    runs.append(('mappings', 'default', 100))
                for gate, theme, scale in runs:
                    label = f'browser-{scale}' if gate == 'object' else gate
                    root = options.output.parent / label
                    command = [
                        sys.executable,
                        'tools/cdeadmin_firebird_ui_orchestrator.py',
                        '--source-config-db', str(options.source_config_db),
                        '--desktop-user', options.desktop_user,
                        '--database', route['database'], '--firebird-port',
                        str(port), '--client-library', os.environ[
                            'CDEADMIN_FIREBIRD_CLIENT_LIBRARY'],
                        '--profiles', str(profiles), '--gate-kind', gate,
                        '--resource-kind', mappings.KINDS[0],
                        '--resource-kind', mappings.KINDS[1],
                        '--theme', theme, '--font-scale', str(scale),
                        '--timeout', '30']
                    for key, filename in (
                            ('evidence-root', 'screenshots'),
                            ('summary-output', 'summary.json'),
                            ('manifest-output', 'manifest.json'),
                            ('output', 'result.json'),
                            ('server-log', 'server.log'),
                            ('browser-log', 'browser.log')):
                        command.extend(['--' + key, str(root / filename)])
                    environment['CDEADMIN_FIREBIRD_DEMO_PASSWORD'] = password
                    code = subprocess.call(command, env=environment)
                    if code:
                        result['failures'].append({
                            'case': label, 'exit_code': code})
                    else:
                        result['checks'].append(label)
        result['status'] = 'passed' if not result['failures'] else 'failed'
    except Exception as error:
        result['failures'].append({'case': 'infrastructure',
                                   'type': type(error).__name__,
                                   'traceback': traceback.format_exc()})
    finally:
        if connection is not None:
            connection.close()
        if created:
            removed = subprocess.run(['docker', 'rm', '-f', '-v', name],
                                     capture_output=True, text=True)
            result['container_removed'] = removed.returncode == 0
        if not result['container_removed']:
            result['status'] = 'failed'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--browser-mutations', action='store_true')
    parser.add_argument('--source-config-db', type=Path)
    parser.add_argument('--desktop-user')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if (args.browser or args.browser_mutations) and not (
            args.source_config_db and args.desktop_user):
        parser.error('--browser needs --source-config-db and --desktop-user')
    result = run(args)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
