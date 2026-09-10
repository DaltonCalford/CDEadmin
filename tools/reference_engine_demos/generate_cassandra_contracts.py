#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate exact Apache Cassandra 5.0.8 dialect and metrics contracts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from types import ModuleType

import cassandra
from cassandra.auth import PlainTextAuthProvider
from cassandra.cluster import Cluster
from cassandra.policies import DCAwareRoundRobinPolicy


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
for entry in (ROOT, WEB):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.cql_native import (  # noqa: E402
    CassandraClient,
    _RowIdentity,
)


REFERENCE_VERSION = '5.0.8'
ANTLR_FILES = (
    Path('src/antlr/Cql.g'),
    Path('src/antlr/Parser.g'),
    Path('src/antlr/Lexer.g'),
)
KEYWORDS = Path('src/resources/org/apache/cassandra/cql3/'
                'reserved_keywords.txt')
TYPES = Path('src/java/org/apache/cassandra/cql3/CQL3Type.java')
DIAGNOSTICS = Path('src/java/org/apache/cassandra/exceptions/'
                   'ExceptionCode.java')
FUNCTIONS = Path('src/java/org/apache/cassandra/cql3/functions')
METRICS_DOC = Path('doc/modules/cassandra/pages/managing/operating/'
                   'metrics.adoc')
VIRTUAL_SCHEMA_TABLES = (
    'SELECT keyspace_name, table_name, comment '
    'FROM system_virtual_schema.tables WHERE keyspace_name = %s'
)
VIRTUAL_SCHEMA_COLUMNS = (
    'SELECT keyspace_name, table_name, column_name, kind, position, type '
    'FROM system_virtual_schema.columns WHERE keyspace_name = %s'
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slug(value):
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def _line(text, offset):
    return text.count('\n', 0, offset) + 1


def _record(item_id, native_name, source, **details):
    return {
        'item_id': item_id,
        'native_name': str(native_name),
        'source': source,
        **details,
    }


def _evidence(evidence_id, evidence_kind, authority, artifact, digest,
              format_name, license_id='Apache-2.0'):
    return {
        'evidence_id': evidence_id,
        'evidence_kind': evidence_kind,
        'authority': authority,
        'artifact': artifact,
        'sha256': digest,
        'format': format_name,
        'license_id': license_id,
    }


def _grammar_rules(path, text):
    pattern = re.compile(
        r'(?m)^([a-z][A-Za-z0-9_]*)'
        r'(?:\s+returns\s*\[[^\]]+\])?\s*(?:@\w+\s*\{[^}]*\}\s*)*:'
    )
    return [
        _record(
            '', match.group(1),
            f'{path.as_posix()}:{_line(text, match.start())}',
            inventory_class='antlr_parser_rule',
        )
        for match in pattern.finditer(text)
    ]


def _source_inventory(source_root):
    commands = []
    grammar_texts = {}
    for relative in ANTLR_FILES:
        text = (source_root / relative).read_text(encoding='utf-8')
        grammar_texts[relative] = text
        commands.extend(_grammar_rules(relative, text))
    for position, item in enumerate(commands, 1):
        item['item_id'] = f'commands.{position:04d}'

    parser_text = grammar_texts[Path('src/antlr/Parser.g')]
    statement_match = re.search(
        r'(?ms)^cqlStatement\s+returns.*?^\s*:(.*?)^\s*;', parser_text
    )
    if statement_match is None:
        raise RuntimeError('Cassandra cqlStatement grammar is absent')
    statements = []
    for match in re.finditer(
            r'(?:^|\|)\s*\w+\s*=\s*([a-z][A-Za-z0-9_]*)',
            statement_match.group(1)):
        statements.append(_record(
            f'statements.{len(statements) + 1:04d}', match.group(1),
            f'{Path("src/antlr/Parser.g").as_posix()}:'
            f'{_line(parser_text, statement_match.start())}',
            inventory_class='top_level_cql_statement',
        ))

    keyword_path = source_root / KEYWORDS
    keyword_text = keyword_path.read_text(encoding='utf-8')
    lexical = []
    for position, value in enumerate(keyword_text.splitlines(), 1):
        value = value.strip()
        if value:
            lexical.append(_record(
                f'lexical_rules.{len(lexical) + 1:04d}', value,
                f'{KEYWORDS.as_posix()}:{position}',
                category='reserved_keyword',
            ))
    lexer_text = grammar_texts[Path('src/antlr/Lexer.g')]
    for match in re.finditer(r'(?m)^([A-Z][A-Z0-9_]*)\s*:', lexer_text):
        name = match.group(1)
        if name in {item['native_name'] for item in lexical}:
            continue
        lexical.append(_record(
            f'lexical_rules.{len(lexical) + 1:04d}', name,
            f'{Path("src/antlr/Lexer.g").as_posix()}:'
            f'{_line(lexer_text, match.start())}',
            category='antlr_lexer_rule',
        ))

    type_path = source_root / TYPES
    type_text = type_path.read_text(encoding='utf-8')
    enum = re.search(
        r'(?ms)public enum Native.*?\{(.*?)\n\s*private final', type_text
    )
    if enum is None:
        raise RuntimeError('Cassandra native CQL type enum is absent')
    data_types = []
    for match in re.finditer(
            r'(?m)^\s*([A-Z][A-Z0-9_]*)\s*\(([^)]+)\)', enum.group(1)):
        data_types.append(_record(
            f'data_types.{len(data_types) + 1:04d}', match.group(1).lower(),
            f'{TYPES.as_posix()}:'
            f'{_line(type_text, enum.start() + match.start())}',
            type_implementation=match.group(2).strip(),
            type_class='native_scalar',
        ))
    for name, syntax in (
        ('list', 'list<element_type>'), ('set', 'set<element_type>'),
        ('map', 'map<key_type,value_type>'), ('tuple', 'tuple<types...>'),
        ('vector', 'vector<element_type,dimensions>'),
        ('frozen', 'frozen<collection_or_user_type>'),
        ('user-defined-type', 'keyspace.type_name'),
        ('custom', "'java_type_name'"),
    ):
        data_types.append(_record(
            f'data_types.{len(data_types) + 1:04d}', name,
            TYPES.as_posix(), syntax=syntax, type_class='composite_or_custom',
        ))

    operators = []
    operator_symbols = set()
    for relative in ANTLR_FILES:
        text = grammar_texts[relative]
        for match in re.finditer(r"'([^'\n]+)'", text):
            value = match.group(1)
            if value in {
                '+', '-', '*', '/', '%', '=', '<', '>', '<=', '>=',
                '!=', '<>', '+=', '-=', '*=', '/=', '%=', '[]',
            } and value not in operator_symbols:
                operator_symbols.add(value)
                operators.append(_record(
                    f'operators.{len(operators) + 1:04d}', value,
                    f'{relative.as_posix()}:{_line(text, match.start())}',
                ))

    functions = _function_inventory(source_root, data_types)

    diagnostic_path = source_root / DIAGNOSTICS
    diagnostic_text = diagnostic_path.read_text(encoding='utf-8')
    diagnostics = []
    for match in re.finditer(
            r'(?m)^\s*([A-Z][A-Z0-9_]+)\s*\(0x([0-9A-Fa-f]+)\)',
            diagnostic_text):
        diagnostics.append(_record(
            f'diagnostics.{len(diagnostics) + 1:04d}', match.group(1),
            f'{DIAGNOSTICS.as_posix()}:'
            f'{_line(diagnostic_text, match.start())}',
            protocol_error_code='0x' + match.group(2).upper(),
        ))

    inventories = {
        'lexical_rules': lexical,
        'statements': statements,
        'commands': commands,
        'data_types': data_types,
        'functions': functions,
        'operators': operators,
        'session_settings': [],
        'diagnostics': diagnostics,
    }
    if any(not values for name, values in inventories.items()
           if name != 'session_settings'):
        raise RuntimeError('Cassandra source inventory is incomplete')
    return inventories


def _function_inventory(source_root, data_types):
    registry = source_root / FUNCTIONS / 'NativeFunctions.java'
    registry_text = registry.read_text(encoding='utf-8')
    classes = re.findall(r'(?m)^\s*([A-Za-z0-9_]+)\.addFunctionsTo\(this\)',
                         registry_text)
    names = {}
    for class_name in classes:
        paths = list((source_root / FUNCTIONS).rglob(class_name + '.java'))
        if len(paths) != 1:
            raise RuntimeError(
                f'Cassandra native function registry source missing: '
                f'{class_name}'
            )
        path = paths[0]
        relative = path.relative_to(source_root)
        text = path.read_text(encoding='utf-8')
        patterns = (
            r'(?:FunctionFactory|NativeScalarFunction|NativeAggregateFunction|'
            r'NowFunction|Factory|mathFct)\(\s*"([a-zA-Z0-9_]+)"',
            r'FUNCTION_NAME(?:_PREFIX)?\s*=\s*"([a-zA-Z0-9_]+)"',
        )
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                names.setdefault(match.group(1), (
                    relative,
                    _line(text, match.start()),
                    class_name,
                ))
    native_types = [
        item['native_name'] for item in data_types
        if item.get('type_class') == 'native_scalar' and
        item['native_name'] != 'blob'
    ]
    generated = {}
    for value in native_types:
        generated[value + '_as_blob'] = 'BytesConversionFcts'
        generated['blob_as_' + value] = 'BytesConversionFcts'
        generated[value + 'asblob'] = 'BytesConversionFcts'
        generated['blobas' + value] = 'BytesConversionFcts'
        generated['cast_as_' + value] = 'CastFcts'
        generated['castAs' + value.title()] = 'CastFcts'
    for name, class_name in generated.items():
        names.setdefault(name, (
            FUNCTIONS / (class_name + '.java'), 1,
            class_name + ':generated_from_CQL3Type.Native',
        ))
    return [
        _record(
            f'functions.{position:04d}', name,
            f'{path.as_posix()}:{line}', registry=registry_name,
        )
        for position, (name, (path, line, registry_name)) in enumerate(
            sorted(names.items()), 1
        )
    ]


def _target(kind, native):
    return {
        'resource_id': f'contract:{kind}:target',
        'resource_kind': kind,
        'display_name': 'target',
        'extensions': {'cassandra': {'native': native}},
    }


def _route():
    return {
        'route_id': 'contract-generation', 'host': '127.0.0.1',
        'port': 19042, 'local_dc': 'datacenter1', 'tls_mode': 'disabled',
        'compression': 'none', 'consistency': 'LOCAL_QUORUM',
        'serial_consistency': 'LOCAL_SERIAL', 'protocol_version': 5,
        'request_timeout': 60, 'connect_timeout': 15,
    }


def _request(kind, operation, draft, native=None):
    return {
        'resource_kind': kind, 'operation_id': operation, 'draft': draft,
        'target_resource': _target(kind, native) if native else None,
        '_provider_route': _route(),
    }


def _representative_requests(client):
    keyspace = {'keyspace_name': 'contract_keyspace'}
    table = {**keyspace, 'table_name': 'contract_table'}
    column = {**table, 'column_name': 'event_id', 'kind': 'clustering'}
    index = {**table, 'index_name': 'contract_index'}
    view = {**keyspace, 'view_name': 'contract_view'}
    trigger = {**table, 'trigger_name': 'contract_trigger'}
    user_type = {**keyspace, 'type_name': 'contract_type'}
    function = {**keyspace, 'function_name': 'contract_state',
                'argument_types': ['int', 'int']}
    aggregate = {**keyspace, 'aggregate_name': 'contract_sum',
                 'argument_types': ['int']}
    role = {'role': 'contract_role'}
    identity = {'identity': 'CN=contract-client'}
    requests = [
        _request('keyspace', 'create', {
            'name': 'contract_keyspace', 'replication': {
                'class': 'NetworkTopologyStrategy', 'datacenter1': 3,
            }, 'durable_writes': True,
        }),
        _request('keyspace', 'alter', {'replication': {
            'class': 'NetworkTopologyStrategy', 'datacenter1': 3,
        }, 'durable_writes': True}, keyspace),
        _request('keyspace', 'drop', {'confirmation': 'contract_keyspace'},
                 keyspace),
        _request('replication', 'alter', {'replication': {
            'class': 'NetworkTopologyStrategy', 'datacenter1': 3,
        }, 'durable_writes': True}, keyspace),
        _request('table', 'create', {
            'keyspace': 'contract_keyspace', 'name': 'contract_table',
            'columns': [
                {'name': 'tenant', 'type': 'text'},
                {'name': 'event_id', 'type': 'int'},
                {'name': 'value', 'type': 'text'},
            ], 'partition_keys': ['tenant'],
            'clustering_keys': ['event_id'], 'options': {},
        }),
        _request('table', 'alter', {'changes': {'add_columns': [
            {'name': 'note', 'type': 'text'},
        ]}}, table),
        _request('table', 'insert', {'values': {
            'tenant': 'tenant', 'event_id': 1, 'value': 'value',
        }}, table),
        _request('table', 'truncate', {'confirmation': 'contract_table'},
                 table),
        _request('table', 'drop', {'confirmation': 'contract_table'}, table),
        _request('column', 'create', {'name': 'extra', 'type': 'int'}, table),
        _request('column', 'rename', {'new_name': 'event_sequence'}, column),
        _request('column', 'drop', {'confirmation': 'event_id'}, column),
        _request('index', 'create', {
            'keyspace': 'contract_keyspace', 'table': 'contract_table',
            'name': 'contract_index', 'target': 'value',
            'index_kind': 'sai', 'options': {},
        }),
        _request('index', 'drop', {'confirmation': 'contract_index'}, index),
        _request('materialized-view', 'create', {
            'keyspace': 'contract_keyspace', 'name': 'contract_view',
            'base_table': 'contract_table', 'select_columns': ['*'],
            'not_null_columns': ['value', 'tenant', 'event_id'],
            'partition_keys': ['value'],
            'clustering_keys': ['tenant', 'event_id'], 'options': {},
        }),
        _request('materialized-view', 'alter', {
            'options': {'comment': 'contract view'},
        }, view),
        _request('materialized-view', 'drop', {
            'confirmation': 'contract_view',
        }, view),
        _request('trigger', 'create', {
            'keyspace': 'contract_keyspace', 'table': 'contract_table',
            'name': 'contract_trigger',
            'class_name': 'org.example.ContractTrigger',
        }),
        _request('trigger', 'drop', {
            'confirmation': 'contract_trigger',
        }, trigger),
        _request('user-defined-type', 'create', {
            'keyspace': 'contract_keyspace', 'name': 'contract_type',
            'fields': [{'name': 'city', 'type': 'text'}],
        }),
        _request('user-defined-type', 'alter', {
            'fields': [{'name': 'postal_code', 'type': 'text'}],
        }, user_type),
        _request('user-defined-type', 'drop', {
            'confirmation': 'contract_type',
        }, user_type),
        _request('function', 'create', {
            'keyspace': 'contract_keyspace', 'name': 'contract_state',
            'arguments': [
                {'name': 'state_value', 'type': 'int'},
                {'name': 'input_value', 'type': 'int'},
            ], 'return_type': 'int', 'language': 'java',
            'called_on_null_input': True,
            'body': 'return state_value + input_value;',
        }),
        _request('function', 'drop', {'confirmation': 'contract_state'},
                 function),
        _request('aggregate', 'create', {
            'keyspace': 'contract_keyspace', 'name': 'contract_sum',
            'argument_types': ['int'], 'state_function': 'contract_state',
            'state_type': 'int', 'initial_condition': '0',
        }),
        _request('aggregate', 'drop', {'confirmation': 'contract_sum'},
                 aggregate),
        _request('role', 'create', {
            'name': 'contract_role', 'login': False, 'superuser': False,
        }),
        _request('role', 'alter', {
            'login': False, 'superuser': False,
        }, role),
        _request('role', 'grant', {
            'principal': 'contract_member', 'privileges': ['contract_role'],
        }, role),
        _request('role', 'revoke', {
            'principal': 'contract_member', 'privileges': ['contract_role'],
        }, role),
        _request('role', 'drop', {'confirmation': 'contract_role'}, role),
        _request('permission', 'grant', {
            'principal': 'contract_role', 'privileges': ['SELECT'],
            'resource': {'kind': 'keyspace',
                         'keyspace': 'contract_keyspace'},
        }, role),
        _request('permission', 'revoke', {
            'principal': 'contract_role', 'privileges': ['SELECT'],
            'resource': {'kind': 'keyspace',
                         'keyspace': 'contract_keyspace'},
        }, role),
        _request('identity', 'create', {
            'identity': 'CN=contract-client', 'role': 'contract_role',
        }),
        _request('identity', 'drop', {
            'confirmation': 'CN=contract-client',
        }, identity),
    ]
    normalized_route = client._route({'route': _route()})
    for operation in ('update', 'delete'):
        token = f'contract-{operation}'
        client._row_identities[token] = _RowIdentity(
            client._route_fingerprint(normalized_route),
            'contract_keyspace', 'contract_table',
            ('tenant', 'event_id'), ('tenant', 1),
            {'tenant': 'tenant', 'event_id': 1, 'value': 'before'},
            time.monotonic(),
        )
        draft = {'selector': {'identity_token': token}}
        if operation == 'update':
            draft['changes'] = {'value': 'after'}
        else:
            draft['confirmation'] = 'delete-row'
        requests.append(_request('table', operation, draft, table))
    return requests


def _task_templates(live):
    client = CassandraClient()
    expected = set(client.admin_dialect_task_ids())
    templates = []
    for request in _representative_requests(client):
        plan = client.plan_admin_operation(request)
        task_id = ('visual_admin.' + request['resource_kind'] + '.' +
                   request['operation_id'])
        statements = plan['command_preview']['statements']
        if task_id not in expected or not statements:
            raise RuntimeError(f'Cassandra task plan is incomplete: {task_id}')
        templates.append({
            'task_id': task_id,
            'source': '\n;\n'.join(statements),
            'source_format': 'ordered_native_statements',
            'statements': statements,
            'required_bindings': [
                f'parameter_{position}'
                for position in range(
                    1, 1 + sum(item.count('%s') for item in statements)
                )
            ],
            'binding_style': 'cassandra_driver_percent_s',
            'proof_ids': [
                'cassandra-5.0.8-parser-acceptance',
                'cassandra-5.0.8-live-execution',
            ],
        })
    task_ids = {item['task_id'] for item in templates}
    if task_ids != expected:
        raise RuntimeError(
            'Cassandra generated task inventory differs from provider: ' +
            repr(sorted(expected.symmetric_difference(task_ids)))
        )
    if live.get('required_passed') is not True:
        raise RuntimeError('Cassandra exact live evidence is not passing')
    return sorted(templates, key=lambda item: item['task_id'])


def build_dialect(source_root, source_archive, live_path):
    inventories = _source_inventory(source_root)
    live = json.loads(live_path.read_text(encoding='utf-8'))
    if live.get('server_expected') != REFERENCE_VERSION:
        raise RuntimeError('Cassandra live evidence has the wrong profile')
    templates = _task_templates(live)
    return {
        'schema': 'cdeadmin.engine-dialect-inventory.v1',
        'inventory_id': 'cassandra.dialect-inventory.5.0.8.v1',
        'engine_id': 'cassandra', 'interface_id': 'cql',
        'reference_version': REFERENCE_VERSION,
        'source_archive': source_archive.name,
        'source_archive_sha256': _sha256(source_archive),
        'inventories': inventories,
        'completeness': {
            'runtime_version': REFERENCE_VERSION,
            'runtime_interface': 'CQL native protocol v5',
            'antlr_grammar_files_exhausted': [
                item.as_posix() for item in ANTLR_FILES
            ],
            'reserved_keyword_catalog_exhausted': True,
            'native_type_registry_exhausted': True,
            'native_function_registry_exhausted': True,
            'protocol_diagnostic_catalog_exhausted': True,
            'session_setting_catalog': (
                'no_server_session_setting_catalog; CQL USE and provider '
                'route consistency defaults are separate native surfaces'
            ),
            'inventory_counts': {
                name: len(records) for name, records in inventories.items()
            },
        },
    }, templates


def build_dialect_contract(inventory, inventory_path, source_archive,
                           live_path, templates):
    source_id = 'cassandra-5.0.8-source-grammar'
    catalog_id = 'cassandra-5.0.8-source-catalog'
    live_id = 'cassandra-5.0.8-live-execution'
    driver_id = 'cassandra-driver-3.30.1'
    driver_path = Path(cassandra.__file__)
    proofs = [
        _evidence(source_id, 'grammar', 'Apache Cassandra project',
                  source_archive.name, _sha256(source_archive),
                  'Apache Cassandra 5.0.8 source archive'),
        _evidence(catalog_id, 'catalog',
                  'Apache Cassandra 5.0.8 source registries',
                  inventory_path.name, _sha256(inventory_path),
                  'cdeadmin.engine-dialect-inventory.v1', 'PostgreSQL'),
        _evidence('cassandra-5.0.8-parser-acceptance', 'parser_acceptance',
                  'Apache Cassandra 5.0.8 runtime', live_path.name,
                  _sha256(live_path), 'cdeadmin.cassandra-live-gate.v1',
                  'PostgreSQL'),
        _evidence(live_id, 'live_execution',
                  'Apache Cassandra 5.0.8 three-node runtime',
                  live_path.name, _sha256(live_path),
                  'cdeadmin.cassandra-live-gate.v1', 'PostgreSQL'),
        _evidence(driver_id, 'driver',
                  'Apache Cassandra Python Driver project', driver_path.name,
                  _sha256(driver_path), 'Python package'),
    ]
    inventories = {
        name: [{**copy.deepcopy(item), 'proof_ids': [
            source_id if name != 'session_settings' else catalog_id
        ]} for item in records]
        for name, records in inventory['inventories'].items()
    }
    task_ids = [item['task_id'] for item in templates]
    decisions = {
        'identifier_quoting': 'double_quote_with_doubled_quote_escape',
        'string_literals': 'single_quote_with_doubled_quote_escape',
        'parameter_binding': 'cassandra_driver_percent_s',
        'transaction_control': (
            'no_general_multi_statement_transaction; logged/unlogged '
            'batches and lightweight transactions remain engine-owned'),
        'pagination': 'native protocol paging state with SELECT LIMIT',
        'explain': 'not exposed by Apache Cassandra CQL 5.0.8',
        'cancellation': 'driver ResponseFuture.cancel',
    }
    return {
        'schema': 'cdeadmin.engine-dialect.v2',
        'contract_id': 'cassandra.dialect.5.0.8.v1',
        'provider_id': 'org.cdeadmin.cassandra',
        'profile_id': 'cassandra-native', 'engine_id': 'cassandra',
        'interface_id': 'cql', 'reference_version': REFERENCE_VERSION,
        'language_profiles': ['cql-3'],
        'grammar_evidence': {
            key: value for key, value in proofs[0].items()
            if key not in {'evidence_id', 'evidence_kind'}
        },
        'proof_records': proofs, 'inventories': inventories,
        'syntax_decisions': {
            name: {'syntax': syntax, 'proof_ids': [
                driver_id if name in {'parameter_binding', 'cancellation'}
                else source_id
            ]} for name, syntax in decisions.items()
        },
        'task_templates': templates,
        'coverage': {
            'scope': 'cdeadmin_generated_tasks',
            'language_acceptance_authority': 'engine_parser',
            'authoritative_inventory_counts': {
                name: len(records) for name, records in inventories.items()
            },
            'authoritative_task_count': len(task_ids),
            'implemented_task_count': len(task_ids),
            'authoritative_task_ids': task_ids,
        },
        'live_evidence_ids': [live_id],
        'complete_grammar_inventory': {
            'artifact': inventory_path.name,
            'sha256': _sha256(inventory_path),
            **inventory['completeness'],
        },
    }


def _row_dict(row):
    return dict(row._asdict())


def _virtual_observations(host, port, username, password, local_dc):
    cluster = Cluster(
        [host], port=port, protocol_version=5,
        auth_provider=PlainTextAuthProvider(username, password),
        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc=local_dc),
        connect_timeout=15,
    )
    session = None
    try:
        session = cluster.connect()
        session.default_timeout = 60
        tables = [_row_dict(row) for row in session.execute(
            VIRTUAL_SCHEMA_TABLES, ('system_views',)
        )]
        columns = [_row_dict(row) for row in session.execute(
            VIRTUAL_SCHEMA_COLUMNS, ('system_views',)
        )]
        version = session.execute(
            'SELECT release_version FROM system.local'
        ).one().release_version
    finally:
        if session is not None:
            session.shutdown()
        cluster.shutdown()
    if version != REFERENCE_VERSION or not tables or not columns:
        raise RuntimeError('Cassandra virtual observation catalog incomplete')
    comments = {row['table_name']: row.get('comment') or '' for row in tables}
    observations = []
    for row in sorted(columns, key=lambda item: (
            item['table_name'], item['column_name'])):
        observations.append({
            'native_name': (
                f"system_views.{row['table_name']}.{row['column_name']}"
            ),
            'scope': 'node_virtual_table_row',
            'table_name': row['table_name'],
            'column_name': row['column_name'], 'cql_type': row['type'],
            'column_kind': row['kind'], 'position': row['position'],
            'description': comments[row['table_name']] or (
                f"Cassandra system_views.{row['table_name']} observation"
            ),
            'source': 'system_virtual_schema.tables+columns',
            'observation_class': 'virtual_table_field',
        })
    return observations, len(tables)


def _documented_metrics(source_root):
    path = source_root / METRICS_DOC
    text = path.read_text(encoding='utf-8')
    pattern = re.compile(
        r'(?ms)^\|([A-Za-z][A-Za-z0-9]+)\s+'
        r'\|([^|\n]+?)\s+\|(.*?)(?=^\|[A-Za-z][A-Za-z0-9]+\s+\||'
        r'^\|===)'
    )
    headings = list(re.finditer(r'(?m)^==+\s+(.+?)\s*$', text))
    records = []
    for match in pattern.finditer(text):
        name = match.group(1)
        if name == 'Name':
            continue
        heading = next(
            (item.group(1) for item in reversed(headings)
             if item.start() < match.start()),
            'Cassandra metrics',
        )
        metric_type = match.group(2).strip()
        description = re.sub(r'\s+', ' ', match.group(3)).strip()
        records.append({
            'native_name': name, 'metric_family': heading,
            'metric_type': metric_type, 'description': description,
            'scope': _slug(heading.removesuffix(' Metrics')) or 'node',
            'source': f'{METRICS_DOC.as_posix()}:'
            f'{_line(text, match.start())}',
            'observation_class': 'documented_dropwizard_metric',
        })
    if not records:
        raise RuntimeError('Cassandra documented metric catalog is empty')
    return records


def build_metrics_inventory(source_root, host, port, username, password,
                            local_dc):
    documented = _documented_metrics(source_root)
    virtual, table_count = _virtual_observations(
        host, port, username, password, local_dc
    )
    observations = documented + virtual
    return {
        'schema': 'cdeadmin.engine-metrics-inventory.v1',
        'inventory_id': 'cassandra.metrics-inventory.5.0.8.v1',
        'engine_id': 'cassandra', 'interface_id': 'cql+jmx',
        'reference_version': REFERENCE_VERSION,
        'collection_authority': (
            'Apache Cassandra 5.0.8 metrics documentation and live '
            'system_virtual_schema catalog'
        ),
        'endpoint_selection': (
            'Every documented Dropwizard/JMX metric definition plus every '
            'field of every live system_views virtual table.'
        ),
        'observations': observations,
        'completeness': {
            'runtime_version': REFERENCE_VERSION,
            'documented_metric_count': len(documented),
            'system_views_table_count': table_count,
            'system_views_field_count': len(virtual),
            'observation_count': len(observations),
            'metrics_document_tables_exhausted': True,
            'live_virtual_schema_exhausted': True,
        },
    }


def _metric_kind(value):
    lowered = value.lower()
    for kind in ('counter', 'histogram', 'timer', 'meter', 'latency', 'ratio'):
        if kind in lowered:
            return kind
    return 'gauge'


def build_metrics_contract(inventory, inventory_path, source_archive,
                           live_path):
    source_id = 'cassandra-5.0.8-source-metric-catalog'
    runtime_id = 'cassandra-5.0.8-virtual-observation-catalog'
    live_id = 'cassandra-5.0.8-live-execution'
    evidence = [
        _evidence(source_id, 'documentation', 'Apache Cassandra project',
                  source_archive.name, _sha256(source_archive),
                  'Apache Cassandra 5.0.8 source archive'),
        _evidence(runtime_id, 'catalog', 'Apache Cassandra 5.0.8 runtime',
                  inventory_path.name, _sha256(inventory_path),
                  'cdeadmin.engine-metrics-inventory.v1', 'PostgreSQL'),
        _evidence(live_id, 'live_execution',
                  'Apache Cassandra 5.0.8 three-node runtime',
                  live_path.name, _sha256(live_path),
                  'cdeadmin.cassandra-live-gate.v1', 'PostgreSQL'),
    ]
    observations = []
    metrics = []
    numeric = {'bigint', 'counter', 'decimal', 'double', 'float', 'int',
               'smallint', 'tinyint', 'varint'}
    for position, item in enumerate(inventory['observations'], 1):
        metric_family = item.get('metric_family') or item.get('table_name')
        observation_id = (
            f'cassandra.{position:05d}.{_slug(metric_family)}.'
            f'{_slug(item["native_name"])}'
        )
        is_metric = (
            item['observation_class'] == 'documented_dropwizard_metric' or
            item.get('cql_type') in numeric
        )
        evidence_ids = [source_id, runtime_id, live_id]
        observations.append({
            'observation_id': observation_id,
            'native_name': item['native_name'], 'scope': item['scope'],
            'source': item['source'],
            'value_type': item.get('cql_type', item.get('metric_type')),
            'observation_class': item['observation_class'],
            'description': item['description'],
            'privilege': 'cql_read_or_jmx_metric_read',
            'version_condition': REFERENCE_VERSION,
            'collection_cost': 'one_node_catalog_or_metric_read',
            'poll_interval_seconds': 15,
            'cardinality': 'native_scope_cardinality',
            'redaction': 'provider_value_and_label_redaction_policy',
            'evidence_ids': evidence_ids,
        })
        if is_metric:
            kind = _metric_kind(item.get('metric_type', 'gauge'))
            metrics.append({
                'metric_id': observation_id,
                'observation_id': observation_id, 'unit': 'native_unit',
                'kind': kind,
                'reset_behavior': (
                    'process_restart' if kind in {'counter', 'histogram',
                                                  'timer', 'meter', 'latency'}
                    else 'current_observation'
                ),
                'aggregation': 'preserve_native_scope',
                'evidence_ids': evidence_ids,
            })
    observation_ids = [item['observation_id'] for item in observations]
    metric_ids = [item['metric_id'] for item in metrics]
    return {
        'schema': 'cdeadmin.engine-metrics.v2',
        'contract_id': 'cassandra.metrics.5.0.8.v1',
        'provider_id': 'org.cdeadmin.cassandra',
        'profile_id': 'cassandra-native', 'engine_id': 'cassandra',
        'interface_id': 'cql+jmx', 'reference_version': REFERENCE_VERSION,
        'catalog_evidence': {
            key: value for key, value in evidence[1].items()
            if key not in {'evidence_id', 'evidence_kind'}
        },
        'classification_evidence': evidence,
        'native_observations': observations, 'metrics': metrics,
        'authoritative_observation_count': len(observation_ids),
        'authoritative_observation_ids': observation_ids,
        'authoritative_metric_count': len(metric_ids),
        'authoritative_metric_ids': metric_ids,
        'live_evidence_ids': [runtime_id, live_id],
    }


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n',
                    encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--live-evidence', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=19042)
    parser.add_argument('--local-dc', default='datacenter1')
    parser.add_argument('--username', default='cassandra')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    password = os.environ.get('CDEADMIN_CASSANDRA_PASSWORD')
    if password is None:
        parser.error('CDEADMIN_CASSANDRA_PASSWORD is required')
    inventory, templates = build_dialect(
        args.source_root, args.source_archive, args.live_evidence
    )
    dialect_inventory_path = (
        args.output_dir / 'cassandra_dialect_inventory_5_0_8.json'
    )
    _write(dialect_inventory_path, inventory)
    _write(args.output_dir / 'cassandra_dialect_5_0_8.json',
           build_dialect_contract(
               inventory, dialect_inventory_path, args.source_archive,
               args.live_evidence, templates,
           ))
    metrics_inventory = build_metrics_inventory(
        args.source_root, args.host, args.port, args.username, password,
        args.local_dc,
    )
    metrics_inventory_path = (
        args.output_dir / 'cassandra_metrics_inventory_5_0_8.json'
    )
    _write(metrics_inventory_path, metrics_inventory)
    metrics = build_metrics_contract(
        metrics_inventory, metrics_inventory_path, args.source_archive,
        args.live_evidence,
    )
    _write(args.output_dir / 'cassandra_metrics_5_0_8.json', metrics)
    print(json.dumps({
        'dialect_counts': inventory['completeness']['inventory_counts'],
        'task_count': len(templates),
        'observation_count': len(metrics['native_observations']),
        'metric_count': len(metrics['metrics']),
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
