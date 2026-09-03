"""Non-networking contract fixture for the upstream acceptance runner."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tools.cdeadmin_upstream_acceptance import (
    EXPECTED_AUTHORITIES,
    make_evidence,
)


class ContractFixtureAdapter:
    """Exercise runner contracts without claiming a live test result."""

    def __init__(self, config):
        self.fixture_id = config['fixture_id']
        self.calls = []

    def candidate_manifest(self):
        return {
            'schema': 'cdeadmin.upstream-candidate-manifest.v1',
            'candidate_protocol_version': '1.0.0',
            'candidate_id': self.fixture_id,
            'candidate_class': 'contract_fixture',
            'production_candidate': False,
            'execution_backend': 'none',
            'network_enabled': False,
            'authority_invariants': dict(EXPECTED_AUTHORITIES),
            'dependencies': {},
        }

    def execute_case(self, case):
        self.calls.append(case['case_id'])
        observed = datetime.now(timezone.utc)
        return make_evidence({
            'schema': 'cdeadmin.acceptance-case-evidence.v1',
            'case_id': case['case_id'],
            'status': 'fixture_observed',
            'observed_at': observed.isoformat(),
            'expires_at': (observed + timedelta(days=1)).isoformat(),
            'assertions': {
                assertion: True for assertion in case['assertions']
            },
            'runtime_identity': {'execution_backend': 'none'},
            'diagnostics': [
                'contract fixture: no driver, CDE, donor, or runtime used'
            ],
        })


def create_acceptance_adapter(config):
    return ContractFixtureAdapter(config)
