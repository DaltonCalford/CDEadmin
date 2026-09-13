#!/usr/bin/env python3
"""Verify native index collation options on a disposable MongoDB fixture."""

import argparse
import json
from pathlib import Path

from cdeadmin_mongodb_index_editor_gate import run
from cdeadmin_mongodb_text_index_gate import verify as verify_text


def verify(database, adapter, result):
    import pymongo
    verify_text(database, adapter, result)

    def create(name, fields):
        collection = database[name]
        native = {'database': database.name, 'collection': name}
        request = {'resource_kind': 'index', 'operation_id': 'create',
                   'target_resource': {'native': native}, 'draft': {
                       'name': 'collated', 'keys': [
                           {'field': 'value', 'kind': 'ascending'}], **fields}}
        assert not adapter.validate_admin_operation(request)['errors']
        payload = json.loads(json.dumps(adapter.plan_admin_operation(
            request)['provider_payload']))
        adapter._apply_index(database, 'create', payload['draft'], native)
        return collection, collection.index_information()['collated']

    base = {'collation_mode': 'locale', 'collation_locale': 'en'}
    cases = [('strength', str(value), value) for value in range(1, 6)]
    for key, values in (('caseFirst', ('upper', 'lower', 'off')),
                        ('alternate', ('shifted', 'non-ignorable')),
                        ('maxVariable', ('space', 'punct'))):
        cases += [(key, value, value) for value in values]
    for key in ('caseLevel', 'numericOrdering', 'normalization', 'backwards'):
        cases += [(key, 'enabled', True), (key, 'disabled', False)]
    for index, (key, choice, expected) in enumerate(cases):
        _, info = create('collation_option_' + str(index), {
            **base, 'collation_' + key: choice})
        assert info['collation'][key] == expected, info
        result['checks'].append('collation-' + key + '-' + choice)
    collection, info = create('case_insensitive', {
        **base, 'collation_strength': '2', 'unique_mode': 'enabled'})
    collection.insert_one({'value': 'Robin'})
    try:
        collection.insert_one({'value': 'robin'})
    except pymongo.errors.DuplicateKeyError:
        result['checks'].append('collation-case-insensitive-uniqueness')
    else:
        raise AssertionError(
            'Collated unique index accepted equivalent values')
    assert collection.count_documents({'value': 'ROBIN'},
                                      collation={'locale': 'en',
                                                 'strength': 2}) == 1
    result['checks'].append('collation-case-insensitive-query')
    collection, _ = create('binary', {
        'collation_mode': 'simple', 'unique_mode': 'enabled'})
    collection.insert_many([{'value': 'Robin'}, {'value': 'robin'}])
    assert collection.count_documents({'value': 'Robin'}) == 1
    result['checks'].append('collation-binary-distinct-values')
    collection, _ = create('numeric', {
        **base, 'collation_numericOrdering': 'enabled'})
    collection.insert_many([{'value': '10'}, {'value': '2'}])
    values = [item['value'] for item in collection.find().collation({
        'locale': 'en', 'numericOrdering': True}).sort('value', 1)]
    assert values == ['2', '10'], values
    result['checks'].append('collation-numeric-sort')
    version = info['collation']['version']
    _, pinned = create('version_pinned', {
        **base, 'collation_version': version})
    assert pinned['collation']['version'] == version
    result['checks'].append('collation-server-version-pin')
    for name, fields in (
            ('invalid_version', {'collation_version': '0.0'}),
            ('invalid_combination', {'collation_strength': '1',
                                     'collation_backwards': 'enabled'})):
        try:
            create(name, {**base, **fields})
        except pymongo.errors.OperationFailure:
            result['checks'].append('collation-native-rejects-' + name)
        else:
            raise AssertionError('Invalid native collation was accepted')


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
