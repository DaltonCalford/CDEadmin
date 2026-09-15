"""Exact native availability bounds, plans, and safe diagnostic evidence."""

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

WEB = Path(__file__).resolve().parents[2] / 'web'
sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.firebird import availability  # noqa: E402
from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    ADMINISTRATION, _firebird_service_operation,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError  # noqa: E402
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine  # noqa: E402
from tools.cdeadmin_firebird_availability_gate import missing_privileges  # noqa: E402,E501


@pytest.mark.parametrize('timeout', [0, 1, 32766, 32767])
def test_native_shutdown_timeout_boundaries(timeout):
    availability.validate('shutdown_database', {'shutdown_timeout': timeout})
    assert not ADMINISTRATION._validate_firebird_service(
        'shutdown_database', {'mode': 'FULL', 'method': 'FORCED',
                              'shutdown_timeout': timeout})


@pytest.mark.parametrize('timeout', [
    -1, 32768, 86400, 2**63, True, False, 1.0, '1', None, [], {},
])
def test_invalid_timeout_never_starts_native_service(timeout):
    server = SimpleNamespace(database=Mock())
    with pytest.raises(RelationalClientError):
        _firebird_service_operation(
            server, 'shutdown_database', '/data/exact.fdb',
            {'shutdown_timeout': timeout}, Mock())
    assert not server.database.mock_calls
    assert ADMINISTRATION._validate_firebird_service(
        'shutdown_database', {'mode': 'FULL', 'method': 'FORCED',
                              'shutdown_timeout': timeout})


@pytest.mark.parametrize('operation,options', [
    ('shutdown_database', {'mode': 'NORMAL'}),
    ('shutdown_database', {'mode': []}),
    ('shutdown_database', {'method': 'INVALID'}),
    ('shutdown_database', {'method': {}}),
    ('bring_online', {'mode': 'FULL'}),
    ('bring_online', {'mode': None}),
    ('bring_online', []), ('unknown', {}), ([], {}),
])
def test_native_availability_rejects_invalid_modes_and_requests(
        operation, options):
    with pytest.raises(RelationalClientError):
        availability.validate(operation, options)


def test_shutdown_form_matches_native_signed_short_limit():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    database = next(item for item in catalog['objects']
                    if item['resource_kind'] == 'database')
    operation = next(item for item in database['operations']
                     if item['operation_id'] == 'shutdown_database')
    field = next(item for item in operation['form']['fields']
                 if item['field_id'] == 'shutdown_timeout')
    assert (field['minimum'], field['maximum']) == (0, 32767)
    assert 'Forced shutdown' in field['help']


@pytest.mark.parametrize('operation', ['shutdown_database', 'bring_online'])
def test_availability_plan_keeps_exact_target_and_warns_about_native_errors(
        operation):
    path = '/data/leading and trailing spaces .fdb '
    compiled = ADMINISTRATION._compile({
        'resource_kind': 'database', 'operation_id': operation,
        'draft': {}, '_provider_route': {'database': path}})
    assert compiled['database'] == path
    assert compiled['warnings'] == [availability.WARNING]
    assert 'before returning a later access error' in compiled['warnings'][0]
    assert 'IGNORE_DB_TRIGGERS' in compiled['warnings'][0]


def test_privilege_evidence_never_exports_arbitrary_native_message():
    native = RuntimeError('Secret=do-not-export; System privilege '
                          'USE_GFIX_UTILITY is missing; path=/private')
    outer = RuntimeError('sanitized operation error')
    outer.__context__ = native
    native.__context__ = outer
    assert missing_privileges(outer) == ['USE_GFIX_UTILITY']
    assert missing_privileges(RuntimeError('System privilege secret-file '
                                           'is missing')) == []
