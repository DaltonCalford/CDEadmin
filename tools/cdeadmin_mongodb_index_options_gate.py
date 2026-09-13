#!/usr/bin/env python3
"""Verify native index option controls on a disposable MongoDB fixture."""

import argparse
import json
from pathlib import Path

from cdeadmin_mongodb_index_editor_gate import run


def verify(database, adapter, result):
    import pymongo

    def create(collection, draft):
        native = {'database': database.name, 'collection': collection}
        request = {'resource_kind': 'index', 'operation_id': 'create',
                   'target_resource': {'native': native}, 'draft': draft}
        assert not adapter.validate_admin_operation(request)['errors']
        payload = json.loads(json.dumps(adapter.plan_admin_operation(
            request)['provider_payload']))
        adapter._apply_index(database, 'create', payload['draft'], native)
        return database[collection].index_information()[draft['name']]

    base = {'name': 'visual', 'keys': [
        {'field': 'value', 'kind': 'ascending'}]}
    for field in ('unique', 'sparse', 'hidden'):
        for choice in ('enabled', 'disabled'):
            info = create(field + '_' + choice, {
                **base, field + '_mode': choice})
            assert bool(info.get(field)) == (choice == 'enabled'), info
            result['checks'].append('create-' + field + '-' + choice)
    info = create('defaults', base)
    assert not any(info.get(field) for field in ('unique', 'sparse', 'hidden'))
    result['checks'].append('create-native-defaults')
    create('sparse_unique', {**base, 'unique_mode': 'enabled',
                             'sparse_mode': 'enabled'})
    collection = database['sparse_unique']
    collection.insert_many([{'other': 1}, {'other': 2}, {'value': 3}])
    try:
        collection.insert_one({'value': 3})
    except pymongo.errors.DuplicateKeyError:
        result['checks'].append('sparse-unique-enforces-present-keys')
    else:
        raise AssertionError('Sparse unique index accepted a duplicate')
    collection = database['hidden_enabled']
    collection.insert_one({'value': 1})
    try:
        list(collection.find({'value': 1}).hint('visual'))
    except pymongo.errors.OperationFailure:
        result['checks'].append('hidden-index-unavailable-to-query-hint')
    else:
        raise AssertionError('Hidden index was available to query hint')
    for seconds in (0, 60, 2147483647):
        info = create('expiry_' + str(seconds), {
            **base, 'enable_ttl': True, 'ttl_seconds': seconds})
        assert info['expireAfterSeconds'] == seconds, info
        result['checks'].append('create-ttl-' + str(seconds))
    info = create('no_expiry', {**base, 'enable_ttl': False,
                                'ttl_seconds': 0})
    assert 'expireAfterSeconds' not in info
    result['checks'].append('unselected-ttl-not-created')
    info = create('advanced', {**base, 'unique_mode': 'native',
                               'options': {'unique': True,
                                           'partialFilterExpression': {
                                               'active': True}}})
    assert info['unique'] and info['partialFilterExpression'] == {
        'active': True}
    result['checks'].append('advanced-options-preserved')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongod', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=True)
    result = run(args.mongod.resolve(), args.workspace.resolve(), verify)
    (args.workspace / 'live.json').write_text(
        json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
