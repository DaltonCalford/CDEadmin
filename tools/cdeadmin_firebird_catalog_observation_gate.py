#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Read-only native failure injection for named catalog observations."""

import argparse
import json
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cdeadmin_firebird_admin_mapping_gate import (
    _create_client, _route_arguments, _resources,
)
from pgadmin.cdeadmin.providers.firebird.catalog_reader import CatalogReader
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def run(profiles):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    _create_client(SimpleNamespace(acquire_secret=None))
    password = route.pop('password')
    native = driver.connect(password=password,
                            **_route_arguments(route, driver))
    result = {'complete': False, 'cases': [], 'failures': [],
              'read_only': True, 'objects_created': 0,
              'connection_closed': False}
    missing = 'CDE_MISSING_' + uuid.uuid4().hex.upper()

    def database(resources):
        return next(item['native'] for item in resources
                    if item['resource_kind'] == 'database')

    try:
        with native.cursor() as cursor:
            cursor.execute(
                "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
                'FROM RDB$DATABASE')
            result['engine_version'] = cursor.fetchone()[0]
            assert result['engine_version'] == '5.0.4'
            cursor.execute('SELECT COUNT(*) FROM RDB$RELATIONS '
                           'WHERE RDB$RELATION_NAME = ?', (missing,))
            assert cursor.fetchone()[0] == 0
        native.rollback()
        baseline = _resources(native, {})
        observations = database(baseline)['catalog_observations']
        assert all(item['available'] for item in observations)
        result['baseline_resource_count'] = len(baseline)
        result['baseline_observations'] = observations
        native.rollback()
        original = CatalogReader.rows
        for observation in observations:
            section = observation['section']
            injected = []

            def failing_read(reader, source, current_section, *,
                             required=True):
                if current_section == section:
                    injected.append(section)
                    source = 'SELECT * FROM ' + missing
                return original(reader, source, current_section,
                                required=required)

            try:
                with patch.object(CatalogReader, 'rows', failing_read):
                    if observation['required_for_structural_catalog']:
                        try:
                            _resources(native, {})
                        except RelationalClientError as error:
                            assert section in str(error)
                            assert 'Firebird status codes:' in str(error)
                            assert missing not in str(error)
                        else:
                            raise AssertionError('Incomplete catalog returned')
                        state = 'structural-catalog-blocked'
                    else:
                        resources = _resources(native, {})
                        metadata = database(resources)
                        failed = next(
                            item for item in metadata['catalog_observations']
                            if item['section'] == section)
                        assert failed['available'] is False
                        assert failed['row_count'] is None
                        assert failed['native_status_codes']
                        assert any(section in warning for warning in
                                   metadata['catalog_warnings'])
                        state = 'optional-observation-explicitly-unavailable'
                assert injected == [section]
                result['cases'].append({'section': section, 'state': state})
            except Exception:
                result['failures'].append({'section': section,
                                          'traceback': traceback.format_exc()})
            finally:
                if native.main_transaction.is_active():
                    native.rollback()
        recovered = _resources(native, {})
        assert {item['resource_id'] for item in recovered} == {
            item['resource_id'] for item in baseline}
        assert all(item['available'] for item in database(recovered)[
            'catalog_observations'])
        result['subsequent_read_recovered'] = True
        native.rollback()
        for field in database(baseline)['information_observations']:
            class Information:
                def __getattr__(self, name):
                    if name == field:
                        raise RuntimeError('private driver details')
                    return getattr(native.info, name)

            class Connection:
                info = Information()

                def __getattr__(self, name):
                    return getattr(native, name)

            try:
                resources = _resources(Connection(), {})
                metadata = database(resources)
                assert metadata['information_observations'][field] == {
                    'available': False, 'error_type': 'RuntimeError'}
                assert any(field in warning for warning in
                           metadata['catalog_warnings'])
                assert 'private driver details' not in repr(resources)
                if field == 'engine_version':
                    server = next(item for item in resources
                                  if item['resource_kind'] == 'server')
                    assert server['native']['engine_version'] is None
                result['cases'].append({'information_field': field,
                                        'unavailable_explicit': True})
            except Exception:
                result['failures'].append({'information_field': field,
                                          'traceback': traceback.format_exc()})
            finally:
                if native.main_transaction.is_active():
                    native.rollback()
    except Exception:
        result['failures'].append({'traceback': traceback.format_exc()})
    finally:
        if native.main_transaction.is_active():
            native.rollback()
        native.close()
        result['connection_closed'] = True
    result['complete'] = (not result['failures'] and
                          result.get('subsequent_read_recovered') is True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.profiles)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'cases': len(result['cases']),
                      'failures': result['failures']}, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
