#!/usr/bin/env python3
"""Verify visual index plans using a disposable loopback MongoDB process."""

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(ROOT / 'web/pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.mongodb.client import (  # noqa: E402
    MongoDBClient,
)


def run(binary, workspace):
    import pymongo
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
    result = {'checks': [], 'status': 'failed'}
    with tempfile.TemporaryDirectory(prefix='mongo-index-',
                                     dir=workspace) as directory:
        process = subprocess.Popen([
            str(binary), '--bind_ip', '127.0.0.1', '--port', str(port),
            '--dbpath', directory, '--logpath', str(workspace / 'mongod.log'),
            '--wiredTigerCacheSizeGB', '0.25',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        connection = pymongo.MongoClient('127.0.0.1', port,
                                         serverSelectionTimeoutMS=500)
        adapter = MongoDBClient()
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Disposable mongod exited early')
                try:
                    connection.admin.command('ping')
                    break
                except pymongo.errors.ConnectionFailure:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            result['engine_version'] = connection.server_info()['version']
            database = connection['cdeadmin_index_qa']
            native = {'database': database.name, 'collection': 'items'}
            cases = [(kind, [{'field': 'value', 'kind': kind}]) for kind in (
                'ascending', 'descending', 'text', 'hashed', '2d', '2dsphere')]
            cases += [('wildcard', [{'field': '$**', 'kind': 'ascending'}]),
                      ('compound', [{'field': 'a', 'kind': 'descending'},
                                    {'field': 'b', 'kind': 'ascending'}])]
            for label, keys in cases:
                name = 'qa_' + label
                request = {'resource_kind': 'index', 'operation_id': 'create',
                           'target_resource': {'native': native},
                           'draft': {'name': name, 'keys': keys,
                                     'options': {}}}
                validation = adapter.validate_admin_operation(request)
                assert not validation.get('errors'), validation
                plan = adapter.plan_admin_operation(request)
                payload = plan['provider_payload']
                adapter._apply_index(database, 'create', payload['draft'],
                                     native)
                info = database['items'].index_information()[name]
                expected = payload['draft']['options']['keys']
                if label == 'text':
                    assert info['weights'] == {'value': 1}
                else:
                    assert info['key'] == expected, (info['key'], expected)
                result['checks'].append(label + '-create-and-inspect')
                adapter._apply_index(database, 'drop', {}, {
                    **native, 'index_name': name})
                assert name not in database['items'].index_information()
                result['checks'].append(label + '-drop')
            adapter._apply_index(database, 'create', {
                'name': 'qa_unique', 'keys': [
                    {'field': 'id', 'kind': 'ascending'}],
                'options': {'unique': True}}, native)
            database['items'].insert_one({'id': 1})
            try:
                database['items'].insert_one({'id': 1})
            except pymongo.errors.DuplicateKeyError:
                result['checks'].append('unique-option-enforced')
            else:
                raise AssertionError('Unique index accepted duplicate values')
            result['status'] = 'passed'
        finally:
            adapter.close()
            connection.close()
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            result['process_stopped'] = process.poll() is not None
    result['temporary_data_removed'] = not Path(directory).exists()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongod', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=True)
    result = run(args.mongod.resolve(), args.workspace.resolve())
    output = args.workspace / 'live.json'
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
