##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

import copy
import json

import pytest

from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_roles,
)


def inputs():
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    evidence = {
        'status': 'passed', 'engine_version': '5.0.4.1812',
        'role_memberships_replayed': 3,
        'temporary_user_removed': True, 'temporary_role_removed': True,
        'temporary_owned_role_removed': True, 'temporary_table_removed': True,
        'role_dialect_task_evidence': {
            'visual_admin.role.' + operation: {
                'live_execution': 'passed',
                'command_preview': {'statements': [{
                    'source': source, 'parameter_count': 0,
                }]},
            } for operation, source in (
                ('grant', 'GRANT "R" TO USER "U"'),
                ('revoke', 'REVOKE "R" FROM USER "U"'),
            )
        },
    }
    return document, evidence


def test_supplement_preserves_prior_evidence_and_is_idempotent():
    original, evidence = inputs()
    before = copy.deepcopy(original)
    result = supplement_roles(original, evidence, 'a' * 64, 'evidence.json')
    assert original == before
    assert len(result['task_templates']) == 62
    assert result == supplement_roles(
        result, evidence, 'a' * 64, 'evidence.json')
    assert all(record in result['proof_records'] for record in
               original['proof_records'] if 'role-membership' not in
               record['evidence_id'])


@pytest.mark.parametrize('key,value', [
    ('status', 'failed'), ('engine_version', '4.0.6'),
    ('role_memberships_replayed', 2), ('temporary_user_removed', False),
    ('temporary_role_removed', False), ('temporary_owned_role_removed', False),
    ('temporary_table_removed', False), ('role_dialect_task_evidence', {}),
])
def test_invalid_or_unclean_evidence_cannot_activate_tasks(key, value):
    document, evidence = inputs()
    evidence[key] = value
    with pytest.raises(ValueError):
        supplement_roles(document, evidence, 'a' * 64, 'evidence.json')


def test_failed_task_cannot_be_admitted():
    document, evidence = inputs()
    evidence['role_dialect_task_evidence']['visual_admin.role.grant'][
        'live_execution'] = 'failed'
    with pytest.raises(ValueError):
        supplement_roles(document, evidence, 'a' * 64, 'evidence.json')
