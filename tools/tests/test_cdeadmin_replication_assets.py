##########################################################################
# CDEadmin Replication Topology project-asset backend validation gates.
##########################################################################

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.workspace.assets import (  # noqa: E402
    ProjectAssetError,
    validate_asset_request,
)


def health(overall='healthy'):
    return {
        'schema': 'cdeadmin.replication-health.v1',
        'overall': overall,
        'dimensions': {
            'connectivity': overall,
            'linkState': overall,
            'quorum': overall,
            'errors': overall,
            'provider': overall,
        },
        'policyAllowsUnknownCritical': False,
        'providerReported': 'ONLINE',
        'nativeDetails': {},
    }


def participant(identity, role, native_role):
    return {
        'schema': 'cdeadmin.replication-participant.v1',
        'id': identity,
        'name': identity,
        'resourceRef': {
            'schema': 'cdeadmin.resource-ref.v1',
            'canonical': f'postgresql://cluster/{identity}',
            'provider': 'postgresql',
        },
        'normalizedRole': role,
        'nativeRole': native_role,
        'nativeState': 'streaming',
        'groupId': 'group-one',
        'health': health(),
        'position': {
            'schema': 'cdeadmin.replication-position.v1',
            'provider': 'postgresql',
            'positionType': 'LSN',
            'rawValue': '0/16B6C50',
            'comparableWithinScope': True,
            'observedAt': '2026-09-11T00:00:00Z',
            'nativeDetails': {},
        },
        'storage': {},
        'connections': {},
        'nativeDetails': {},
    }


def plan_item(identity, action):
    return {
        'id': identity,
        'label': identity,
        'action': action,
        'targetRef': None,
        'parameters': {},
        'evidenceRequirements': {},
        'nativeDetails': {},
    }


def replication_content():
    primary = participant('primary', 'writer_capable', 'primary')
    replica = participant('replica', 'read_only_replica', 'standby')
    topology = {
        'schema': 'cdeadmin.replication-topology.v1',
        'id': 'topology-one',
        'name': 'PostgreSQL streaming replication',
        'scopeRef': {
            'schema': 'cdeadmin.resource-ref.v1',
            'canonical': 'postgresql://cluster',
            'provider': 'postgresql',
        },
        'providerId': 'postgresql',
        'revision': 'provider-revision-1',
        'participants': [primary, replica],
        'groups': [{
            'schema': 'cdeadmin.replica-group.v1',
            'id': 'group-one',
            'name': 'Streaming group',
            'nativeType': 'streaming replication',
            'memberIds': ['primary', 'replica'],
            'quorumState': 'not_applicable',
            'roleMetadata': {},
            'nativeDetails': {},
        }],
        'links': [{
            'schema': 'cdeadmin.replication-link.v1',
            'id': 'link-one',
            'name': 'primary to replica',
            'sourceParticipantId': 'primary',
            'targetParticipantId': 'replica',
            'mechanism': 'physical streaming',
            'nativeState': 'streaming',
            'health': health(),
            'position': None,
            'checkpoint': {},
            'errors': [],
            'nativeDetails': {},
        }],
        'lagSamples': [{
            'schema': 'cdeadmin.replication-lag-sample.v1',
            'id': 'lag-one',
            'linkId': 'link-one',
            'observedAt': '2026-09-11T00:00:00Z',
            'unit': 'bytes',
            'value': 1024,
            'source': 'provider_calculated',
            'calculation': {'method': 'pg_wal_lsn_diff'},
            'nativeDetails': {},
        }],
        'conflictPolicy': {},
        'healthPolicy': {},
        'events': [],
        'nativeDetails': {},
    }
    failover = {
        'schema': 'cdeadmin.replication-failover-plan.v1',
        'id': 'plan-one',
        'name': 'Promote replica',
        'topologyId': 'topology-one',
        'candidateParticipantId': 'replica',
        'targetRole': 'primary',
        'preconditions': [plan_item('quorum', 'check_quorum')],
        'commands': [plan_item('promote', 'pg_promote')],
        'expectedTopology': {'participantRoles': {'replica': 'primary'}},
        'verification': [plan_item('verify', 'verify_primary')],
        'rollback': [plan_item('rollback', 'restore_primary')],
        'dataLossRisk': 'provider-assessed during validation',
        'estimatedRPO': {
            'value': 0,
            'unit': 'bytes',
            'evidence': {'provider': 'postgresql'},
        },
        'estimatedRTO': None,
        'nativeDetails': {},
    }
    return {
        'schema': 'cdeadmin.replication.asset.v1',
        'schemaVersion': 1,
        'moduleId': 'cdeadmin.replication',
        'name': 'Reference topology',
        'description': 'Exact provider-native replication evidence',
        'savedTopologies': [topology],
        'alertPolicies': [{
            'schema': 'cdeadmin.replication-alert-policy.v1',
            'id': 'lag-alert',
            'topologyId': 'topology-one',
            'linkId': 'link-one',
            'metric': 'provider_lag',
            'maximum': 4096,
            'unit': 'bytes',
            'enabled': True,
            'nativeDetails': {},
        }],
        'failoverPlans': [failover],
        'visualLayouts': [{
            'schema': 'cdeadmin.replication-layout.v1',
            'id': 'layout-one',
            'topologyId': 'topology-one',
            'positions': {
                'primary': {'x': 100, 'y': 100},
                'replica': {'x': 300, 'y': 100},
            },
            'nativeDetails': {},
        }],
        'snapshotRefs': [{
            'schema': 'cdeadmin.replication-snapshot-ref.v1',
            'id': 'snapshot-one',
            'topologyId': 'topology-one',
            'capturedAt': '2026-09-11T00:00:00Z',
            'reference': {
                'schema': 'cdeadmin.external-ref.v1',
                'id': 'snapshot://one',
            },
            'nativeDetails': {},
        }],
        'extensions': {},
    }


def replication_asset():
    return {
        'asset_type': 'cdeadmin.replication.v1',
        'schema_name': 'cdeadmin.replication.v1',
        'schema_version': 1,
        'name': 'Reference topology',
        'path': 'replication/reference.json',
        'expected_version': 0,
        'content': replication_content(),
        'metadata': {'moduleId': 'cdeadmin.replication'},
        'dependency_references': [],
        'resource_bindings': [],
        'validation_state': 'valid',
        'validation_details': [],
    }


class ReplicationAssetValidationTest(unittest.TestCase):
    def validate(self, value):
        return validate_asset_request(value, 'project-one', 'replication-one')

    def test_accepts_complete_canonical_asset(self):
        result = self.validate(replication_asset())
        content = json.loads(result['content'])
        self.assertEqual(content['moduleId'], 'cdeadmin.replication')
        self.assertEqual(
            content['savedTopologies'][0]['participants'][1]['nativeRole'],
            'standby',
        )

    def test_rejects_wrong_schema_and_provider_scope(self):
        wrong = replication_asset()
        wrong['content']['schema'] = 'cdeadmin.cdc.asset.v1'
        with self.assertRaisesRegex(ProjectAssetError, 'asset schema'):
            self.validate(wrong)
        mismatch = replication_asset()
        mismatch['content']['savedTopologies'][0]['providerId'] = 'mysql'
        with self.assertRaisesRegex(ProjectAssetError, 'match its scope'):
            self.validate(mismatch)

    def test_rejects_non_normative_roles_and_health_states(self):
        role = replication_asset()
        role['content']['savedTopologies'][0]['participants'][0][
            'normalizedRole'] = 'primary'
        with self.assertRaisesRegex(ProjectAssetError, 'normalized role'):
            self.validate(role)
        state = replication_asset()
        state['content']['savedTopologies'][0]['participants'][0][
            'health']['overall'] = 'online'
        with self.assertRaisesRegex(ProjectAssetError, 'overall state'):
            self.validate(state)

    def test_rejects_dangling_topology_relationships(self):
        for field, replacement, message in (
                ('groupId', 'missing', 'unknown group'),
                ('resourceRef', {
                    'schema': 'cdeadmin.credential-ref.v1',
                    'id': 'forbidden',
                }, 'schema is unsupported')):
            value = replication_asset()
            value['content']['savedTopologies'][0]['participants'][0][
                field] = replacement
            with self.assertRaisesRegex(ProjectAssetError, message):
                self.validate(value)
        link = replication_asset()
        link['content']['savedTopologies'][0]['links'][0][
            'targetParticipantId'] = 'missing'
        with self.assertRaisesRegex(ProjectAssetError, 'unknown participant'):
            self.validate(link)

    def test_rejects_fabricated_or_invalid_lag_values(self):
        for value in (-1, float('inf'), True, '10'):
            asset = replication_asset()
            asset['content']['savedTopologies'][0]['lagSamples'][0][
                'value'] = value
            with self.assertRaisesRegex(
                    ProjectAssetError, 'finite non-negative'):
                self.validate(asset)

    def test_requires_complete_failover_runbook(self):
        for collection in (
                'preconditions', 'commands', 'verification', 'rollback'):
            value = replication_asset()
            value['content']['failoverPlans'][0][collection] = []
            with self.assertRaisesRegex(ProjectAssetError, 'require'):
                self.validate(value)

    def test_rejects_unknown_failover_candidate_and_topology(self):
        candidate = replication_asset()
        candidate['content']['failoverPlans'][0][
            'candidateParticipantId'] = 'missing'
        with self.assertRaisesRegex(ProjectAssetError, 'unknown candidate'):
            self.validate(candidate)
        topology = replication_asset()
        topology['content']['alertPolicies'][0]['topologyId'] = 'missing'
        with self.assertRaisesRegex(ProjectAssetError, 'unknown topology'):
            self.validate(topology)

    def test_rejects_layout_for_unknown_participant(self):
        value = replication_asset()
        value['content']['visualLayouts'][0]['positions']['missing'] = {
            'x': 1,
            'y': 1,
        }
        with self.assertRaisesRegex(ProjectAssetError, 'layout references'):
            self.validate(value)

    def test_rejects_duplicate_ids(self):
        value = replication_asset()
        value['content']['savedTopologies'][0]['participants'].append(
            copy.deepcopy(
                value['content']['savedTopologies'][0]['participants'][0]))
        with self.assertRaisesRegex(ProjectAssetError, 'duplicate'):
            self.validate(value)

    def test_rejects_raw_credentials_anywhere(self):
        value = replication_asset()
        value['content']['savedTopologies'][0]['nativeDetails'][
            'password'] = 'forbidden'
        with self.assertRaisesRegex(ProjectAssetError, 'secret field'):
            self.validate(value)


if __name__ == '__main__':
    unittest.main()
