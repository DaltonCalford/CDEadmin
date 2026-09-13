#!/usr/bin/env python3
"""Verify visual wildcard projections against disposable native MongoDB."""

import argparse
import json
from pathlib import Path

from cdeadmin_mongodb_index_editor_gate import run
from cdeadmin_mongodb_collation_gate import verify as verify_collation


def verify(database, adapter, result):
    import pymongo
    verify_collation(database, adapter, result)
    cases = [
        ('include', {'public': 1}, 'public', 'private'),
        ('exclude', {'private': 0}, 'public', 'private'),
        ('include_without_id', {'public': 1, '_id': 0}, 'public', 'private'),
        ('exclude_with_id', {'private': 0, '_id': 1}, '_id', 'private'),
        ('nested', {'nested.value': 1}, 'nested.value', 'private'),
    ]
    for label, projection, allowed, excluded in cases:
        collection = database['projection_' + label]
        native = {'database': database.name, 'collection': collection.name}
        request = {'resource_kind': 'index', 'operation_id': 'create',
                   'target_resource': {'native': native}, 'draft': {
                       'name': 'projected', 'keys': [
                           {'field': '$**', 'kind': 'ascending'}],
                       'configure_wildcard': True, 'wildcard_fields': [
                           {'field': key, 'action': 'include' if value else
                            'exclude'} for key, value in projection.items()]}}
        assert not adapter.validate_admin_operation(request)['errors']
        payload = json.loads(json.dumps(adapter.plan_admin_operation(
            request)['provider_payload']))
        adapter._apply_index(database, 'create', payload['draft'], native)
        info = collection.index_information()['projected']
        assert info['wildcardProjection'] == projection, info
        result['checks'].append('wildcard-' + label + '-metadata')
        collection.insert_one({'_id': 1, 'public': 1, 'private': 1,
                               'nested': {'value': 1}})
        matches = list(collection.find({allowed: 1}).hint('projected'))
        assert len(matches) == 1 and matches[0]['_id'] == 1, matches
        result['checks'].append('wildcard-' + label + '-included-query')
        try:
            list(collection.find({excluded: 1}).hint('projected'))
        except pymongo.errors.OperationFailure:
            result['checks'].append('wildcard-' + label + '-excluded-query')
        else:
            raise AssertionError('Excluded field was available via index hint')
        adapter._apply_index(database, 'drop', {}, {
            **native, 'index_name': 'projected'})
        assert 'projected' not in collection.index_information()
        result['checks'].append('wildcard-' + label + '-drop')


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
