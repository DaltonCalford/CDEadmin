##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Fail-closed tests for exact dialect and metrics activation contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.engine_contracts import (  # noqa: E402
    EngineContractError,
    contract_descriptor,
    validate_dialect_contract,
    validate_metrics_contract,
)


PROFILE = SimpleNamespace(
    provider_id='org.cdeadmin.example',
    profile_id='example-native',
    engine_id='example',
    exact_version='1.2.3',
    language_profile='example-language',
    dialect_contract_file=None,
    metrics_contract_file=None,
)


def evidence():
    return {
        'authority': 'upstream exact-version parser source',
        'artifact': 'grammar/source.y',
        'sha256': 'a' * 64,
        'format': 'yacc',
        'license_id': 'Example-1.0',
    }


def dialect():
    return {
        'schema': 'cdeadmin.engine-dialect.v2',
        'contract_id': 'example.dialect.1.2.3.v2',
        'provider_id': PROFILE.provider_id,
        'profile_id': PROFILE.profile_id,
        'engine_id': PROFILE.engine_id,
        'interface_id': 'example-native-interface',
        'reference_version': PROFILE.exact_version,
        'language_profiles': [PROFILE.language_profile],
        'grammar_evidence': evidence(),
        'proof_records': [{
            **evidence(), 'evidence_id': 'example-grammar',
            'evidence_kind': 'grammar',
        }, {
            **evidence(), 'evidence_id': 'example-parser-identity',
            'evidence_kind': 'parser_acceptance',
        }, {
            **evidence(), 'evidence_id': 'example-live-identity',
            'evidence_kind': 'live_execution',
        }],
        'inventories': {
            'lexical_rules': [{
                'item_id': 'identifier', 'native_name': 'identifier',
                'source': 'grammar', 'proof_ids': ['example-grammar'],
            }],
            'statements': [{
                'item_id': 'values', 'native_name': 'VALUES',
                'source': 'grammar', 'proof_ids': ['example-grammar'],
            }],
            'commands': [],
            'data_types': [{
                'item_id': 'integer', 'native_name': 'INTEGER',
                'source': 'grammar', 'proof_ids': ['example-grammar'],
            }],
            'functions': [],
            'operators': [{
                'item_id': 'plus', 'native_name': '+',
                'source': 'grammar', 'proof_ids': ['example-grammar'],
            }],
            'session_settings': [],
            'diagnostics': [{
                'item_id': 'sqlstate', 'native_name': 'SQLSTATE',
                'source': 'grammar', 'proof_ids': ['example-grammar'],
            }],
        },
        'syntax_decisions': {
            'identifier_quoting': {
                'style': 'double_quote', 'proof_ids': ['example-grammar']},
            'string_literals': {
                'style': 'single_quote', 'proof_ids': ['example-grammar']},
            'parameter_binding': {
                'style': 'qmark', 'proof_ids': ['example-grammar']},
            'transaction_control': {
                'style': 'native', 'proof_ids': ['example-grammar']},
            'pagination': {
                'style': 'rows', 'proof_ids': ['example-grammar']},
            'explain': {
                'style': 'native', 'proof_ids': ['example-grammar']},
            'cancellation': {
                'style': 'driver', 'proof_ids': ['example-grammar']},
        },
        'task_templates': [{
            'task_id': 'connection.identity',
            'source': 'VALUES (1)',
            'required_bindings': [],
            'proof_ids': [
                'example-parser-identity', 'example-live-identity'],
        }],
        'coverage': {
            'scope': 'cdeadmin_generated_tasks',
            'language_acceptance_authority': 'engine_parser',
            'authoritative_inventory_counts': {
                'lexical_rules': 1, 'statements': 1, 'commands': 0,
                'data_types': 1, 'functions': 0, 'operators': 1,
                'session_settings': 0, 'diagnostics': 1,
            },
            'authoritative_task_ids': ['connection.identity'],
            'authoritative_task_count': 1,
            'implemented_task_count': 1,
        },
        'live_evidence_ids': ['example-live-identity'],
    }


def metrics():
    return {
        'schema': 'cdeadmin.engine-metrics.v2',
        'contract_id': 'example.metrics.1.2.3.v2',
        'provider_id': PROFILE.provider_id,
        'profile_id': PROFILE.profile_id,
        'engine_id': PROFILE.engine_id,
        'interface_id': 'example-native-interface',
        'reference_version': PROFILE.exact_version,
        'catalog_evidence': evidence(),
        'classification_evidence': [{
            **evidence(), 'evidence_id': 'example-native-status-doc',
        }],
        'native_observations': [{
            'observation_id': 'example.connections.current',
            'native_name': 'connections',
            'scope': 'server',
            'source': 'native status API',
            'value_type': 'integer',
            'observation_class': 'operational_metric',
            'description': 'Current native connection count.',
            'privilege': 'monitor',
            'version_condition': '1.2.3',
            'collection_cost': 'constant',
            'poll_interval_seconds': 15,
            'cardinality': 'one_per_server',
            'redaction': 'none',
            'evidence_ids': ['example-native-status-doc'],
        }],
        'metrics': [{
            'metric_id': 'example.connections.current',
            'observation_id': 'example.connections.current',
            'unit': 'connections',
            'kind': 'gauge',
            'reset_behavior': 'not_applicable',
            'aggregation': 'none',
            'evidence_ids': ['example-native-status-doc'],
        }],
        'authoritative_observation_count': 1,
        'authoritative_observation_ids': ['example.connections.current'],
        'authoritative_metric_count': 1,
        'authoritative_metric_ids': ['example.connections.current'],
        'live_evidence_ids': ['example-live-metrics'],
    }


def test_complete_exact_contracts_validate_without_defaults():
    assert validate_dialect_contract(dialect(), PROFILE)['engine_id'] == (
        'example'
    )
    assert validate_metrics_contract(metrics(), PROFILE)['engine_id'] == (
        'example'
    )


def test_incomplete_dialect_coverage_is_rejected():
    value = dialect()
    value['coverage']['authoritative_task_count'] = 2
    with pytest.raises(EngineContractError, match='not complete and exact'):
        validate_dialect_contract(value, PROFILE)


def test_dialect_coverage_cannot_pass_with_matching_false_counts():
    value = dialect()
    value['coverage']['authoritative_task_ids'] = ['unimplemented.task']
    with pytest.raises(EngineContractError, match='not complete and exact'):
        validate_dialect_contract(value, PROFILE)


def test_dialect_must_cover_every_executable_provider_task():
    with pytest.raises(EngineContractError, match='executable provider'):
        validate_dialect_contract(
            dialect(), PROFILE, {'visual_admin.table.create'}
        )


def test_dialect_inventory_counts_cannot_hide_missing_native_items():
    value = dialect()
    value['inventories']['operators'] = []
    with pytest.raises(EngineContractError, match='inventory coverage'):
        validate_dialect_contract(value, PROFILE)


def test_each_task_requires_parser_and_live_execution_evidence():
    value = dialect()
    value['task_templates'][0]['proof_ids'] = ['example-live-identity']
    with pytest.raises(EngineContractError, match='parser and live'):
        validate_dialect_contract(value, PROFILE)


def test_metric_without_polling_semantics_is_rejected():
    value = metrics()
    del value['native_observations'][0]['poll_interval_seconds']
    with pytest.raises(
            EngineContractError, match='native observation record is missing'):
        validate_metrics_contract(value, PROFILE)


def test_metric_coverage_cannot_pass_with_matching_false_counts():
    value = metrics()
    value['authoritative_metric_ids'] = ['example.missing.metric']
    with pytest.raises(EngineContractError, match='not complete'):
        validate_metrics_contract(value, PROFILE)


def test_metric_must_reference_an_authoritative_native_observation():
    value = metrics()
    value['metrics'][0]['observation_id'] = 'example.unknown'
    with pytest.raises(EngineContractError, match='unknown native'):
        validate_metrics_contract(value, PROFILE)


def test_absent_provider_contracts_are_visible_blockers():
    result = contract_descriptor(PROFILE, __name__)
    assert result['state'] == 'blocked'
    assert result['dialect']['reason'] == (
        'provider_exact_dialect_contract_absent'
    )
    assert result['metrics']['reason'] == (
        'provider_exact_metrics_contract_absent'
    )


def test_firebird_exact_dialect_and_metric_inventories_are_activated():
    from pgadmin.cdeadmin.providers.firebird.provider import (
        ADMINISTRATION as FIREBIRD_ADMINISTRATION,
        PROFILE as FIREBIRD,
    )

    result = contract_descriptor(
        FIREBIRD, 'pgadmin.cdeadmin.providers.firebird.provider',
        FIREBIRD_ADMINISTRATION.dialect_task_ids(),
    )
    assert result['metrics']['state'] == 'passed'
    assert result['metrics']['contract_id'] == (
        'firebird.metrics.5.0.4.v2'
    )
    assert result['dialect']['state'] == 'passed'
    assert result['dialect']['contract_id'] == (
        'firebird.dialect.5.0.4.v2'
    )

    dialect_document = json.loads((
        WEB / 'pgadmin/cdeadmin/providers/firebird/'
        'firebird_dialect_5_0_4.json'
    ).read_text(encoding='utf-8'))
    checked_dialect = validate_dialect_contract(
        dialect_document, FIREBIRD,
        FIREBIRD_ADMINISTRATION.dialect_task_ids(),
    )
    assert len(checked_dialect['inventories']['lexical_rules']) == 518
    assert len(checked_dialect['task_templates']) == 124

    document = json.loads((
        WEB / 'pgadmin/cdeadmin/providers/firebird/'
        'firebird_metrics_5_0_4.json'
    ).read_text(encoding='utf-8'))
    checked = validate_metrics_contract(document, FIREBIRD)
    assert checked['authoritative_observation_count'] == 146
    assert checked['authoritative_metric_count'] == 36
    assert all(
        item['observation_class'] != 'operational_metric'
        for item in checked['native_observations']
        if item['native_name'] in {
            'MON$ATTACHMENT_ID', 'MON$SQL_TEXT', 'MON$EXPLAINED_PLAN'
        }
    )


def test_duckdb_exact_dialect_inventory_activates_generated_tasks():
    from pgadmin.cdeadmin.providers.duckdb.provider import (
        ADMINISTRATION as DUCKDB_ADMINISTRATION,
        PROFILE as DUCKDB,
    )

    result = contract_descriptor(
        DUCKDB, 'pgadmin.cdeadmin.providers.duckdb.provider',
        DUCKDB_ADMINISTRATION.dialect_task_ids(),
    )
    assert result['dialect']['state'] == 'passed'
    assert result['dialect']['contract_id'] == (
        'duckdb.dialect.1.5.2.v1'
    )
    assert result['metrics']['state'] == 'passed'
    assert result['metrics']['contract_id'] == (
        'duckdb.metrics.1.5.2.v1'
    )
    document = json.loads((
        WEB / 'pgadmin/cdeadmin/providers/duckdb/'
        'duckdb_dialect_1_5_2.json'
    ).read_text(encoding='utf-8'))
    checked = validate_dialect_contract(
        document, DUCKDB, DUCKDB_ADMINISTRATION.dialect_task_ids()
    )
    assert checked['coverage']['authoritative_task_count'] == 33
    assert checked['coverage']['authoritative_inventory_counts'] == {
        'lexical_rules': 489,
        'statements': 42,
        'commands': 511,
        'data_types': 244,
        'functions': 2946,
        'operators': 221,
        'session_settings': 157,
        'diagnostics': 43,
    }
    metrics_document = json.loads((
        WEB / 'pgadmin/cdeadmin/providers/duckdb/'
        'duckdb_metrics_1_5_2.json'
    ).read_text(encoding='utf-8'))
    checked_metrics = validate_metrics_contract(metrics_document, DUCKDB)
    assert checked_metrics['authoritative_observation_count'] == 58
    assert checked_metrics['authoritative_metric_count'] == 12


def test_mysql_exact_dialect_inventory_activates_generated_tasks():
    from pgadmin.cdeadmin.providers.mysql_family.provider import (
        MYSQL_ADMINISTRATION,
        MYSQL_PROFILE,
    )

    result = contract_descriptor(
        MYSQL_PROFILE,
        'pgadmin.cdeadmin.providers.mysql_family.provider',
        MYSQL_ADMINISTRATION.dialect_task_ids(),
    )
    assert result['dialect']['state'] == 'passed'
    assert result['dialect']['contract_id'] == (
        'mysql.dialect.9.7.0.v1'
    )
    assert result['metrics']['state'] == 'passed'
    assert result['metrics']['contract_id'] == 'mysql.metrics.9.7.0.v1'
    document = json.loads((
        WEB / 'pgadmin/cdeadmin/providers/mysql_family/'
        'mysql_dialect_9_7_0.json'
    ).read_text(encoding='utf-8'))
    checked = validate_dialect_contract(
        document, MYSQL_PROFILE, MYSQL_ADMINISTRATION.dialect_task_ids()
    )
    assert checked['coverage']['authoritative_task_count'] == 46
    assert checked['coverage']['authoritative_inventory_counts'] == {
        'lexical_rules': 760,
        'statements': 143,
        'commands': 987,
        'data_types': 37,
        'functions': 353,
        'operators': 65,
        'session_settings': 655,
        'diagnostics': 1932,
    }
    metrics_document = json.loads((
        WEB / 'pgadmin/cdeadmin/providers/mysql_family/'
        'mysql_metrics_9_7_0.json'
    ).read_text(encoding='utf-8'))
    checked_metrics = validate_metrics_contract(
        metrics_document, MYSQL_PROFILE
    )
    assert checked_metrics['authoritative_observation_count'] == 342
    assert checked_metrics['authoritative_metric_count'] == 23
    metrics = {
        item['native_name']: item
        for item in checked_metrics['native_observations']
        if item['observation_class'] == 'operational_metric'
    }
    definitions = {
        item['observation_id']: item
        for item in checked_metrics['metrics']
    }
    assert definitions[
        metrics['Threads_connected']['observation_id']
    ]['kind'] == 'gauge'
    assert definitions[
        metrics['Connections']['observation_id']
    ]['reset_behavior'] == 'server_restart'


def test_mariadb_exact_dialect_inventory_activates_generated_tasks():
    from pgadmin.cdeadmin.providers.mysql_family.provider import (
        MARIADB_ADMINISTRATION,
        MARIADB_PROFILE,
    )

    result = contract_descriptor(
        MARIADB_PROFILE,
        'pgadmin.cdeadmin.providers.mysql_family.provider',
        MARIADB_ADMINISTRATION.dialect_task_ids(),
    )
    assert result['dialect']['state'] == 'passed'
    assert result['dialect']['contract_id'] == (
        'mariadb.dialect.12.2.2.v1'
    )
    assert result['metrics']['state'] == 'passed'
    assert result['metrics']['contract_id'] == (
        'mariadb.metrics.12.2.2.v1'
    )
    document = json.loads((
        WEB / 'pgadmin/cdeadmin/providers/mysql_family/'
        'mariadb_dialect_12_2_2.json'
    ).read_text(encoding='utf-8'))
    checked = validate_dialect_contract(
        document, MARIADB_PROFILE,
        MARIADB_ADMINISTRATION.dialect_task_ids(),
    )
    assert checked['coverage']['authoritative_task_count'] == 63
    assert checked['coverage']['authoritative_inventory_counts'] == {
        'lexical_rules': 693,
        'statements': 59,
        'commands': 1123,
        'data_types': 36,
        'functions': 223,
        'operators': 70,
        'session_settings': 708,
        'diagnostics': 1097,
    }
    metrics_document = json.loads((
        WEB / 'pgadmin/cdeadmin/providers/mysql_family/'
        'mariadb_metrics_12_2_2.json'
    ).read_text(encoding='utf-8'))
    checked_metrics = validate_metrics_contract(
        metrics_document, MARIADB_PROFILE
    )
    assert checked_metrics['authoritative_observation_count'] == 571
    assert checked_metrics['authoritative_metric_count'] == 28
    metrics = {
        item['native_name']: item
        for item in checked_metrics['native_observations']
        if item['observation_class'] == 'operational_metric'
    }
    definitions = {
        item['observation_id']: item
        for item in checked_metrics['metrics']
    }
    assert len(metrics) == 28
    assert definitions[
        metrics['THREADS_CONNECTED']['observation_id']
    ]['kind'] == 'gauge'
    assert definitions[
        metrics['CONNECTIONS']['observation_id']
    ]['reset_behavior'] == 'server_restart'


def test_sqlite_exact_contracts_activate_generated_tasks_and_metrics():
    from pgadmin.cdeadmin.providers.sqlite.provider import (
        ADMINISTRATION as SQLITE_ADMINISTRATION,
        PROFILE as SQLITE,
    )

    result = contract_descriptor(
        SQLITE, 'pgadmin.cdeadmin.providers.sqlite.provider',
        SQLITE_ADMINISTRATION.dialect_task_ids(),
    )
    assert result['dialect']['state'] == 'passed'
    assert result['dialect']['contract_id'] == (
        'sqlite.dialect.3.53.0.v1'
    )
    assert result['metrics']['state'] == 'passed'
    assert result['metrics']['contract_id'] == (
        'sqlite.metrics.3.53.0.v1'
    )
    dialect_document = json.loads((
        WEB / 'pgadmin/cdeadmin/providers/sqlite/'
        'sqlite_dialect_3_53_0.json'
    ).read_text(encoding='utf-8'))
    checked = validate_dialect_contract(
        dialect_document, SQLITE, SQLITE_ADMINISTRATION.dialect_task_ids()
    )
    assert checked['coverage']['authoritative_task_count'] == 32
    assert checked['coverage']['authoritative_inventory_counts'] == {
        'lexical_rules': 148,
        'statements': 45,
        'commands': 416,
        'data_types': 12,
        'functions': 196,
        'operators': 31,
        'session_settings': 66,
        'diagnostics': 113,
    }
    metrics_document = json.loads((
        WEB / 'pgadmin/cdeadmin/providers/sqlite/'
        'sqlite_metrics_3_53_0.json'
    ).read_text(encoding='utf-8'))
    checked_metrics = validate_metrics_contract(metrics_document, SQLITE)
    assert checked_metrics['authoritative_observation_count'] == 23
    assert checked_metrics['authoritative_metric_count'] == 6
    assert len(
        checked_metrics['unavailable_native_c_status_surface']
    ) == 24
    assert all(
        item['dbapi_accessible'] is False
        for item in checked_metrics['unavailable_native_c_status_surface']
    )


def test_every_provider_manifest_records_completed_activation_gate():
    manifests = (
        WEB / 'pgadmin/cdeadmin/providers'
    ).glob('**/*manifest*.json')
    activated = []
    incomplete = []
    for path in manifests:
        document = json.loads(path.read_text(encoding='utf-8'))
        gate = (document.get('provenance') or {}).get('activation_gate', '')
        if str(gate).startswith('passed'):
            activated.append(str(path.relative_to(WEB)))
        else:
            incomplete.append(str(path.relative_to(WEB)))
    assert incomplete == []
    assert len(activated) == 26
