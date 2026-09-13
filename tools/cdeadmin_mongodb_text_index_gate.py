#!/usr/bin/env python3
"""Qualify visual text-index settings using a disposable native MongoDB."""

import argparse
import json
from pathlib import Path

from cdeadmin_mongodb_index_editor_gate import run
from cdeadmin_mongodb_index_options_gate import verify as verify_options


def verify(database, adapter, result):
    import pymongo
    verify_options(database, adapter, result)
    for version in ('2', '3'):
        collection = database['text_v' + version]
        native = {'database': database.name, 'collection': collection.name}
        request = {'resource_kind': 'index', 'operation_id': 'create',
                   'target_resource': {'native': native}, 'draft': {
                       'name': 'weighted_search', 'keys': [
                           {'field': 'title', 'kind': 'text'},
                           {'field': 'body', 'kind': 'text'}],
                       'configure_text': True, 'text_weights': [
                           {'field': 'title', 'weight': 10},
                           {'field': 'body', 'weight': 1}],
                       'text_language': 'none',
                       'text_language_override': 'content_language',
                       'text_version': version}}
        assert not adapter.validate_admin_operation(request)['errors']
        payload = json.loads(json.dumps(adapter.plan_admin_operation(
            request)['provider_payload']))
        adapter._apply_index(database, 'create', payload['draft'], native)
        info = collection.index_information()['weighted_search']
        assert info['weights'] == {'title': 10, 'body': 1}, info
        assert info['textIndexVersion'] == int(version), info
        assert info['default_language'] == 'none', info
        assert info['language_override'] == 'content_language', info
        result['checks'].append('text-v' + version + '-native-settings')
        collection.insert_many([
            {'_id': 'title', 'title': 'robin'},
            {'_id': 'body', 'body': 'robin'},
            {'_id': 'stopword', 'body': 'the'},
        ])
        matches = list(collection.find({'$text': {'$search': 'robin'}},
                                       {'score': {'$meta': 'textScore'}}))
        scores = {item['_id']: item['score'] for item in matches}
        assert scores['title'] > scores['body'], scores
        result['checks'].append('text-v' + version + '-weighted-search')
        matches = list(collection.find({'$text': {'$search': 'the'}}))
        assert [item['_id'] for item in matches] == ['stopword'], matches
        result['checks'].append('text-v' + version + '-none-keeps-stopwords')
        try:
            collection.insert_one({'body': 'robin',
                                   'content_language': 'not_a_language'})
        except pymongo.errors.WriteError:
            result['checks'].append('text-v' + version + '-override-enforced')
        else:
            raise AssertionError('Invalid document language was accepted')
        adapter._apply_index(database, 'drop', {}, {
            **native, 'index_name': 'weighted_search'})
        assert 'weighted_search' not in collection.index_information()
        result['checks'].append('text-v' + version + '-drop')


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
