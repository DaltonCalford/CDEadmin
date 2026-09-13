#!/usr/bin/env python3
"""Verify view edits and omitted-field preservation on isolated MongoDB."""

import argparse
import json
from pathlib import Path

from cdeadmin_mongodb_index_editor_gate import run
from cdeadmin_mongodb_collection_collation_gate import (
    verify as verify_previous,
)


def verify(database, adapter, result):
    import pymongo
    verify_previous(database, adapter, result)
    database.source_a.insert_many([{'n': 1}, {'n': 2}])
    database.source_b.insert_many([{'n': 2}, {'n': 3}])
    view = database.create_collection(
        'edited_view', viewOn='source_a', pipeline=[{'$match': {'n': 2}}],
        collation={'locale': 'en', 'strength': 2})
    stale = {'database': database.name, 'collection': view.name,
             'options': view.options()}

    def alter(draft):
        request = {'resource_kind': 'view', 'operation_id': 'alter',
                   'target_resource': {'native': stale}, 'draft': draft}
        validation = adapter.validate_admin_operation(request)
        assert not validation['errors'], validation
        payload = json.loads(json.dumps(adapter.plan_admin_operation(
            request)['provider_payload']))
        adapter._apply_admin(database.client, {}, payload)

    alter({'view_source': 'source_b'})
    assert view.options()['viewOn'] == 'source_b'
    assert view.options()['pipeline'] == stale['options']['pipeline']
    assert [row['n'] for row in view.find()] == [2]
    result['checks'].append('view-source-only-preserves-pipeline')
    alter({'configure_pipeline': True, 'view_pipeline': []})
    assert view.options()['viewOn'] == 'source_b'
    assert view.options()['pipeline'] == []
    assert sorted(row['n'] for row in view.find()) == [2, 3]
    result['checks'].append('view-clear-pipeline-preserves-newer-source')
    database.command({'collMod': view.name,
                      'pipeline': [{'$match': {'n': 1}}]})
    alter({'view_source': 'source_a'})
    assert [row['n'] for row in view.find()] == [1]
    result['checks'].append('view-source-edit-preserves-external-pipeline')
    alter({'view_source': 'source_b', 'configure_pipeline': True,
           'view_pipeline': [{'$match': {'n': 3}}]})
    assert [row['n'] for row in view.find()] == [3]
    result['checks'].append('view-source-and-pipeline-edit')
    assert view.options()['collation'] == stale['options']['collation']
    result['checks'].append('view-alter-preserves-immutable-collation')
    before = view.options()
    try:
        alter({'configure_pipeline': True,
               'view_pipeline': [{'$out': 'forbidden'}]})
    except pymongo.errors.OperationFailure:
        assert view.options() == before
        result['checks'].append('view-native-rejection-leaves-definition')
    else:
        raise AssertionError('View accepted an output stage')
    alter({'changes': {'pipeline': []}})
    assert view.count_documents({}) == 2
    result['checks'].append('view-legacy-collmod-input-preserved')
    view.drop()
    database.source_a.drop()
    database.source_b.drop()
    assert view.name not in database.list_collection_names()
    result['checks'].append('edited-view-cleanup')


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
