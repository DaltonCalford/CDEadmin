import hashlib
import json

from tools.cdeadmin_reference_engine_operation_qualification_gate import (
    qualify,
)


def _write_evidence(tmp_path, operations, *, profile='1.2.3'):
    path = tmp_path / 'evidence.json'
    path.write_text(json.dumps({
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': 'sample',
        'exact_profile': profile,
        'passed': True,
        'missing_resource_operations': {},
        'operation_failures': [],
        'passed_resource_operations': operations,
    }), encoding='utf-8')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    index = tmp_path / 'index.json'
    index.write_text(json.dumps({
        'profiles': {'sample-native': {
            'path': str(path), 'sha256': digest,
        }},
    }), encoding='utf-8')
    return index


def _inputs(operations):
    profiles = {'sample-native': {
        'engine_id': 'sample',
        'provider_id': 'org.cdeadmin.sample',
        'profile_version': '1.2.3',
    }}
    objects = []
    for resource_kind, operation_ids in operations.items():
        objects.append({
            'resource_kind': resource_kind,
            'operations': [{
                'operation_id': operation_id,
                'native_supported': True,
                'graphical_ready': True,
                'graphical_form_authority': 'provider-adapter',
                'form': {'form_id': f'{resource_kind}.{operation_id}'},
            } for operation_id in operation_ids],
        })
    catalogs = {'sample-native': {
        'descriptor': {'engine_id': 'sample', 'objects': objects},
    }}
    return catalogs, profiles


def test_qualifies_exact_matching_final_catalog(tmp_path):
    operations = {'table': ['create', 'alter', 'drop', 'inspect']}
    index = _write_evidence(tmp_path, operations)
    catalogs, profiles = _inputs(operations)

    result = qualify(index, catalogs, profiles)

    assert result['complete'] is True
    assert result['catalog_operation_count'] == 4
    assert result['missing_live_operation_count'] == 0
    assert result['profiles']['sample-native']['passed'] is True


def test_blocks_final_catalog_operation_missing_from_live_evidence(tmp_path):
    index = _write_evidence(tmp_path, {'table': ['create', 'inspect']})
    catalogs, profiles = _inputs({
        'table': ['create', 'alter', 'drop', 'inspect'],
    })

    result = qualify(index, catalogs, profiles)

    assert result['complete'] is False
    assert result['missing_live_operation_count'] == 2
    assert result['profiles']['sample-native']['missing_live_operations'] == {
        'table': ['alter', 'drop'],
    }


def test_blocks_wrong_exact_profile(tmp_path):
    operations = {'table': ['inspect']}
    index = _write_evidence(tmp_path, operations, profile='1.2.2')
    catalogs, profiles = _inputs(operations)

    result = qualify(index, catalogs, profiles)

    assert result['complete'] is False
    failures = result['profiles']['sample-native']['failures']
    assert 'live-evidence-profile-mismatch' in failures


def test_blocks_live_operation_absent_from_final_catalog(tmp_path):
    index = _write_evidence(
        tmp_path, {'table': ['create', 'drop', 'inspect']}
    )
    catalogs, profiles = _inputs({'table': ['create', 'inspect']})

    result = qualify(index, catalogs, profiles)

    assert result['complete'] is False
    assert result['evidence_only_operation_count'] == 1
    assert result['profiles']['sample-native'][
        'evidence_only_operations'
    ] == {'table': ['drop']}
