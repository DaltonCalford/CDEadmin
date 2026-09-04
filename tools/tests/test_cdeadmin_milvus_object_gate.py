##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

from tools.cdeadmin_milvus_object_experience_gate import audit


def test_milvus_vector_declarations_are_structurally_complete():
    result = audit()
    assert result['structural_complete'] is True
    assert result['live_complete'] is False
    family = result['coverage']['families'][0]
    assert family['family_id'] == 'vector'
    assert {item['concept_id'] for item in family['concepts']} == {
        'collections', 'fields', 'indexes', 'partitions', 'load_state',
        'resource_groups',
    }


def test_milvus_vector_declarations_require_live_operation_evidence(tmp_path):
    operations = {
        'collection': [
            'alter', 'create', 'delete', 'drop', 'insert', 'inspect',
            'rename', 'update',
        ],
        'field': ['alter', 'create', 'inspect'],
        'vector-index': ['alter', 'create', 'drop', 'inspect'],
        'partition': [
            'create', 'delete', 'drop', 'insert', 'inspect', 'update',
        ],
        'load-state': ['execute', 'inspect'],
        'resource-group': ['alter', 'create', 'drop', 'inspect'],
    }
    concepts = {'vector': {
        concept: {
            'status': 'passed',
            'operations': {kind: operations[kind] for kind in kinds},
        }
        for concept, kinds in {
            'collections': ('collection',), 'fields': ('field',),
            'indexes': ('vector-index',), 'partitions': ('partition',),
            'load_state': ('load-state',),
            'resource_groups': ('resource-group',),
        }.items()
    }}
    evidence = tmp_path / 'milvus-live.json'
    evidence.write_text(__import__('json').dumps({
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': 'milvus', 'exact_profile': '2.6.5',
        'concepts': concepts,
        'passed_resource_operations': operations,
        'missing_resource_operations': {}, 'operation_failures': [],
        'raw_commands_used_for_provider_operations': False,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpreted': False,
        'passed': True,
    }), encoding='utf-8')
    result = audit([evidence])
    assert result['structural_complete'] is True
    assert result['live_complete'] is True
