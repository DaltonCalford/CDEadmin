##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Project ownership, asset revision, validation, API and migration gates."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.workspace.assets import (  # noqa: E402
    APP_EXTENSION_KEY,
    ProjectAssetConflict,
    ProjectAssetError,
    ProjectAssetForbidden,
    ProjectAssetNotFound,
    ProjectAssetService,
    _register_routes,
    service_for_app,
    validate_asset_request,
)


MIGRATION_PATH = ROOT / 'web/migrations/versions/cde_project_assets_v1_.py'

try:
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    MIGRATION_DEPENDENCIES = True
except ImportError:
    MIGRATION_DEPENDENCIES = False


class Row:
    def __init__(self, **values):
        now = datetime(2026, 9, 11, 9, 0, 0)
        self.created_at = now
        self.updated_at = now
        self.__dict__.update(values)


class MemoryRepository:
    def __init__(self):
        self.project_rows = []
        self.asset_rows = []
        self.revision_rows = []
        self.member_rows = []
        self.commits = 0
        self.rollbacks = 0

    def project(self, owner_id, project_key):
        return self._find(
            self.project_rows, user_id=owner_id, project_key=project_key
        )

    def project_by_key(self, project_key):
        return self._find(self.project_rows, project_key=project_key)

    def accessible_project(self, user_id, role_ids, project_key):
        project = self.project(user_id, project_key)
        if project:
            return project, 'owner'
        levels = {'viewer': 1, 'editor': 2, 'manager': 3}
        candidates = [
            member for member in self.member_rows
            if member.project.project_key == project_key and (
                (member.principal_type == 'user' and
                 member.principal_id == user_id) or
                (member.principal_type == 'role' and
                 member.principal_id in role_ids)
            )
        ]
        if not candidates:
            return None, None
        member = max(candidates, key=lambda item: levels[item.access_level])
        return member.project, member.access_level

    def projects(self, user_id, role_ids):
        result = [(project, 'owner') for project in self.project_rows
                  if project.user_id == user_id]
        owned = {project.id for project, _access in result}
        levels = {'viewer': 1, 'editor': 2, 'manager': 3}
        shared = {}
        for member in self.member_rows:
            matches = (
                member.principal_type == 'user' and
                member.principal_id == user_id
            ) or (
                member.principal_type == 'role' and
                member.principal_id in role_ids
            )
            if not matches or member.project_id in owned:
                continue
            prior = shared.get(member.project_id)
            if (prior is None or levels[member.access_level] >
                    levels[prior.access_level]):
                shared[member.project_id] = member
        return result + [(member.project, member.access_level)
                         for member in shared.values()]

    def asset(self, project_id, asset_key):
        return self._find(
            self.asset_rows, project_id=project_id, asset_key=asset_key
        )

    @staticmethod
    def new_project(**values):
        return Row(assets=[], members=[], **values)

    @staticmethod
    def new_asset(**values):
        return Row(revisions=[], **values)

    @staticmethod
    def new_revision(**values):
        return Row(**values)

    @staticmethod
    def new_member(**values):
        return Row(**values)

    def add(self, row):
        if hasattr(row, 'project_key'):
            self.project_rows.append(row)
        elif hasattr(row, 'asset_key'):
            project = self._find(self.project_rows, id=row.project_id)
            row.project = project
            project.assets.append(row)
            self.asset_rows.append(row)
        elif hasattr(row, 'asset_id'):
            asset = self._find(self.asset_rows, id=row.asset_id)
            row.asset = asset
            asset.revisions.append(row)
            self.revision_rows.append(row)
        elif hasattr(row, 'principal_type'):
            project = self._find(self.project_rows, id=row.project_id)
            row.project = project
            project.members.append(row)
            self.member_rows.append(row)

    def delete(self, row):
        if hasattr(row, 'project_key'):
            self.project_rows.remove(row)
            for asset in list(row.assets):
                self.delete(asset)
            for member in list(row.members):
                self.delete(member)
        elif hasattr(row, 'asset_key'):
            self.asset_rows.remove(row)
            row.project.assets.remove(row)
            for revision in list(row.revisions):
                self.revision_rows.remove(revision)
        elif hasattr(row, 'principal_type'):
            self.member_rows.remove(row)
            row.project.members.remove(row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    @staticmethod
    def _find(rows, **values):
        return next((row for row in rows if all(
            getattr(row, key, None) == value for key, value in values.items()
        )), None)


def generic_asset(version=0, **updates):
    value = {
        'asset_type': 'query',
        'name': 'Revenue query',
        'path': 'queries/revenue.json',
        'schema_name': 'cdeadmin.query.v1',
        'schema_version': 1,
        'expected_version': version,
        'content': {'language': 'sql', 'source_reference': 'query-17'},
        'metadata': {'description': 'Quarterly revenue'},
        'dependency_references': [{'asset_id': 'model-1'}],
        'resource_bindings': [{'resource_id': 'table:revenue'}],
        'validation_state': 'valid',
        'validation_details': [],
    }
    value.update(updates)
    return value


def ddn_asset(version=0, source='diagram example {}'):
    return {
        'schema': 'cdeadmin.ddn-asset.v1',
        'assetRef': {
            'schemaVersion': 1,
            'projectId': 'project-one',
            'assetId': 'diagram-one',
            'assetType': 'ddn-workspace',
            'assetVersion': version,
            'path': 'diagrams/example.ddn',
            'displayName': 'Example diagram',
        },
        'snapshot': {
            'format': 'ddn-workspace@1',
            'files': {'example.ddn': source},
            'entry': 'example.ddn',
            'view': 'example',
        },
    }


def schema_compare_asset(version=0, *, change_plan=None):
    reference = {
        'schema': 'cdeadmin.resource-ref.v1',
        'provider': 'firebird', 'connection': 'local', 'scope': '/',
        'kind': 'database', 'nativeIdentity': 'demo.fdb',
        'canonical': 'cde-resource://firebird/local/%2F/database/demo.fdb',
    }
    return {
        'asset_type': 'cdeadmin.schema_compare.v1',
        'name': 'Firebird schema comparison',
        'path': 'comparisons/firebird.json',
        'schema_name': 'cdeadmin.schema_compare.v1',
        'schema_version': 1,
        'expected_version': version,
        'content': {
            'schema': 'cdeadmin.schema-compare.asset.v1',
            'schemaVersion': 1, 'moduleId': 'cdeadmin.schema_compare',
            'leftRef': reference, 'rightRef': {
                **reference,
                'canonical': (
                    'cde-resource://firebird/local/%2F/database/target.fdb'
                ),
                'nativeIdentity': 'target.fdb',
            },
            'options': {}, 'acceptedMappings': [], 'ignoredDiffs': [],
            'comparisonResultRef': None, 'changePlan': change_plan,
        },
        'metadata': {}, 'dependency_references': [],
        'resource_bindings': [reference], 'validation_state': 'valid',
        'validation_details': [],
    }


def lineage_asset(version=0):
    reference = {
        'schema': 'cdeadmin.resource-ref.v1',
        'provider': 'firebird',
        'canonical': 'cde-resource://firebird/local/%2F/table/SOURCE',
    }
    evidence = {
        'schema': 'cdeadmin.lineage-evidence.v1',
        'id': 'evidence-one', 'origin': 'user_curated',
        'confidence': 1, 'capturedAt': '2026-09-11T12:00:00Z',
        'validFrom': None, 'validTo': None, 'stale': False,
        'providerVersion': '5.0.4', 'reference': reference,
        'details': {'review': 'accepted'},
    }
    edge = {
        'schema': 'cdeadmin.lineage-edge.v1', 'id': 'edge-one',
        'from': 'source', 'to': 'target', 'type': 'derives',
        'origin': 'user_curated', 'confidence': 1,
        'validFrom': None, 'validTo': None, 'evidence': [evidence],
        'fieldLineage': [{
            'schema': 'cdeadmin.field-lineage.v1', 'id': 'field-one',
            'sourceField': 'SOURCE.ID', 'targetField': 'TARGET.ID',
            'transformation': 'DIRECT_IDENTITY', 'expressionRef': None,
            'evidenceIds': ['evidence-one'], 'nativeDetails': {},
        }], 'presentationState': 'confirmed', 'suppressed': False,
        'nativeDetails': {},
    }
    return {
        'asset_type': 'cdeadmin.lineage.v1',
        'name': 'Firebird lineage', 'path': 'lineage/firebird.json',
        'schema_name': 'cdeadmin.lineage.v1', 'schema_version': 1,
        'expected_version': version,
        'content': {
            'schema': 'cdeadmin.lineage.asset.v1', 'schemaVersion': 1,
            'moduleId': 'cdeadmin.lineage', 'scopeRefs': [reference],
            'sourcePolicies': [{
                'id': 'firebird-native',
                'sourcePriority': ['provider_declared', 'user_curated'],
                'inferenceEnabled': True, 'retentionDays': 30,
                'providerId': 'firebird',
            }], 'savedFilters': {'origin': 'user_curated'},
            'layoutPreferences': {'mode': 'graph'},
            'curatedEdges': [edge],
            'suppressedInferenceRules': ['source|target|derives'],
            'snapshotRefs': [{
                'schema': 'cdeadmin.external-ref.v1',
                'id': 'lineage-snapshot:before',
            }],
        },
        'metadata': {}, 'dependency_references': [],
        'resource_bindings': [reference], 'validation_state': 'valid',
        'validation_details': [],
    }


def quality_asset(version=0):
    reference = {
        'schema': 'cdeadmin.resource-ref.v1',
        'provider': 'firebird',
        'canonical': 'cde-resource://firebird/local/%2F/table/ORDERS',
    }
    sample_reference = {
        'schema': 'cdeadmin.external-ref.v1',
        'id': 'profile-sample:orders:42',
    }
    return {
        'asset_type': 'cdeadmin.quality.v1',
        'name': 'Orders quality', 'path': 'quality/orders.json',
        'schema_name': 'cdeadmin.quality.v1', 'schema_version': 1,
        'expected_version': version,
        'content': {
            'schema': 'cdeadmin.quality.asset.v1', 'schemaVersion': 1,
            'moduleId': 'cdeadmin.quality', 'name': 'Orders quality',
            'owner': 'quality-team', 'tags': ['orders'],
            'rules': [{
                'schema': 'cdeadmin.quality-rule.v1',
                'id': 'rule-one', 'name': 'Order ID is populated',
                'type': 'not_null', 'dimension': 'completeness',
                'scope': None, 'parameters': {'column': 'ID'},
                'threshold': {'operator': '>=', 'value': 0.99},
                'ratio': True, 'severity': 'critical',
                'evaluationMode': 'exact', 'enabled': True,
                'generatedSuggestion': True,
                'sourceSampleRef': sample_reference,
                'sourceRevision': 'transaction:42',
                'actionIds': ['block-publish'],
                'documentation': 'Primary keys cannot be null.',
                'nativeDetails': {'catalog': 'RDB$RELATION_FIELDS'},
            }],
            'parameters': {'checkpoint': 'publish'},
            'defaultScope': {
                'schema': 'cdeadmin.quality-data-slice.v1',
                'id': 'orders-slice', 'resourceRef': reference,
                'partition': None, 'window': None,
                'filter': {'STATUS': 'OPEN'},
                'samplePolicy': {'mode': 'random_n', 'limit': 500},
                'nativeDetails': {'relationType': 0},
            },
            'severityPolicy': {
                'weights': {'info': 1, 'warning': 2, 'critical': 5},
            },
            'schedule': {'cron': '0 2 * * *', 'timezone': 'UTC'},
            'actions': [{
                'schema': 'cdeadmin.quality-action.v1',
                'id': 'block-publish', 'type': 'block',
                'label': 'Block publish', 'severities': ['critical'],
                'enabled': True, 'parameters': {'checkpoint': 'publish'},
            }],
            'baselineRefs': [{
                'schema': 'cdeadmin.external-ref.v1',
                'id': 'quality-baseline:orders',
            }],
        },
        'metadata': {'moduleId': 'cdeadmin.quality'},
        'dependency_references': [], 'resource_bindings': [reference],
        'validation_state': 'valid', 'validation_details': [],
    }


def etl_asset(version=0):
    source_ref = {
        'schema': 'cdeadmin.resource-ref.v1', 'provider': 'firebird',
        'canonical': 'cde-resource://firebird/local/database/demo/SOURCE',
    }
    sink_ref = {
        'schema': 'cdeadmin.resource-ref.v1', 'provider': 'mongodb',
        'canonical': 'cde-resource://mongodb/local/database/demo/TARGET',
    }

    def field():
        return {
            'schema': 'cdeadmin.etl-schema-field.v1', 'id': 'id',
            'name': 'ID', 'nativeType': 'INTEGER',
            'semanticType': 'integer', 'nullable': False,
            'precision': None, 'scale': None, 'timezone': None,
            'encoding': None, 'path': None, 'nativeDetails': {},
        }

    def port(port_id, direction):
        return {
            'schema': 'cdeadmin.etl-port.v1', 'id': port_id,
            'name': port_id, 'direction': direction, 'mode': 'batch',
            'schemaState': 'known', 'schemaRef': None,
            'fields': [field()], 'cardinality': None, 'ordering': [],
            'partitioning': {}, 'watermark': {}, 'nativeDetails': {},
        }

    source = {
        'schema': 'cdeadmin.etl-node.v1', 'id': 'source',
        'name': 'Firebird source', 'kind': 'source', 'config': {},
        'resourceRef': source_ref, 'ports': [port('out', 'output')],
        'capabilityRequirements': ['batch_read'],
        'executionPreference': 'source_pushdown', 'errorRoute': None,
        'checkpointEnabled': True, 'nativeDetails': {},
    }
    sink = {
        'schema': 'cdeadmin.etl-node.v1', 'id': 'sink',
        'name': 'MongoDB sink', 'kind': 'sink', 'config': {},
        'resourceRef': sink_ref, 'ports': [port('in', 'input')],
        'capabilityRequirements': ['batch_write'],
        'executionPreference': 'target_pushdown', 'errorRoute': {
            'schema': 'cdeadmin.etl-error-route.v1', 'action': 'stop',
            'maximumAttempts': 0, 'targetRef': None,
            'retryPolicy': {}, 'nativeDetails': {},
        }, 'checkpointEnabled': False, 'nativeDetails': {},
    }
    return {
        'asset_type': 'cdeadmin.etl.v1',
        'name': 'Cross-engine orders', 'path': 'etl/orders.json',
        'schema_name': 'cdeadmin.etl.v1', 'schema_version': 1,
        'expected_version': version,
        'content': {
            'schema': 'cdeadmin.etl.asset.v1', 'schemaVersion': 1,
            'moduleId': 'cdeadmin.etl', 'name': 'Cross-engine orders',
            'description': 'Firebird to MongoDB', 'mode': 'batch',
            'parameters': [{
                'schema': 'cdeadmin.etl-parameter.v1', 'id': 'auth',
                'name': 'Authentication', 'type': 'credential',
                'required': True, 'secret': True, 'default': None,
                'credentialRef': {
                    'schema': 'cdeadmin.credential-ref.v1',
                    'scheme': 'keyring', 'id': 'etl-development',
                }, 'description': '', 'nativeDetails': {},
            }],
            'nodes': [source, sink],
            'edges': [{
                'schema': 'cdeadmin.etl-edge.v1', 'id': 'flow',
                'fromNodeId': 'source', 'fromPort': 'out',
                'toNodeId': 'sink', 'toPort': 'in',
                'mappingPolicy': 'explicit', 'mappings': [{
                    'schema': 'cdeadmin.etl-schema-mapping.v1',
                    'id': 'id', 'source': 'id', 'target': 'id',
                    'sourceNativeType': 'INTEGER',
                    'semanticType': 'integer', 'targetNativeType': 'long',
                    'conversion': 'widen', 'nullPolicy': 'preserve',
                    'nullable': False, 'precision': None, 'scale': None,
                    'timezone': None, 'encoding': None, 'lossy': False,
                    'lossAcknowledged': False, 'nativeDetails': {},
                }], 'deliveryGuarantee': 'at_least_once',
                'partitioning': {}, 'ordering': {}, 'nativeDetails': {},
            }],
            'deployments': [{
                'schema': 'cdeadmin.etl-deployment.v1', 'id': 'dev',
                'name': 'Development', 'environment': 'development',
                'bindings': [{
                    'schema': 'cdeadmin.etl-deployment-binding.v1',
                    'id': 'source-dev', 'nodeId': 'source',
                    'resourceRef': source_ref, 'nativeDetails': {},
                }], 'parameterBindings': {
                    'auth': {
                        'schema': 'cdeadmin.credential-ref.v1',
                        'scheme': 'keyring', 'id': 'etl-development',
                    },
                }, 'resourceLimits': {}, 'nativeDetails': {},
            }],
            'schedules': [{
                'schema': 'cdeadmin.etl-schedule.v1', 'id': 'nightly',
                'name': 'Nightly', 'enabled': True, 'trigger': 'cron',
                'expression': '0 1 * * *', 'timezone': 'UTC',
                'deploymentId': 'dev', 'dependencyRefs': [],
                'parameters': {}, 'nativeDetails': {},
            }],
            'tests': [{
                'id': 'minimum-count', 'name': 'Minimum count',
                'definition': {'minimum': 1},
            }],
            'visualLayout': {'source': {'x': 20, 'y': 30}},
            'extensions': {},
        },
        'metadata': {'moduleId': 'cdeadmin.etl', 'mode': 'batch'},
        'dependency_references': [],
        'resource_bindings': [source_ref, sink_ref],
        'validation_state': 'valid', 'validation_details': [],
    }


def contract_asset(version=0):
    resource = {
        'schema': 'cdeadmin.resource-ref.v1', 'provider': 'mongodb',
        'canonical': (
            'cde-resource://mongodb/local/database/sales/collection/orders'
        ),
    }
    quality_reference = {
        'schema': 'cdeadmin.asset-ref.v1',
        'projectId': 'project-one', 'assetId': 'quality-one',
    }
    return {
        'asset_type': 'cdeadmin.contract.v1',
        'name': 'Orders contract', 'path': 'contracts/orders.json',
        'schema_name': 'cdeadmin.contract.v1', 'schema_version': 1,
        'expected_version': version,
        'content': {
            'schema': 'cdeadmin.contract.asset.v1', 'schemaVersion': 1,
            'moduleId': 'cdeadmin.contract', 'contractVersion': '1.0.0',
            'status': 'draft', 'name': 'Orders contract', 'domain': 'sales',
            'description': 'Governed order documents',
            'elements': [{
                'schema': 'cdeadmin.contract-element.v1', 'id': 'orders',
                'name': 'Orders', 'logicalType': 'document',
                'parentId': None, 'description': 'Order document',
                'required': True, 'classification': 'internal',
                'constraints': {}, 'physicalDefinition': {},
                'authoritativeDefinitionRefs': [], 'extensions': {},
            }],
            'bindings': [{
                'schema': 'cdeadmin.contract-binding.v1',
                'id': 'orders-production', 'elementId': 'orders',
                'targetRef': resource, 'environment': 'production',
                'bindingStatus': 'observed',
                'observedRevision': 'catalog:42',
                'nativeDetails': {'collectionType': 'timeseries'},
            }],
            'qualityObligations': [{
                'schema': 'cdeadmin.contract-quality-obligation.v1',
                'id': 'quality-orders', 'elementId': 'orders',
                'qualityRef': quality_reference, 'importedDefinition': None,
                'severity': 'critical', 'threshold': {'maximumFailures': 0},
                'description': 'No critical violations', 'extensions': {},
            }],
            'sla': [{
                'schema': 'cdeadmin.contract-service-level.v1',
                'id': 'freshness', 'measure': 'freshness', 'target': 5,
                'comparison': '<=', 'unit': 'minutes',
                'window': {'rolling': '15m'}, 'elementId': 'orders',
                'description': 'Freshness commitment', 'extensions': {},
            }],
            'team': [{
                'schema': 'cdeadmin.contract-team-role.v1',
                'id': 'producer', 'name': 'Order producer',
                'principalRefs': [{
                    'schema': 'cdeadmin.external-ref.v1', 'id': 'team:sales',
                }], 'responsibilities': ['publish'],
                'accessExpectations': [], 'support': {}, 'extensions': {},
            }],
            'roles': [],
            'servers': [{
                'schema': 'cdeadmin.contract-server-binding.v1',
                'id': 'orders-server', 'providerId': 'mongodb',
                'environment': 'production', 'interface': 'mongodb-wire',
                'resourceRef': resource, 'apiRef': None, 'nativeDetails': {},
            }],
            'authoritativeDefinitions': [{
                'schema': 'cdeadmin.contract-authoritative-definition.v1',
                'id': 'glossary', 'type': 'business_glossary',
                'uri': 'https://catalog.invalid/orders', 'assetRef': None,
                'description': 'Order vocabulary', 'extensions': {},
            }],
            'extensions': {
                'org.opendatacontractstandard.import': {
                    'apiVersion': 'v3.1.0', 'kind': 'DataContract',
                    'unknownTopLevel': {},
                },
            },
        },
        'metadata': {'moduleId': 'cdeadmin.contract'},
        'dependency_references': [quality_reference],
        'resource_bindings': [resource], 'validation_state': 'valid',
        'validation_details': [],
    }


class ProjectAssetServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        self.service = ProjectAssetService(self.repository)
        self.service.create_project(7, 'project-one', {
            'name': 'Project One', 'description': 'Test assets'
        })

    def test_project_lifecycle_is_optimistic_and_globally_identified(self):
        with self.assertRaises(ProjectAssetConflict):
            self.service.create_project(8, 'project-one', {'name': 'Other'})
        updated = self.service.update_project(7, (), 'project-one', {
            'expected_revision': 0, 'name': 'Renamed',
            'source_control_eligible': False,
        })
        self.assertEqual('Renamed', updated['name'])
        self.assertEqual(1, updated['revision'])
        self.assertFalse(updated['source_control_eligible'])
        with self.assertRaises(ProjectAssetConflict):
            self.service.update_project(7, (), 'project-one', {
                'expected_revision': 0, 'name': 'Stale'
            })
        deleted = self.service.delete_project(7, (), 'project-one', 1)
        self.assertEqual(1, deleted['deleted_revision'])
        self.assertEqual([], self.repository.project_rows)

    def test_asset_create_update_history_and_delete_are_atomic(self):
        created = self.service.save_asset(
            7, (), 'project-one', 'query-one', generic_asset()
        )
        self.assertEqual(1, created['version'])
        self.assertEqual('owner', created['access'])
        self.assertEqual('table:revenue',
                         created['resource_bindings'][0]['resource_id'])
        updated = self.service.save_asset(
            7, (), 'project-one', 'query-one',
            generic_asset(1, content={'source_reference': 'query-18'})
        )
        self.assertEqual(2, updated['version'])
        with self.assertRaises(ProjectAssetConflict):
            self.service.save_asset(
                7, (), 'project-one', 'query-one', generic_asset(1)
            )
        historical = self.service.get_asset(
            7, (), 'project-one', 'query-one', 1
        )
        self.assertTrue(historical['historical'])
        self.assertEqual('query-17', historical['content']['source_reference'])
        revisions = self.service.list_revisions(
            7, (), 'project-one', 'query-one'
        )
        self.assertEqual([2, 1], [item['version'] for item in revisions])
        self.assertNotIn('content', revisions[0])
        self.service.delete_asset(7, (), 'project-one', 'query-one', 2)
        self.assertEqual([], self.repository.asset_rows)
        self.assertEqual([], self.repository.revision_rows)

    def test_ddn_payload_round_trips_as_authoritative_source(self):
        created = self.service.save_asset(
            7, (), 'project-one', 'diagram-one', ddn_asset()
        )
        self.assertEqual('ddn-workspace', created['asset_type'])
        self.assertEqual('example.ddn', created['content']['entry'])
        self.assertEqual('diagram example {}',
                         created['content']['files']['example.ddn'])
        self.assertEqual(1, created['assetRef']['assetVersion'])
        saved = self.service.save_asset(
            7, (), 'project-one', 'diagram-one',
            ddn_asset(1, 'diagram example { node n {} }')
        )
        self.assertEqual(2, saved['assetRef']['assetVersion'])
        self.assertIn('node n', saved['content']['files']['example.ddn'])

    def test_lineage_asset_round_trips_with_optimistic_revisions(self):
        created = self.service.save_asset(
            7, (), 'project-one', 'lineage-one', lineage_asset()
        )
        self.assertEqual(1, created['version'])
        self.assertEqual(
            'cdeadmin.lineage.asset.v1', created['content']['schema']
        )
        update = lineage_asset(1)
        update['content']['savedFilters']['origin'] = 'observed_trace'
        saved = self.service.save_asset(
            7, (), 'project-one', 'lineage-one', update
        )
        self.assertEqual(2, saved['version'])
        historical = self.service.get_asset(
            7, (), 'project-one', 'lineage-one', 1
        )
        self.assertEqual(
            'user_curated', historical['content']['savedFilters']['origin']
        )
        with self.assertRaises(ProjectAssetConflict):
            self.service.save_asset(
                7, (), 'project-one', 'lineage-one', lineage_asset(1)
            )

    def test_quality_asset_round_trips_with_optimistic_revisions(self):
        created = self.service.save_asset(
            7, (), 'project-one', 'quality-one', quality_asset()
        )
        self.assertEqual(1, created['version'])
        self.assertEqual(
            'cdeadmin.quality.asset.v1', created['content']['schema']
        )
        self.assertEqual(
            'random_n', created['content']['defaultScope'][
                'samplePolicy'
            ]['mode']
        )
        update = quality_asset(1)
        update['content']['rules'][0]['severity'] = 'warning'
        saved = self.service.save_asset(
            7, (), 'project-one', 'quality-one', update
        )
        self.assertEqual(2, saved['version'])
        historical = self.service.get_asset(
            7, (), 'project-one', 'quality-one', 1
        )
        self.assertEqual(
            'critical', historical['content']['rules'][0]['severity']
        )
        with self.assertRaises(ProjectAssetConflict):
            self.service.save_asset(
                7, (), 'project-one', 'quality-one', quality_asset(1)
            )

    def test_contract_asset_round_trips_with_optimistic_revisions(self):
        created = self.service.save_asset(
            7, (), 'project-one', 'contract-one', contract_asset()
        )
        self.assertEqual(1, created['version'])
        self.assertEqual(
            'cdeadmin.contract.asset.v1', created['content']['schema']
        )
        self.assertEqual(
            'document', created['content']['elements'][0]['logicalType']
        )
        update = contract_asset(1)
        update['content']['description'] = 'Revised contract'
        saved = self.service.save_asset(
            7, (), 'project-one', 'contract-one', update
        )
        self.assertEqual(2, saved['version'])
        historical = self.service.get_asset(
            7, (), 'project-one', 'contract-one', 1
        )
        self.assertEqual(
            'Governed order documents', historical['content']['description']
        )
        with self.assertRaises(ProjectAssetConflict):
            self.service.save_asset(
                7, (), 'project-one', 'contract-one', contract_asset(1)
            )

    def test_etl_asset_round_trips_with_optimistic_revisions(self):
        created = self.service.save_asset(
            7, (), 'project-one', 'etl-one', etl_asset()
        )
        self.assertEqual(1, created['version'])
        self.assertEqual('cdeadmin.etl.asset.v1', created['content']['schema'])
        self.assertTrue(created['content']['parameters'][0]['secret'])
        update = etl_asset(1)
        update['content']['description'] = 'Revised pipeline'
        saved = self.service.save_asset(
            7, (), 'project-one', 'etl-one', update
        )
        self.assertEqual(2, saved['version'])
        historical = self.service.get_asset(
            7, (), 'project-one', 'etl-one', 1
        )
        self.assertEqual(
            'Firebird to MongoDB', historical['content']['description']
        )
        with self.assertRaises(ProjectAssetConflict):
            self.service.save_asset(
                7, (), 'project-one', 'etl-one', etl_asset(1)
            )

    def test_contract_asset_rejects_cycles_dangling_links_and_secrets(self):
        invalid = contract_asset()
        invalid['content']['elements'][0]['parentId'] = 'orders'
        with self.assertRaisesRegex(ProjectAssetError, 'parent itself'):
            validate_asset_request(invalid, 'project-one', 'contract-one')
        invalid = contract_asset()
        invalid['content']['bindings'][0]['elementId'] = 'missing'
        with self.assertRaisesRegex(ProjectAssetError, 'unknown element'):
            validate_asset_request(invalid, 'project-one', 'contract-one')
        invalid = contract_asset()
        invalid['content']['extensions'] = {'nested': {'accessToken': 'x'}}
        with self.assertRaisesRegex(ProjectAssetError, 'secret field'):
            validate_asset_request(invalid, 'project-one', 'contract-one')

    def test_membership_enforces_viewer_editor_manager_and_owner(self):
        self.service.set_member(7, (), 'project-one', {
            'principal_type': 'user', 'principal_id': 8,
            'access_level': 'viewer',
        })
        self.service.set_member(7, (), 'project-one', {
            'principal_type': 'role', 'principal_id': 50,
            'access_level': 'editor',
        })
        listed = self.service.list_projects(8, (50,))
        self.assertEqual('editor', listed[0]['access'])
        self.assertEqual([], self.service.project_state(
            8, (), 'project-one'
        )['members'])
        with self.assertRaises(ProjectAssetForbidden):
            self.service.save_asset(
                8, (), 'project-one', 'query-one', generic_asset()
            )
        self.service.save_asset(
            8, (50,), 'project-one', 'query-one', generic_asset()
        )
        with self.assertRaises(ProjectAssetForbidden):
            self.service.set_member(8, (50,), 'project-one', {
                'principal_type': 'user', 'principal_id': 9,
                'access_level': 'viewer',
            })
        removed = self.service.remove_member(
            7, (), 'project-one', 'user', 8
        )
        self.assertTrue(removed['removed'])
        with self.assertRaises(ProjectAssetNotFound):
            self.service.project_state(8, (), 'project-one')

    def test_missing_resources_and_invalid_versions_are_precise(self):
        with self.assertRaises(ProjectAssetNotFound):
            self.service.get_asset(7, (), 'project-one', 'missing')
        with self.assertRaises(ProjectAssetConflict):
            self.service.save_asset(
                7, (), 'project-one', 'query-one', generic_asset(2)
            )
        self.service.save_asset(
            7, (), 'project-one', 'query-one', generic_asset()
        )
        with self.assertRaises(ProjectAssetNotFound):
            self.service.get_asset(
                7, (), 'project-one', 'query-one', 99
            )
        with self.assertRaises(ProjectAssetConflict):
            self.service.delete_asset(
                7, (), 'project-one', 'query-one', 0
            )


class ProjectAssetValidationTests(unittest.TestCase):
    def test_generic_and_ddn_contracts_accept_complete_safe_payloads(self):
        generic = validate_asset_request(
            generic_asset(), 'project-one', 'query-one'
        )
        self.assertEqual('query', generic['asset_type'])
        diagram = validate_asset_request(
            ddn_asset(), 'project-one', 'diagram-one'
        )
        self.assertEqual('cdeadmin.ddn-asset.v1', diagram['schema_name'])

    def test_schema_comparison_contract_accepts_exact_versioned_content(self):
        plan = {
            'schema': 'cdeadmin.schema-compare.change-plan.v1',
            'targetSide': 'right',
            'targetRef': schema_compare_asset()['content']['rightRef'],
            'operations': [{
                'id': 'operation:1', 'action': 'create', 'risk': 'low',
                'dependencies': [],
                'nativeStatement': 'CREATE TABLE T (ID INTEGER)',
            }],
        }
        validated = validate_asset_request(
            schema_compare_asset(change_plan=plan),
            'project-one', 'comparison-one',
        )
        self.assertEqual(
            'cdeadmin.schema_compare.v1', validated['asset_type']
        )
        self.assertEqual(
            'right', json.loads(validated['content'])['changePlan'][
                'targetSide'
            ]
        )

    def test_schema_comparison_rejects_contract_drift(self):
        cases = []
        wrong_schema = schema_compare_asset()
        wrong_schema['content']['schema'] = 'unknown'
        cases.append((wrong_schema, 'asset schema'))
        wrong_mapping = schema_compare_asset()
        wrong_mapping['content']['acceptedMappings'] = [{
            'mappingId': 'm1', 'leftId': 'left', 'rightId': 'right',
            'category': 'guessed', 'accepted': True,
        }]
        cases.append((wrong_mapping, 'mapping category'))
        wrong_plan = schema_compare_asset(change_plan={
            'schema': 'cdeadmin.schema-compare.change-plan.v1',
            'targetSide': 'automatic',
            'targetRef': schema_compare_asset()['content']['rightRef'],
            'operations': [],
        })
        cases.append((wrong_plan, 'explicit target side'))
        missing_dependency = schema_compare_asset(change_plan={
            'schema': 'cdeadmin.schema-compare.change-plan.v1',
            'targetSide': 'right',
            'targetRef': schema_compare_asset()['content']['rightRef'],
            'operations': [{
                'id': 'operation:1', 'action': 'create', 'risk': 'low',
                'dependencies': ['operation:missing'],
                'nativeStatement': 'CREATE TABLE T (ID INTEGER)',
            }],
        })
        cases.append((missing_dependency, 'dependency is missing'))
        for value, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                    ProjectAssetError, message):
                validate_asset_request(
                    value, 'project-one', 'comparison-one'
                )

    def test_lineage_contract_accepts_complete_evidence_content(self):
        validated = validate_asset_request(
            lineage_asset(), 'project-one', 'lineage-one'
        )
        content = json.loads(validated['content'])
        self.assertEqual('cdeadmin.lineage.v1', validated['asset_type'])
        self.assertEqual(
            'user_curated', content['curatedEdges'][0]['evidence'][0][
                'origin'
            ]
        )
        self.assertEqual(
            'DIRECT_IDENTITY',
            content['curatedEdges'][0]['fieldLineage'][0][
                'transformation'
            ]
        )

    def test_lineage_rejects_reference_evidence_field_and_identity_drift(self):
        cases = []
        wrong_reference = lineage_asset()
        wrong_reference['content']['scopeRefs'][0]['schema'] = 'invented-ref'
        cases.append((wrong_reference, 'reference type'))
        wrong_origin = lineage_asset()
        wrong_origin['content']['curatedEdges'][0]['evidence'][0][
            'origin'
        ] = 'guessed'
        cases.append((wrong_origin, 'evidence origin'))
        wrong_field = lineage_asset()
        wrong_field['content']['curatedEdges'][0]['fieldLineage'][0][
            'transformation'
        ] = 'MAGIC'
        cases.append((wrong_field, 'Field transformation'))
        wrong_timestamp = lineage_asset()
        wrong_timestamp['content']['curatedEdges'][0]['evidence'][0][
            'capturedAt'
        ] = 'not-a-time'
        cases.append((wrong_timestamp, 'ISO timestamp'))
        wrong_state = lineage_asset()
        wrong_state['content']['curatedEdges'][0][
            'presentationState'
        ] = 'probably-correct'
        cases.append((wrong_state, 'presentation state'))
        duplicate_edge = lineage_asset()
        duplicate_edge['content']['curatedEdges'].append(
            dict(duplicate_edge['content']['curatedEdges'][0])
        )
        cases.append((duplicate_edge, 'edge IDs must be unique'))
        for value, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                    ProjectAssetError, message):
                validate_asset_request(
                    value, 'project-one', 'lineage-one'
                )

    def test_lineage_rejects_credentials_in_nested_native_evidence(self):
        value = lineage_asset()
        value['content']['curatedEdges'][0]['evidence'][0]['details'] = {
            'nested': {'accessToken': 'must-not-persist'}
        }
        with self.assertRaisesRegex(ProjectAssetError, 'secret field'):
            validate_asset_request(value, 'project-one', 'lineage-one')

    def test_quality_contract_accepts_typed_runtime_independent_content(self):
        validated = validate_asset_request(
            quality_asset(), 'project-one', 'quality-one'
        )
        content = json.loads(validated['content'])
        self.assertEqual('cdeadmin.quality.v1', validated['asset_type'])
        self.assertEqual('not_null', content['rules'][0]['type'])
        self.assertEqual(
            'profile-sample:orders:42',
            content['rules'][0]['sourceSampleRef']['id'],
        )
        self.assertEqual(
            ['critical'], content['actions'][0]['severities']
        )

    def test_quality_rejects_every_contract_identity_and_bound_violation(self):
        cases = []
        wrong_schema = quality_asset()
        wrong_schema['content']['schema'] = 'invented'
        cases.append((wrong_schema, 'asset schema'))
        wrong_family = quality_asset()
        wrong_family['content']['rules'][0]['type'] = 'guessed'
        cases.append((wrong_family, 'rule family'))
        wrong_dimension = quality_asset()
        wrong_dimension['content']['rules'][0]['dimension'] = 'probably_good'
        cases.append((wrong_dimension, 'rule dimension'))
        wrong_mode = quality_asset()
        wrong_mode['content']['rules'][0]['evaluationMode'] = 'magic'
        cases.append((wrong_mode, 'evaluation mode'))
        wrong_ratio = quality_asset()
        wrong_ratio['content']['rules'][0]['threshold']['value'] = 1.01
        cases.append((wrong_ratio, 'exceeds its maximum'))
        wrong_sample = quality_asset()
        wrong_sample['content']['defaultScope']['samplePolicy']['limit'] = 0
        cases.append((wrong_sample, 'below its minimum'))
        wrong_action = quality_asset()
        wrong_action['content']['rules'][0]['actionIds'] = ['missing']
        cases.append((wrong_action, 'unknown action'))
        duplicate_rule = quality_asset()
        duplicate_rule['content']['rules'].append(
            dict(duplicate_rule['content']['rules'][0])
        )
        cases.append((duplicate_rule, 'rule IDs must be unique'))
        for value, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                    ProjectAssetError, message):
                validate_asset_request(
                    value, 'project-one', 'quality-one'
                )

    def test_quality_rejects_unevidenced_and_non_finite_values(self):
        no_evidence = quality_asset()
        no_evidence['content']['rules'][0]['sourceSampleRef'] = None
        with self.assertRaisesRegex(ProjectAssetError, 'source evidence'):
            validate_asset_request(
                no_evidence, 'project-one', 'quality-one'
            )
        for number in (float('nan'), float('inf'), float('-inf')):
            value = quality_asset()
            value['content']['severityPolicy']['weights']['critical'] = number
            with self.subTest(number=number), self.assertRaisesRegex(
                    ProjectAssetError, 'must be finite'):
                validate_asset_request(
                    value, 'project-one', 'quality-one'
                )

    def test_quality_rejects_credentials_in_rules_actions_and_schedules(self):
        for target in ('rule', 'action', 'schedule'):
            value = quality_asset()
            if target == 'rule':
                value['content']['rules'][0]['nativeDetails'] = {
                    'password': 'must-not-persist'
                }
            elif target == 'action':
                value['content']['actions'][0]['parameters'] = {
                    'accessToken': 'must-not-persist'
                }
            else:
                value['content']['schedule'] = {
                    'cron': '0 2 * * *',
                    'secret': 'must-not-persist',
                }
            with self.subTest(target=target), self.assertRaisesRegex(
                    ProjectAssetError, 'secret field'):
                validate_asset_request(
                    value, 'project-one', 'quality-one'
                )

    def test_etl_contract_accepts_typed_cross_engine_pipeline(self):
        validated = validate_asset_request(
            etl_asset(), 'project-one', 'etl-one'
        )
        content = json.loads(validated['content'])
        self.assertEqual('cdeadmin.etl.v1', validated['asset_type'])
        self.assertEqual(['source', 'sink'], [
            node['kind'] for node in content['nodes']
        ])
        self.assertTrue(content['parameters'][0]['secret'])
        self.assertEqual(
            'cdeadmin.credential-ref.v1',
            content['parameters'][0]['credentialRef']['schema'],
        )

    def test_etl_rejects_identity_mode_reference_and_graph_drift(self):
        cases = []
        wrong_schema = etl_asset()
        wrong_schema['content']['schema'] = 'invented'
        cases.append((wrong_schema, 'asset schema'))
        wrong_family = etl_asset()
        wrong_family['content']['nodes'][0]['kind'] = 'probably_source'
        cases.append((wrong_family, 'node family'))
        wrong_port_mode = etl_asset()
        wrong_port_mode['content']['nodes'][0]['ports'][0]['mode'] = 'realtime'
        cases.append((wrong_port_mode, 'port mode'))
        incompatible = etl_asset()
        incompatible['content']['nodes'][1]['ports'][0]['mode'] = 'stream'
        cases.append((incompatible, 'port modes are incompatible'))
        dangling = etl_asset()
        dangling['content']['edges'][0]['toNodeId'] = 'missing'
        cases.append((dangling, 'unknown node'))
        duplicate = etl_asset()
        duplicate['content']['nodes'].append(
            dict(duplicate['content']['nodes'][0])
        )
        cases.append((duplicate, 'nodes IDs must be unique'))
        bad_deployment = etl_asset()
        bad_deployment['content']['deployments'][0]['bindings'][0][
            'nodeId'
        ] = 'missing'
        cases.append((bad_deployment, 'binds an unknown node'))
        bad_schedule = etl_asset()
        bad_schedule['content']['schedules'][0][
            'deploymentId'
        ] = 'missing'
        cases.append((bad_schedule, 'unknown deployment'))
        bad_dead_letter = etl_asset()
        bad_dead_letter['content']['nodes'][1]['errorRoute'] = {
            'schema': 'cdeadmin.etl-error-route.v1',
            'action': 'dead_letter', 'maximumAttempts': 0,
            'targetRef': {
                'schema': 'cdeadmin.asset-ref.v1',
                'projectId': 'project-one', 'assetId': 'not-live',
            },
            'retryPolicy': {}, 'nativeDetails': {},
        }
        cases.append((bad_dead_letter, 'provider ResourceRef target'))
        bad_binding = etl_asset()
        bad_binding['content']['deployments'][0]['bindings'][0][
            'resourceRef'
        ] = {
            'schema': 'cdeadmin.asset-ref.v1',
            'projectId': 'project-one', 'assetId': 'not-live',
        }
        cases.append((
            bad_binding, 'deployment resource must be a ResourceRef'
        ))
        for value, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                    ProjectAssetError, message):
                validate_asset_request(value, 'project-one', 'etl-one')

    def test_etl_rejects_cycles_invalid_loss_and_raw_credentials(self):
        cyclic = etl_asset()
        source = cyclic['content']['nodes'][0]
        sink = cyclic['content']['nodes'][1]
        source['ports'].append({
            **source['ports'][0], 'id': 'return', 'name': 'return',
            'direction': 'input',
        })
        sink['ports'].append({
            **sink['ports'][0], 'id': 'return', 'name': 'return',
            'direction': 'output',
        })
        cyclic['content']['edges'].append({
            **cyclic['content']['edges'][0], 'id': 'return',
            'fromNodeId': 'sink', 'fromPort': 'return',
            'toNodeId': 'source', 'toPort': 'return',
        })
        with self.assertRaisesRegex(ProjectAssetError, 'contains a cycle'):
            validate_asset_request(cyclic, 'project-one', 'etl-one')
        invalid_loss = etl_asset()
        invalid_loss['content']['edges'][0]['mappings'][0][
            'lossy'
        ] = 'maybe'
        with self.assertRaisesRegex(ProjectAssetError, 'must be boolean'):
            validate_asset_request(invalid_loss, 'project-one', 'etl-one')
        secret_default = etl_asset()
        secret_default['content']['parameters'][0]['default'] = 'bad'
        with self.assertRaisesRegex(ProjectAssetError, 'cannot contain'):
            validate_asset_request(secret_default, 'project-one', 'etl-one')
        raw = etl_asset()
        raw['content']['nodes'][0]['nativeDetails'] = {
            'accessToken': 'must-not-persist'
        }
        with self.assertRaisesRegex(ProjectAssetError, 'secret field'):
            validate_asset_request(raw, 'project-one', 'etl-one')

    def test_sensitive_nested_metadata_is_never_persisted(self):
        for field, value in (
            ('metadata', {'nested': {'password': 'bad'}}),
            ('dependency_references', [{'accessToken': 'bad'}]),
            ('resource_bindings', [{'private_key': 'bad'}]),
            ('validation_details', [{'clientSecret': 'bad'}]),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                    ProjectAssetError, 'secret field'):
                validate_asset_request(
                    generic_asset(**{field: value}),
                    'project-one', 'query-one'
                )
        with self.assertRaisesRegex(ProjectAssetError, 'secret field'):
            validate_asset_request(
                generic_asset(content={'nested': {'password': 'bad'}}),
                'project-one', 'query-one'
            )

    def test_paths_ids_versions_states_and_json_are_strict(self):
        cases = (
            ({'path': '../escape.json'}, 'safe relative path'),
            ({'path': '/absolute.json'}, 'safe relative path'),
            ({'asset_type': 'SQL Query'}, 'asset type'),
            ({'expected_version': True}, 'expected asset version'),
            ({'schema_version': 0}, 'schema version'),
            ({'validation_state': 'pretend'}, 'validation state'),
            ({'content': {1, 2}}, 'JSON serializable'),
        )
        for update, message in cases:
            with self.subTest(update=update), self.assertRaisesRegex(
                    ProjectAssetError, message):
                validate_asset_request(
                    generic_asset(**update), 'project-one', 'query-one'
                )

    def test_ddn_contract_rejects_wrong_route_format_files_and_source(self):
        cases = []
        wrong_ref = ddn_asset()
        wrong_ref['assetRef']['assetId'] = 'another'
        cases.append((wrong_ref, 'does not match'))
        wrong_format = ddn_asset()
        wrong_format['snapshot']['format'] = 'unknown'
        cases.append((wrong_format, 'unsupported'))
        wrong_path = ddn_asset()
        wrong_path['snapshot']['files'] = {'../escape.ddn': 'diagram x {}'}
        cases.append((wrong_path, 'safe relative path'))
        wrong_type = ddn_asset()
        wrong_type['snapshot']['files'] = {'example.ddn': 42}
        cases.append((wrong_type, 'must be text'))
        for value, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                    ProjectAssetError, message):
                validate_asset_request(
                    value, 'project-one', 'diagram-one'
                )


class ProjectAssetRouteTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        self.repository = MemoryRepository()
        self.service = ProjectAssetService(self.repository)
        self.app = Flask(__name__)
        self.app.extensions[APP_EXTENSION_KEY] = self.service
        security = ModuleType('flask_security')
        security.current_user = SimpleNamespace(id=7, roles=[])
        login = ModuleType('pgadmin.user_login_check')
        login.pga_login_required = lambda function: function
        with patch.dict(sys.modules, {
            'flask_security': security,
            'pgadmin.user_login_check': login,
        }):
            _register_routes(self.app)
        self.client = self.app.test_client()

    def test_complete_http_lifecycle_and_conflict_contract(self):
        response = self.client.put('/cdeadmin/api/projects/project-one',
                                   json={'name': 'Project One'})
        self.assertEqual(200, response.status_code)
        response = self.client.put(
            '/cdeadmin/api/projects/project-one/assets/query-one',
            json=generic_asset(),
        )
        self.assertEqual(200, response.status_code)
        response = self.client.get(
            '/cdeadmin/api/projects/project-one/assets/query-one'
        )
        self.assertEqual('query', response.get_json()['data']['asset_type'])
        response = self.client.get(
            '/cdeadmin/api/projects/project-one/assets/query-one/revisions'
        )
        self.assertEqual(
            [1], [item['version']
                  for item in response.get_json()['data']]
        )
        response = self.client.put(
            '/cdeadmin/api/projects/project-one/assets/query-one',
            json=generic_asset(),
        )
        self.assertEqual(409, response.status_code)
        self.assertEqual('asset_conflict', response.get_json()['code'])
        self.assertEqual(1, self.repository.rollbacks)

    def test_service_lookup_fails_closed_without_initialization(self):
        with self.assertRaises(ProjectAssetError):
            service_for_app(SimpleNamespace(extensions={}))


@unittest.skipUnless(MIGRATION_DEPENDENCIES, 'migration dependencies missing')
class ProjectAssetMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_create_exact_reversible_schema(self):
        spec = importlib.util.spec_from_file_location(
            'cde_project_asset_migration', MIGRATION_PATH
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        engine = sa.create_engine('sqlite://')
        with engine.begin() as connection:
            connection.execute(sa.text(
                'CREATE TABLE user (id INTEGER PRIMARY KEY)'
            ))
            context = MigrationContext.configure(connection)
            module.op = Operations(context)
            module.upgrade()
            inspector = sa.inspect(connection)
            expected = {
                'cde_project', 'cde_project_member',
                'cde_project_asset', 'cde_project_asset_revision',
            }
            self.assertTrue(expected.issubset(inspector.get_table_names()))
            project_unique = {
                tuple(item['column_names'])
                for item in inspector.get_unique_constraints('cde_project')
            }
            self.assertIn(('project_key',), project_unique)
            asset_columns = {
                item['name'] for item in inspector.get_columns(
                    'cde_project_asset'
                )
            }
            self.assertTrue({
                'resource_bindings', 'validation_state', 'version', 'content'
            }.issubset(asset_columns))
            module.downgrade()
            self.assertTrue(expected.isdisjoint(
                sa.inspect(connection).get_table_names()
            ))


if __name__ == '__main__':
    unittest.main()
