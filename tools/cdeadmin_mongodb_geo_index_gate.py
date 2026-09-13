#!/usr/bin/env python3
"""Qualify visual geospatial index options on disposable native MongoDB."""

import argparse
import json
from pathlib import Path

from cdeadmin_mongodb_index_editor_gate import run
from cdeadmin_mongodb_wildcard_gate import verify as verify_wildcard


def verify(database, adapter, result):
    import pymongo
    verify_wildcard(database, adapter, result)
    cases = [('2d', str(bits), {
        'geo_bits': bits, 'geo_min': -100.5, 'geo_max': 100.5})
             for bits in (1, 26, 32)]
    cases += [('2dsphere', v, {'geo_version': v}) for v in ('1', '2', '3')]
    for kind, label, settings in cases:
        collection = database['geo_' + kind + '_' + label]
        native = {'database': database.name, 'collection': collection.name}
        request = {'resource_kind': 'index', 'operation_id': 'create',
                   'target_resource': {'native': native}, 'draft': {
                       'name': 'spatial', 'keys': [
                           {'field': 'point', 'kind': kind}],
                       'geo_mode': kind, **settings}}
        assert not adapter.validate_admin_operation(request)['errors']
        payload = json.loads(json.dumps(adapter.plan_admin_operation(
            request)['provider_payload']))
        adapter._apply_index(database, 'create', payload['draft'], native)
        info = collection.index_information()['spatial']
        if kind == '2d':
            assert info['bits'] == int(label)
            assert info['min'] == -100.5 and info['max'] == 100.5
            points = [[0, 0], [50, 50]]
            query = {'point': {'$geoWithin': {'$box': [[-1, -1], [1, 1]]}}}
        else:
            assert info['2dsphereIndexVersion'] == int(label)
            points = [{'type': 'Point', 'coordinates': coordinates}
                      for coordinates in ([0, 0], [50, 50])]
            query = {'point': {'$geoWithin': {
                '$centerSphere': [[0, 0], 0.01]}}}
        result['checks'].append('geo-' + kind + '-' + label + '-metadata')
        collection.insert_many([{'_id': i, 'point': point}
                                for i, point in enumerate(points)])
        matches = list(collection.find(query).hint('spatial'))
        assert [item['_id'] for item in matches] == [0], matches
        result['checks'].append('geo-' + kind + '-' + label + '-spatial-query')
        if kind == '2d':
            try:
                collection.insert_one({'point': [101, 101]})
            except pymongo.errors.WriteError:
                result['checks'].append('geo-2d-' + label + '-bounds-enforced')
            else:
                raise AssertionError('Out-of-range planar point was accepted')
        adapter._apply_index(database, 'drop', {}, {
            **native, 'index_name': 'spatial'})
        assert 'spatial' not in collection.index_information()
        result['checks'].append('geo-' + kind + '-' + label + '-drop')


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
