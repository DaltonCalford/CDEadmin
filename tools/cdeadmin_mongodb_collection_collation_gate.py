#!/usr/bin/env python3
"""Verify collection/view collation controls on a disposable MongoDB."""

import argparse
import json
from pathlib import Path

from cdeadmin_mongodb_index_editor_gate import run
from cdeadmin_mongodb_geo_index_gate import verify as verify_indexes


def verify(database, adapter, result):
    import pymongo
    verify_indexes(database, adapter, result)
    native = {'database': database.name}

    def create(kind, name, fields):
        fields = {**fields, 'options': {
            'database': database.name, **fields.get('options', {})}}
        request = {'resource_kind': kind, 'operation_id': 'create',
                   'target_resource': {'native': native},
                   'draft': {'name': name, **fields}}
        validation = adapter.validate_admin_operation(request)
        assert not validation['errors'], validation
        payload = json.loads(json.dumps(adapter.plan_admin_operation(
            request)['provider_payload']))
        adapter._apply_collection(database, kind, 'create', payload['draft'],
                                  None, native)
        return database[name]

    source = create('collection', 'collated_source', {
        'collation_mode': 'locale', 'collation_locale': 'en',
        'collation_strength': '2'})
    assert source.options()['collation']['strength'] == 2
    result['checks'].append('collection-collation-metadata')
    source.insert_one({'value': 'Robin'})
    assert source.count_documents({'value': 'robin'}) == 1
    result['checks'].append('collection-default-query-collation')
    source.create_index('value', unique=True)
    try:
        source.insert_one({'value': 'robin'})
    except pymongo.errors.DuplicateKeyError:
        result['checks'].append('collection-index-inherits-collation')
    else:
        raise AssertionError('Inherited unique collation was not enforced')
    for mode in ('native', 'simple', 'locale'):
        name = 'collated_view_' + mode
        view = create('view', name, {
            'options': {'view_on': source.name, 'pipeline': []},
            'collation_mode': mode, 'collation_locale': 'en',
            'collation_strength': '2'})
        expected = 1 if mode == 'locale' else 0
        assert view.count_documents({'value': 'robin'}) == expected
        result['checks'].append('view-' + mode + '-query-collation')
        if mode == 'locale':
            try:
                view.count_documents({}, collation={'locale': 'simple'})
            except pymongo.errors.OperationFailure:
                result['checks'].append('view-rejects-collation-override')
            else:
                raise AssertionError('View collation override was accepted')
        adapter._apply_collection(database, 'view', 'drop', {}, name, native)
        assert name not in database.list_collection_names()
        result['checks'].append('view-' + mode + '-drop')
    adapter._apply_collection(database, 'collection', 'drop', {},
                              source.name, native)
    assert source.name not in database.list_collection_names()
    result['checks'].append('collated-collection-drop')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongod', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    args = parser.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=True)
    result = run(args.mongod.resolve(), args.workspace.resolve(), verify)
    (args.workspace / 'live.json').write_text(
        json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
