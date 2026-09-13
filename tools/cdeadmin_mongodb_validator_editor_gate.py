#!/usr/bin/env python3
"""Qualify validator controls on the disposable MongoDB index-gate fixture."""

import argparse
import json
from pathlib import Path

from cdeadmin_mongodb_index_editor_gate import run


def verify(database, adapter, result):
    import pymongo
    collection = database['validation_qa']
    collection.insert_one({'_id': 'old-invalid', 'score': -1})
    native = {'database': database.name, 'collection': collection.name}
    rule = {'score': {'$gte': 0}}

    def apply(operation, draft):
        request = {'resource_kind': 'validator', 'operation_id': operation,
                   'target_resource': {'native': native}, 'draft': draft}
        if operation != 'drop':
            assert not adapter.validate_admin_operation(request)['errors']
        payload = adapter.plan_admin_operation(request)['provider_payload']
        adapter._apply_validator(database, operation, payload['draft'], native)
        return collection.options()

    def rejected(document):
        try:
            collection.insert_one(document)
        except pymongo.errors.WriteError as error:
            assert error.code == 121, error.code
        else:
            raise AssertionError('Invalid document was accepted')

    options = apply('create', {'replace_rule': True, 'validator': rule,
                               'validation_level': 'strict',
                               'validation_action': 'error'})
    assert options['validator'] == rule
    assert options['validationLevel'] == 'strict'
    host, port = database.client.address
    resources = adapter.list_resources({'route': {
        'host': host, 'port': port, 'database': database.name}})
    validator = next(item['native'] for item in resources
                     if item['resource_kind'] == 'validator' and
                     item['native'].get('collection') == collection.name)
    form = adapter._admin_form('validator', 'alter')
    expected = {'validator': adapter._extended_json(rule),
                'validation_level': 'strict',
                'validation_action': 'error'}
    for field in form['fields']:
        if field['field_id'] not in expected:
            continue
        observed = validator
        for key in field['initial_value_path']:
            observed = observed[key]
        assert observed == expected[field['field_id']]
    result['checks'].append('validator-native-prefill-paths')
    index = next(item['native'] for item in resources
                 if item['resource_kind'] == 'index' and
                 item['native'].get('index_name') == 'qa_ttl')
    index_form = adapter._admin_form('index', 'alter')
    ttl = next(field for field in index_form['fields']
               if field['field_id'] == 'ttl_seconds')
    observed = index
    for key in ttl['initial_value_path']:
        observed = observed[key]
    assert observed == 0
    result['checks'].append('index-native-zero-ttl-prefill-path')
    rejected({'score': -1})
    collection.insert_one({'score': 1})
    result['checks'].append('validator-strict-error')
    options = apply('alter', {'validation_action': 'warn'})
    assert options['validator'] == rule
    collection.insert_one({'score': -2})
    result['checks'].append('validator-warn-preserves-rule')
    options = apply('alter', {'validation_action': 'errorAndLog'})
    assert options['validationAction'] == 'errorAndLog'
    rejected({'score': -3})
    result['checks'].append('validator-error-and-log')
    apply('alter', {'validation_level': 'moderate',
                    'validation_action': 'error'})
    collection.update_one({'_id': 'old-invalid'},
                          {'$set': {'note': 'allowed'}})
    rejected({'score': -4})
    result['checks'].append('validator-moderate-old-invalid-update')
    options = apply('alter', {'validation_level': 'off'})
    assert options['validator'] == rule
    collection.insert_one({'score': -5})
    result['checks'].append('validator-off-preserves-rule')
    options = apply('alter', {'replace_rule': True, 'validator': {},
                              'validation_level': 'strict'})
    assert not options.get('validator')
    collection.insert_one({'score': -6})
    result['checks'].append('validator-explicit-clear')
    apply('alter', {'changes': {'validator': rule,
                                'validationLevel': 'strict',
                                'validationAction': 'error'}})
    rejected({'score': -7})
    result['checks'].append('validator-legacy-wrapper-settings')
    options = apply('drop', {})
    assert not options.get('validator')
    assert options['validationLevel'] == 'strict'
    collection.insert_one({'score': -8})
    result['checks'].append('validator-drop-preserves-level')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongod', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    args = parser.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=True)
    result = run(args.mongod.resolve(), args.workspace.resolve(), verify)
    output = args.workspace / 'live.json'
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
