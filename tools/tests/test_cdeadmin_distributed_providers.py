##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Contract and failure-isolation tests for distributed providers."""

from __future__ import annotations

import json
import sys
import unittest
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

from pgadmin.cdeadmin.providers.apache_ignite.provider import (  # noqa: E402
    PROFILE as IGNITE,
)
from pgadmin.cdeadmin.providers.apache_ignite.client import (  # noqa: E402
    IgniteBackend,
)
from pgadmin.cdeadmin.providers.apache_ignite.control_plane import (  # noqa: E402
    OPERATIONS as IGNITE_CONTROL_OPERATIONS,
    compile_action as compile_ignite_action,
)
from pgadmin.cdeadmin.providers.cockroachdb.provider import (  # noqa: E402
    ADMINISTRATION as COCKROACH_ADMIN,
    CONTROL_OPERATIONS as COCKROACH_CONTROL_OPERATIONS,
    PROFILE as COCKROACHDB,
    _node_membership as cockroach_node_membership,
    _run_node as run_cockroach_node,
    _version as cockroachdb_version,
)
from pgadmin.cdeadmin.providers.distributed_sql import (  # noqa: E402
    _PsycopgPoolConnector,
    _initialize_mysql_connection,
    _initialize_postgresql_connection,
    mysql_route,
    optional_rows,
    postgresql_route,
    resource,
)
from pgadmin.cdeadmin.providers.dolt.provider import (  # noqa: E402
    ADMINISTRATION as DOLT_ADMIN,
    CONTROL_OPERATIONS as DOLT_CONTROL_OPERATIONS,
    PROFILE as DOLT,
    _compile_control_plane as compile_dolt_control,
    create_provider as create_dolt_provider,
    _version as dolt_version,
)
from pgadmin.cdeadmin.providers.foundationdb.provider import (  # noqa: E402
    PROFILE as FOUNDATIONDB,
)
from pgadmin.cdeadmin.providers.foundationdb.client import (  # noqa: E402
    CONTROL_OPERATIONS as FOUNDATIONDB_CONTROL_OPERATIONS,
    FoundationDBBackend,
)
from pgadmin.cdeadmin.providers.immudb.provider import (  # noqa: E402
    ADMINISTRATION as IMMUDB_ADMIN,
    CONTROL_OPERATIONS as IMMUDB_CONTROL_OPERATIONS,
    PROFILE as IMMUDB,
    ImmudbDBAPIClient,
    _compile_control as compile_immudb_control,
    _contains_name as immudb_contains_name,
)
from pgadmin.cdeadmin.providers.native_distributed import (  # noqa: E402
    NativeDistributedClient,
    NativeDistributedError,
    NativeResult,
)
from pgadmin.cdeadmin.providers.native_key_value import (  # noqa: E402
    KeyIdentityStore,
    decode_value,
    key_value_catalog,
    validate_key_value_request,
)
from pgadmin.cdeadmin.providers.tidb.provider import (  # noqa: E402
    ADMINISTRATION as TIDB_ADMIN,
    CONTROL_OPERATIONS as TIDB_CONTROL_OPERATIONS,
    PROFILE as TIDB,
    _run_br as run_tidb_br,
    _run_cdc as run_tidb_cdc,
    _version as tidb_version,
)
from pgadmin.cdeadmin.providers.tikv.provider import (  # noqa: E402
    PROFILE as TIKV,
)
from pgadmin.cdeadmin.providers.tikv.client import (  # noqa: E402
    CONTROL_OPERATIONS as TIKV_CONTROL_OPERATIONS,
    TiKVBackend,
)
from pgadmin.cdeadmin.providers.vitess.provider import (  # noqa: E402
    ADMINISTRATION as VITESS_ADMIN,
    CONTROL_OPERATIONS as VITESS_CONTROL_OPERATIONS,
    PROFILE as VITESS,
    VitessDBAPIClient,
    _version as vitess_version,
)
from pgadmin.cdeadmin.providers.vitess.control_plane import (  # noqa: E402
    _document_contains_named_resource as vitess_contains_resource,
    compile_action as compile_vitess_action,
)
from pgadmin.cdeadmin.providers.yugabytedb.provider import (  # noqa: E402
    ADMINISTRATION as YUGABYTEDB_ADMIN,
    CONTROL_OPERATIONS as YUGABYTEDB_CONTROL_OPERATIONS,
    PROFILE as YUGABYTEDB,
    _version as yugabytedb_version,
)
from pgadmin.cdeadmin.providers.yugabytedb.control_plane import (  # noqa: E402
    _restoration_id as yugabytedb_restoration_id,
    compile_action as compile_yugabytedb_action,
)
from pgadmin.cdeadmin.sdk import (  # noqa: E402
    RelationalDBAPIClient,
    RelationalClientConfig,
    RelationalClientError,
)
from pgadmin.cdeadmin.visual_admin import (  # noqa: E402
    catalog_for_engine, enrich_engine_experience,
)


PROVIDER_ROOT = WEB / 'pgadmin/cdeadmin/providers'


class _Cursor:
    def __init__(self, row=(1,)):
        self.row = row
        self.executed = []
        self.closed = False

    def execute(self, source, parameters=()):
        self.executed.append((source, parameters))

    def fetchone(self):
        return self.row

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, row=(1,)):
        self.cursor_value = _Cursor(row)
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def close(self):
        self.closed = True


class _ConnectorModule:
    def __init__(self, connection):
        self.connection = connection
        self.arguments = None

    def connect(self, **arguments):
        self.arguments = arguments
        return self.connection


class _Response:
    def __init__(self, document):
        self.payload = json.dumps(document).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, maximum):
        return self.payload[:maximum]


class _Backend:
    def __init__(self):
        self.commands = []
        self.closed = False

    @staticmethod
    def runtime_identity(_request, _handle=None):
        return {
            'engine_id': 'native-test', 'version': '1.0.0',
            'build_id': 'native-test:1.0.0', 'protocol_id': 'native',
        }

    @staticmethod
    def list_resources(request):
        return [resource(
            'cluster', [], 'one',
            request.get('capability_generation', 'current'),
        )]

    @staticmethod
    def open_session(_request):
        return object()

    @staticmethod
    def describe_transaction(_handle):
        return {'native_state': 'opaque'}

    def execute(self, _handle, command, parameters):
        self.commands.append((command, parameters))
        return NativeResult(
            'key_value', 'entries', [{'key': 'one', 'value': 1}],
            {'fields': ['key', 'value']}, {'operation': 'get'},
        )

    @staticmethod
    def cancel(_token):
        return False

    @staticmethod
    def describe_security(_request):
        return {'authorization_model': 'native'}

    @staticmethod
    def validate_admin_operation(_request):
        return {'errors': []}

    @staticmethod
    def plan_admin_operation(request):
        return {'provider_payload': request}

    @staticmethod
    def apply_admin_operation(_request):
        return {'accepted': True}

    @staticmethod
    def read_admin_rows(_request):
        return {'rows': []}

    @staticmethod
    def complete(_request):
        return []

    def close(self):
        self.closed = True


class _IgniteCache:
    def __init__(self, name, owner):
        self.name = name
        self.owner = owner
        self.values = {}

    def destroy(self):
        del self.owner[self.name]

    def put_if_absent(self, key, value):
        if key in self.values:
            return False
        self.values[key] = value
        return True

    def replace_if_equals(self, key, original, value):
        if self.values.get(key) != original:
            return False
        self.values[key] = value
        return True

    def remove_if_equals(self, key, original):
        if self.values.get(key) != original:
            return False
        del self.values[key]
        return True

    def scan(self):
        return iter(self.values.items())


class _IgniteClient:
    caches = {}

    def __init__(self, **_options):
        pass

    @staticmethod
    def connect(_hosts):
        return None

    @classmethod
    def create_cache(cls, name):
        if isinstance(name, dict):
            name = name[0]
        cls.caches[name] = _IgniteCache(name, cls.caches)

    @classmethod
    def get_cache(cls, name):
        return cls.caches[name]

    @staticmethod
    def close():
        return None

    @classmethod
    def get_cache_names(cls):
        return list(cls.caches)

    @staticmethod
    def sql(_source):
        return []


class DistributedProviderTests(unittest.TestCase):

    def test_cockroach_relational_object_contract_is_structurally_complete(
            self):
        catalog = enrich_engine_experience(COCKROACH_ADMIN.catalog(
            catalog_for_engine('cockroachdb')
        ))
        coverage = catalog['concept_coverage']
        self.assertTrue(coverage['declaration_ready'])
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        concepts = {
            item['concept_id']: item
            for family in coverage['families']
            for item in family['concepts']
        }
        self.assertEqual(
            'not_applicable', concepts['domains']['declared_status']
        )
        self.assertEqual(
            'not_applicable',
            concepts['extensions_and_plugins']['declared_status'],
        )
        self.assertEqual(
            ['cluster'],
            concepts['servers']['catalog_resource_kinds'],
        )
        self.assertEqual(
            ['zone-config'],
            concepts['tablespaces_and_filespaces'][
                'catalog_resource_kinds'],
        )
        self.assertEqual(
            ['create', 'drop', 'inspect'],
            concepts['procedures']['operation_obligations']['procedure'],
        )
        self.assertIn(
            'pause',
            concepts['jobs_and_events'][
                'operation_obligations']['schedule'],
        )

    def test_ignite_control_plane_is_typed_and_control_sh_compiled(self):
        keys = {
            (item.resource_kind, item.operation_id)
            for item in IGNITE_CONTROL_OPERATIONS
        }
        self.assertTrue({
            ('cluster', 'set_state'),
            ('baseline-topology', 'add_nodes'),
            ('baseline-topology', 'configure_auto_adjust'),
            ('cache', 'validate_indexes'),
            ('cache', 'idle_verify'),
            ('cache', 'reset_lost_partitions'),
            ('snapshot', 'create'),
            ('snapshot', 'restore'),
        }.issubset(keys))
        self.assertFalse(any(
            field['control'] == 'code'
            for operation in IGNITE_CONTROL_OPERATIONS
            for field in operation.fields
        ))
        baseline = compile_ignite_action({
            'resource_kind': 'baseline-topology',
            'operation_id': 'set_nodes', 'target_resource': None,
            'draft': {'consistent_ids': ['node-a', 'node-b']},
        })
        self.assertEqual([
            '--baseline', 'set', 'node-a,node-b', '--yes',
        ], baseline['provider_action']['arguments'])
        snapshot = compile_ignite_action({
            'resource_kind': 'snapshot', 'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': 'pre_upgrade', 'synchronous': False},
        })
        self.assertEqual([
            '--snapshot', 'cancel', '--name', 'pre_upgrade',
        ], snapshot['provider_action']['cancel_arguments'])

    def test_ignite_control_plan_redacts_native_arguments(self):
        plan = IgniteBackend.plan_admin_operation({
            'resource_kind': 'cluster', 'operation_id': 'set_state',
            'target_resource': {
                'resource_id': 'cluster:ignite',
                'display_name': 'Apache Ignite',
            },
            'draft': {'state': 'ACTIVE_READ_ONLY', 'force': False},
            '_provider_route': {
                'host': '127.0.0.1', 'port': 10800,
                'control_sh_path': '/opt/ignite/bin/control.sh',
            },
        })
        action = plan['provider_payload']['compiled']['provider_action']
        self.assertEqual([
            '--set-state', 'ACTIVE_READ_ONLY', '--yes',
        ], action['arguments'])
        preview = plan['command_preview']['statements'][0]
        self.assertEqual('control.sh', preview['tool'])
        self.assertNotIn('arguments', preview)
        self.assertTrue(plan['receipt']['provider_finality_authority'])
        self.assertFalse(plan['receipt']['automatic_mutation_retry'])

    def test_ignite_control_values_fail_closed(self):
        with self.assertRaisesRegex(
            NativeDistributedError, 'cluster state'
        ):
            compile_ignite_action({
                'resource_kind': 'cluster', 'operation_id': 'set_state',
                'target_resource': {
                    'resource_id': 'cluster:ignite',
                    'display_name': 'Apache Ignite',
                },
                'draft': {'state': 'ACTIVE; shutdown'},
            })

    def test_ignite_resource_discovery_includes_rest_topology_nodes(self):
        backend = IgniteBackend(module=SimpleNamespace(Client=_IgniteClient))
        backend._rest = lambda _route, command: ({
            'top': [{
                'consistentId': 'ignite-node-a',
                'tcpAddresses': ['127.0.0.1'],
                'tcpPort': 10800,
                'clientMode': False,
            }],
        })[command]
        resources = backend.list_resources({
            'route': {'host': '127.0.0.1', 'port': 10800},
            'capability_generation': 'test-generation',
        })
        nodes = [
            item for item in resources if item['resource_kind'] == 'node'
        ]
        self.assertEqual(1, len(nodes))
        self.assertEqual('ignite-node-a', nodes[0]['display_name'])
        self.assertEqual(10800, nodes[0]['native']['tcp_port'])

    def test_yugabytedb_control_plane_is_typed_and_cli_compiled(self):
        keys = {
            (item.resource_kind, item.operation_id)
            for item in YUGABYTEDB_CONTROL_OPERATIONS
        }
        self.assertTrue({
            ('placement-policy', 'configure'),
            ('node', 'add_blacklist'),
            ('cluster', 'set_load_balancer'),
            ('tablet', 'leader_stepdown'),
            ('snapshot', 'create_database'),
            ('schedule', 'restore'),
            ('changefeed', 'create'),
            ('xcluster-replication', 'create'),
        }.issubset(keys))
        self.assertFalse(any(
            field['control'] == 'code'
            for operation in YUGABYTEDB_CONTROL_OPERATIONS
            for field in operation.fields
        ))
        placement = compile_yugabytedb_action({
            'resource_kind': 'placement-policy',
            'operation_id': 'configure', 'target_resource': None,
            'draft': {
                'placements': [
                    'aws.ca-central-1.zone-a:2',
                    'aws.ca-central-1.zone-b',
                ],
                'replication_factor': 3,
                'placement_uuid': 'canada-primary',
            },
        })
        self.assertEqual([
            'modify_placement_info',
            'aws.ca-central-1.zone-a:2,aws.ca-central-1.zone-b',
            '3', 'canada-primary',
        ], placement['provider_action']['arguments'])
        self.assertTrue(placement['impact']['data_movement_possible'])

    def test_yugabytedb_xcluster_plan_is_provider_constructed(self):
        plan = YUGABYTEDB_ADMIN.plan({
            'resource_kind': 'xcluster-replication',
            'operation_id': 'create', 'target_resource': None,
            'draft': {
                'replication_group_id': 'producer-ca',
                'producer_master_addresses': [
                    'ybm1.example:7100', 'ybm2.example:7100',
                ],
                'table_ids': ['abc123', 'def456'],
                'bootstrap_ids': ['feed123', 'feed456'],
                'transactional': True,
            },
            '_provider_route': {
                'host': '127.0.0.1', 'port': 5433,
                'yb_admin_path': '/opt/yugabyte/bin/yb-admin',
                'master_addresses': '127.0.0.1:7100',
            },
        })
        action = plan['provider_payload']['compiled']['provider_action']
        self.assertEqual([
            'setup_universe_replication', 'producer-ca',
            'ybm1.example:7100,ybm2.example:7100', 'abc123,def456',
            'feed123,feed456', 'transactional',
        ], action['arguments'])
        preview = plan['command_preview']['statements'][0]
        self.assertEqual('yb-admin', preview['tool'])
        self.assertNotIn('arguments', preview)
        self.assertFalse(plan['receipt']['automatic_mutation_retry'])

    def test_yugabytedb_control_values_fail_closed_and_restore_is_parsed(self):
        with self.assertRaisesRegex(
            RelationalClientError, 'placement replica count'
        ):
            compile_yugabytedb_action({
                'resource_kind': 'placement-policy',
                'operation_id': 'configure', 'target_resource': None,
                'draft': {
                    'placements': ['aws.ca-central-1.zone-a:0'],
                    'replication_factor': 3,
                },
            })
        self.assertEqual(
            '01234567-89ab-cdef-0123-456789abcdef',
            yugabytedb_restoration_id({
                'stdout': json.dumps({
                    'restoration_id':
                    '01234567-89ab-cdef-0123-456789abcdef',
                }),
            }),
        )

    def test_yugabytedb_ysql_native_object_forms_compile_typed_sql(self):
        route = {'host': '127.0.0.1', 'port': 5433, 'database': 'app'}
        materialized = YUGABYTEDB_ADMIN.plan({
            'resource_kind': 'materialized-view', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'summary', 'parent': 'public',
                'query': 'SELECT id FROM public.orders',
                'with_data': False,
            },
            '_provider_route': route,
        })
        self.assertEqual(
            'CREATE MATERIALIZED VIEW "public"."summary" AS '
            'SELECT id FROM public.orders WITH NO DATA',
            materialized['provider_payload']['compiled']['statements'][0][
                'source'
            ],
        )
        enum_type = YUGABYTEDB_ADMIN.plan({
            'resource_kind': 'type', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'mood', 'parent': 'public', 'type_kind': 'ENUM',
                'enum_values': ['calm', 'busy'], 'fields': [],
            },
            '_provider_route': route,
        })
        self.assertIn(
            "CREATE TYPE \"public\".\"mood\" AS ENUM ('calm', 'busy')",
            enum_type['provider_payload']['compiled']['statements'][0][
                'source'
            ],
        )
        placement = YUGABYTEDB_ADMIN.plan({
            'resource_kind': 'tablespace', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'canada',
                'replica_placement': {
                    'num_replicas': 1,
                    'placement_blocks': [{
                        'cloud': 'cloud1', 'region': 'datacenter1',
                        'zone': 'rack1', 'min_num_replicas': 1,
                    }],
                },
            },
            '_provider_route': route,
        })
        source = placement['provider_payload']['compiled']['statements'][0][
            'source'
        ]
        self.assertIn('CREATE TABLESPACE "canada" WITH', source)
        self.assertIn('replica_placement=', source)

    def test_yugabytedb_ysql_tablespace_rejects_untyped_placement(self):
        with self.assertRaisesRegex(
            RelationalClientError, 'replica placement is incomplete'
        ):
            YUGABYTEDB_ADMIN.plan({
                'resource_kind': 'tablespace', 'operation_id': 'create',
                'target_resource': None,
                'draft': {
                    'name': 'unsafe',
                    'replica_placement': {
                        'num_replicas': 0, 'placement_blocks': [],
                    },
                },
                '_provider_route': {
                    'host': '127.0.0.1', 'port': 5433,
                    'database': 'app',
                },
            })

    def test_vitess_control_plane_is_typed_and_cli_compiled(self):
        keys = {
            (item.resource_kind, item.operation_id)
            for item in VITESS_CONTROL_OPERATIONS
        }
        self.assertTrue({
            ('keyspace', 'create'), ('shard', 'planned_reparent'),
            ('workflow', 'create_move_tables'),
            ('workflow', 'create_reshard'), ('tablet', 'restore'),
            ('workflow', 'switch_traffic'),
            ('workflow', 'reverse_traffic'),
            ('workflow', 'complete'), ('routing-rule', 'apply'),
            ('online-ddl', 'launch'), ('online-ddl', 'retry'),
            ('online-ddl', 'complete'), ('online-ddl', 'cancel'),
            ('online-ddl', 'cleanup'), ('online-ddl', 'throttle'),
            ('online-ddl', 'unthrottle'),
            ('online-ddl', 'force_cutover'),
            ('vschema', 'rebuild'),
        }.issubset(keys))
        self.assertFalse(any(
            field['control'] == 'code'
            for operation in VITESS_CONTROL_OPERATIONS
            for field in operation.fields
        ))
        keyspace = compile_vitess_action({
            'resource_kind': 'keyspace', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'commerce', 'durability_policy': 'semi_sync',
                'allow_empty_vschema': True,
            },
        })
        self.assertEqual([
            'CreateKeyspace', '--allow-empty-vschema',
            '--durability-policy', 'semi_sync', 'commerce',
        ], keyspace['provider_action']['arguments'])
        reparent = compile_vitess_action({
            'resource_kind': 'shard',
            'operation_id': 'planned_reparent',
            'target_resource': {
                'resource_id': 'shard:commerce/-80',
                'display_path': ['commerce', '-80'],
            },
            'draft': {
                'new_primary': 'zone1-0000000101',
                'expected_primary': 'zone1-0000000100',
                'allow_cross_cell': True,
            },
        })
        self.assertEqual([
            'PlannedReparentShard', '--new-primary',
            'zone1-0000000101', '--expected-primary',
            'zone1-0000000100', '--allow-cross-cell-promotion',
            'commerce/-80',
        ], reparent['provider_action']['arguments'])

    def test_vitess_workflow_plan_and_cancel_are_provider_constructed(self):
        plan = VITESS_ADMIN.plan({
            'resource_kind': 'workflow',
            'operation_id': 'create_move_tables',
            'target_resource': None,
            'draft': {
                'workflow_name': 'commerce_to_customer',
                'target_keyspace': 'customer',
                'source_keyspace': 'commerce',
                'tables': ['orders', 'customers'],
                'tablet_types': 'replica,rdonly',
            },
            '_provider_route': {
                'host': '127.0.0.1', 'port': 15306,
                'vtctldclient_path': '/opt/vitess/bin/vtctldclient',
                'vtctld_server': '127.0.0.1:15999',
            },
        })
        action = plan['provider_payload']['compiled']['provider_action']
        self.assertEqual([
            'movetables', '--workflow', 'commerce_to_customer',
            '--target-keyspace', 'customer', 'create',
            '--source-keyspace', 'commerce', '--tables',
            'orders,customers', '--tablet-types', 'replica,rdonly',
        ], action['arguments'])
        self.assertEqual([
            'movetables', '--workflow', 'commerce_to_customer',
            '--target-keyspace', 'customer', 'cancel',
        ], action['cancel_arguments'])
        self.assertNotIn('arguments', plan['command_preview']['statements'][0])
        self.assertTrue(plan['receipt']['provider_finality_authority'])
        self.assertFalse(plan['receipt']['automatic_mutation_retry'])

    def test_vitess_workflow_drop_uses_native_delete_and_state_is_exact(self):
        action = compile_vitess_action({
            'resource_kind': 'workflow', 'operation_id': 'drop',
            'target_resource': {
                'resource_id': 'workflow:commerce/reshard_80',
                'display_path': ['commerce', 'reshard_80'],
            },
            'draft': {},
        })
        self.assertEqual([
            'workflow', '--keyspace', 'commerce', 'delete',
            '--workflow', 'reshard_80',
        ], action['provider_action']['arguments'])
        document = {
            'workflows': [{'name': 'orders'}, {'name': 'reshard_80'}],
        }
        self.assertTrue(vitess_contains_resource(document, 'reshard_80'))
        self.assertFalse(vitess_contains_resource(document, 'missing'))

    def test_vitess_workflow_traffic_and_completion_are_typed(self):
        target = {
            'resource_id': 'workflow:customer/commerce_to_customer',
            'display_path': ['customer', 'commerce_to_customer'],
        }
        switched = compile_vitess_action({
            'resource_kind': 'workflow', 'operation_id': 'switch_traffic',
            'target_resource': target,
            'draft': {
                'workflow_family': 'movetables',
                'tablet_types': 'primary', 'cells': ['zone1', 'zone2'],
                'timeout_seconds': 60,
                'max_replication_lag_seconds': 15,
                'enable_reverse_replication': False,
                'initialize_target_sequences': True,
            },
        })
        self.assertEqual([
            'movetables', '--workflow', 'commerce_to_customer',
            '--target-keyspace', 'customer', 'switchtraffic',
            '--tablet-types', 'primary', '--cells', 'zone1,zone2',
            '--timeout', '60s', '--max-replication-lag-allowed', '15s',
            '--enable-reverse-replication=false',
            '--initialize-target-sequences',
        ], switched['provider_action']['arguments'])
        completed = compile_vitess_action({
            'resource_kind': 'workflow', 'operation_id': 'complete',
            'target_resource': target,
            'draft': {
                'workflow_family': 'movetables', 'keep_data': True,
                'keep_routing_rules': True, 'rename_tables': True,
                'ignore_source_keyspace': True,
            },
        })
        self.assertEqual([
            'movetables', '--workflow', 'commerce_to_customer',
            '--target-keyspace', 'customer', 'complete', '--keep-data',
            '--keep-routing-rules', '--rename-tables',
            '--ignore-source-keyspace',
        ], completed['provider_action']['arguments'])

    def test_vitess_routing_rules_are_structured_and_fail_closed(self):
        rules = {
            'rules': [{
                'fromTable': 'commerce.orders',
                'toTables': ['customer.orders'],
            }],
        }
        action = compile_vitess_action({
            'resource_kind': 'routing-rule', 'operation_id': 'apply',
            'target_resource': None,
            'draft': {
                'rules': rules, 'cells': ['zone1'], 'skip_rebuild': True,
            },
        })
        self.assertEqual('ApplyRoutingRules',
                         action['provider_action']['arguments'][0])
        self.assertEqual(rules, json.loads(
            action['provider_action']['arguments'][2]
        ))
        self.assertEqual([
            '--cells', 'zone1', '--skip-rebuild',
        ], action['provider_action']['arguments'][3:])
        with self.assertRaisesRegex(
                RelationalClientError, 'routing rule is invalid'):
            compile_vitess_action({
                'resource_kind': 'routing-rule', 'operation_id': 'apply',
                'target_resource': None,
                'draft': {'rules': {'rules': [{
                    'fromTable': 'orders', 'toTables': ['archive.orders'],
                    'unexpected': 'silently ignored by protobuf JSON',
                }]}},
            })

    def test_vitess_online_ddl_lifecycle_uses_exact_native_identifiers(self):
        target = {
            'resource_id': 'online-ddl:commerce/migration',
            'display_path': [
                'commerce', 'a0638f6b_ec7b_11ea_9bf8_000d3a9b8a9a',
            ],
        }
        action = compile_vitess_action({
            'resource_kind': 'online-ddl',
            'operation_id': 'force_cutover',
            'target_resource': target, 'draft': {},
        })
        self.assertEqual([
            'OnlineDDL', 'force-cutover', 'commerce',
            'a0638f6b_ec7b_11ea_9bf8_000d3a9b8a9a',
        ], action['provider_action']['arguments'])
        with self.assertRaisesRegex(
                RelationalClientError, 'online DDL migration is invalid'):
            compile_vitess_action({
                'resource_kind': 'online-ddl', 'operation_id': 'cancel',
                'target_resource': {
                    'resource_id': 'online-ddl:commerce/bad',
                    'display_path': ['commerce', 'all'],
                },
                'draft': {},
            })

    def test_vitess_tablet_lifecycle_uses_exact_native_commands(self):
        target = {
            'resource_id': 'tablet:zone1-0000000101',
            'display_name': 'zone1-0000000101',
            'display_path': ['zone1-0000000101'],
        }
        cases = (
            ('ping', {}, ['PingTablet', 'zone1-0000000101']),
            ('refresh_state', {}, [
                'RefreshState', 'zone1-0000000101',
            ]),
            ('run_health_check', {}, [
                'RunHealthCheck', 'zone1-0000000101',
            ]),
            ('change_type', {'tablet_type': 'rdonly'}, [
                'ChangeTabletType', 'zone1-0000000101', 'rdonly',
            ]),
            ('set_writable', {'writable': False}, [
                'SetWritable', 'zone1-0000000101', 'false',
            ]),
            ('drop', {'allow_primary': True}, [
                'DeleteTablets', '--allow-primary', 'zone1-0000000101',
            ]),
        )
        for operation, draft, expected in cases:
            with self.subTest(operation=operation):
                action = compile_vitess_action({
                    'resource_kind': 'tablet',
                    'operation_id': operation,
                    'target_resource': target,
                    'draft': draft,
                })
                self.assertEqual(
                    expected, action['provider_action']['arguments']
                )
        with self.assertRaisesRegex(
                RelationalClientError, 'tablet type is invalid'):
            compile_vitess_action({
                'resource_kind': 'tablet',
                'operation_id': 'change_type',
                'target_resource': target,
                'draft': {'tablet_type': 'primary'},
            })

    def test_dolt_control_plane_compiles_version_operations(self):
        keys = {
            (item.resource_kind, item.operation_id)
            for item in DOLT_CONTROL_OPERATIONS
        }
        self.assertTrue({
            ('branch', 'create'), ('branch', 'set_default'),
            ('branch', 'rename'), ('branch', 'copy'),
            ('working-set', 'commit'), ('working-set', 'stage_tables'),
            ('working-set', 'stage_all'),
            ('working-set', 'unstage_tables'),
            ('working-set', 'reset_hard'), ('working-set', 'clean'),
            ('merge', 'start'), ('rebase', 'start'),
            ('rebase', 'continue'), ('rebase', 'abort'),
            ('commit', 'cherry_pick'), ('commit', 'revert'),
            ('remote', 'push'),
            ('conflict', 'resolve'),
        }.issubset(keys))
        branch = DOLT_ADMIN.plan({
            'resource_kind': 'branch', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'feature/cubes', 'start_point': 'main',
            },
            '_provider_route': {'host': '127.0.0.1', 'port': 3306},
        })
        statement = branch['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            'CALL DOLT_BRANCH(%s, %s)', statement['source']
        )
        self.assertEqual(
            ('feature/cubes', 'main'), tuple(statement['parameters'])
        )
        merge = DOLT_ADMIN.plan({
            'resource_kind': 'merge', 'operation_id': 'start',
            'target_resource': None,
            'draft': {
                'revision': 'feature/cubes', 'strategy': 'no_ff',
                'message': 'Merge cube work', 'no_commit': False,
            },
            '_provider_route': {'host': '127.0.0.1', 'port': 3306},
        })
        statement = merge['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            ('feature/cubes', '--no-ff', '-m', 'Merge cube work'),
            tuple(statement['parameters']),
        )
        self.assertTrue(merge['impact']['data_movement_possible'])

        default = DOLT_ADMIN.plan({
            'resource_kind': 'branch', 'operation_id': 'set_default',
            'target_resource': {
                'resource_id': 'branch:feature/cubes',
                'display_name': 'feature/cubes',
            },
            'draft': {'persist': False},
            '_provider_route': {
                'host': '127.0.0.1', 'port': 3306,
                'database': 'inventory/main',
            },
        })['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            'SET @@GLOBAL.`inventory_default_branch` = %s',
            default['source'],
        )
        self.assertEqual(('feature/cubes',), default['parameters'])
        persistent = DOLT_ADMIN.plan({
            'resource_kind': 'branch', 'operation_id': 'set_default',
            'target_resource': {
                'resource_id': 'branch:main', 'display_name': 'main',
            },
            'draft': {'persist': True},
            '_provider_route': {
                'host': '127.0.0.1', 'port': 3306,
                'database': 'inventory',
            },
        })['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            'SET PERSIST `inventory_default_branch` = %s',
            persistent['source'],
        )

    def test_dolt_visual_catalog_admits_rebase_control(self):
        context = SimpleNamespace(
            endpoint_id='dolt-catalog-test',
            session_namespace='dolt-catalog-session',
            mode='legacy_native', runtime_verification_state='verified',
            declared_runtime_family='dolt', verified_runtime_family='dolt',
        )
        permissions = SimpleNamespace(
            allows=lambda *_args: True,
            require=lambda *_args: None,
            acquire_secret=None,
        )
        descriptor = create_dolt_provider(
            context, permissions
        ).visual_admin_descriptor()
        resources = {
            item['resource_kind']: item for item in descriptor['objects']
        }
        self.assertIn('rebase', resources)
        operations = {
            item['operation_id'] for item in resources['rebase']['operations']
        }
        self.assertTrue({'start', 'continue', 'abort'}.issubset(operations))

    def test_dolt_visual_catalog_exposes_exact_relational_objects(self):
        self.assertIn('remote', DOLT.resource_kinds)
        catalog = DOLT_ADMIN.catalog(catalog_for_engine('dolt'))
        objects = {
            item['resource_kind']: item for item in catalog['objects']
        }
        self.assertIn('trigger', objects)
        self.assertIn('event', objects)
        self.assertEqual(
            {'inspect', 'create', 'drop'},
            DOLT_ADMIN.dialect.supported['trigger'],
        )
        self.assertEqual(
            {'inspect', 'create', 'alter', 'drop'},
            DOLT_ADMIN.dialect.supported['event'],
        )
        declarations = catalog['concept_declarations']['relational']
        self.assertEqual('not_applicable', declarations['functions']['status'])
        self.assertEqual('not_applicable', declarations['sequences']['status'])

    def test_dolt_event_editor_compiles_native_mysql_event_sql(self):
        plan = DOLT_ADMIN.plan({
            'resource_kind': 'event', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'nightly_commit', 'parent': 'inventory',
                'schedule': 'EVERY 1 DAY', 'preserve': True,
                'enabled': False, 'body': 'CALL DOLT_COMMIT()',
            },
            '_provider_route': {'host': '127.0.0.1', 'port': 3306},
        })
        statement = plan['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            'CREATE EVENT `inventory`.`nightly_commit` ON SCHEDULE '
            'EVERY 1 DAY ON COMPLETION PRESERVE DISABLE DO '
            'CALL DOLT_COMMIT()',
            statement['source'],
        )

    def test_dolt_index_view_and_account_edits_use_supported_syntax(self):
        route = {'host': '127.0.0.1', 'port': 3306, 'database': 'inventory'}
        index = DOLT_ADMIN.plan({
            'resource_kind': 'index', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'by_value', 'parent': 'inventory',
                'table': 'inventory.items', 'columns': ['value'],
                'unique': False,
            },
            '_provider_route': route,
        })['provider_payload']['compiled']['statements'][0]['source']
        self.assertEqual(
            'CREATE INDEX `by_value` ON `inventory`.`items` (`value`)',
            index,
        )
        view = DOLT_ADMIN.plan({
            'resource_kind': 'view', 'operation_id': 'alter',
            'target_resource': {
                'resource_id': 'view:inventory:current_items',
                'resource_kind': 'view',
                'display_name': 'current_items',
                'display_path': ['inventory', 'current_items'],
            },
            'draft': {'query': 'SELECT id FROM items'},
            '_provider_route': route,
        })['provider_payload']['compiled']['statements'][0]['source']
        self.assertEqual(
            'CREATE OR REPLACE VIEW `inventory`.`current_items` AS '
            'SELECT id FROM items',
            view,
        )
        user = DOLT_ADMIN.plan({
            'resource_kind': 'user', 'operation_id': 'alter',
            'target_resource': {
                'resource_id': 'user:reader@%', 'resource_kind': 'user',
                'display_name': 'reader@%', 'display_path': ['reader@%'],
            },
            'draft': {'password': 'replacement-secret'},
            '_provider_route': route,
        })['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            "ALTER USER 'reader'@'%' IDENTIFIED BY <redacted>",
            user['preview_source'],
        )

    def test_dolt_branch_transform_and_revert_are_parameterized(self):
        target = {
            'resource_id': 'branch:feature/old',
            'display_name': 'feature/old',
        }
        rename = compile_dolt_control({
            'resource_kind': 'branch', 'operation_id': 'rename',
            'target_resource': target,
            'draft': {'new_name': 'feature/new', 'force': True},
        })['statements'][0]
        self.assertEqual('CALL DOLT_BRANCH(%s, %s, %s)', rename['source'])
        self.assertEqual(
            ('-mf', 'feature/old', 'feature/new'), rename['parameters']
        )
        copied = compile_dolt_control({
            'resource_kind': 'branch', 'operation_id': 'copy',
            'target_resource': target,
            'draft': {'new_name': 'feature/copy', 'force': False},
        })['statements'][0]
        self.assertEqual(
            ('-c', 'feature/old', 'feature/copy'), copied['parameters']
        )
        reverted = compile_dolt_control({
            'resource_kind': 'commit', 'operation_id': 'revert',
            'target_resource': {
                'resource_id': 'commit:abc', 'display_name': 'abc123',
            },
            'draft': {},
        })['statements'][0]
        self.assertEqual('CALL DOLT_REVERT(%s)', reverted['source'])
        self.assertEqual(('abc123',), reverted['parameters'])

    def test_dolt_working_set_and_rebase_controls_are_parameterized(self):
        target = {
            'resource_id': 'working-set:current',
            'display_name': 'current',
        }
        cases = (
            ('stage_tables', {
                'table_names': ['orders', 'line items'],
                'force_ignored': True,
            }, 'DOLT_ADD', ('-f', 'orders', 'line items')),
            ('stage_all', {}, 'DOLT_ADD', ('-A',)),
            ('unstage_tables', {
                'table_names': ['orders'],
            }, 'DOLT_RESET', ('orders',)),
            ('reset_hard', {
                'revision': 'HEAD~1',
            }, 'DOLT_RESET', ('--hard', 'HEAD~1')),
            ('reset_soft', {
                'revision': 'main',
            }, 'DOLT_RESET', ('--soft', 'main')),
            ('clean', {
                'table_names': ['scratch_table'],
                'include_ignored': True,
            }, 'DOLT_CLEAN', ('-x', 'scratch_table')),
        )
        for operation, draft, procedure, parameters in cases:
            with self.subTest(operation=operation):
                statement = compile_dolt_control({
                    'resource_kind': 'working-set',
                    'operation_id': operation,
                    'target_resource': target,
                    'draft': draft,
                })['statements'][0]
                self.assertEqual(
                    f'CALL {procedure}(' + ', '.join(
                        '%s' for _item in parameters
                    ) + ')',
                    statement['source'],
                )
                self.assertEqual(parameters, statement['parameters'])

        rebase = compile_dolt_control({
            'resource_kind': 'rebase', 'operation_id': 'start',
            'target_resource': {
                'resource_id': 'rebase:current',
                'display_name': 'current',
            },
            'draft': {
                'upstream': 'main', 'interactive': True,
                'empty_commits': 'keep', 'skip_verification': True,
            },
        })['statements'][0]
        self.assertEqual('CALL DOLT_REBASE(%s, %s, %s, %s, %s)',
                         rebase['source'])
        self.assertEqual(
            ('main', '--interactive', '--empty', 'keep',
             '--skip-verification'),
            rebase['parameters'],
        )
        continued = compile_dolt_control({
            'resource_kind': 'rebase', 'operation_id': 'continue',
            'target_resource': {
                'resource_id': 'rebase:current',
                'display_name': 'current',
            }, 'draft': {},
        })['statements'][0]
        self.assertEqual(('--continue',), continued['parameters'])

    def test_dolt_remote_url_requires_server_allowlist(self):
        request = {
            'resource_kind': 'remote', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'origin', 'url': 'https://git.example/data/repo',
            },
            '_provider_route': {
                'host': '127.0.0.1',
                'remote_url_allowlist': ['https://trusted.example/'],
            },
        }
        with self.assertRaisesRegex(
            RelationalClientError, 'outside the endpoint allowlist'
        ):
            DOLT_ADMIN.plan(request)
        request['_provider_route']['remote_url_allowlist'] = [
            'https://git.example/data/'
        ]
        plan = DOLT_ADMIN.plan(request)
        statement = plan['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            ('add', 'origin', 'https://git.example/data/repo'),
            tuple(statement['parameters']),
        )

    def test_dolt_backup_and_restore_are_typed_and_parameterized(self):
        route = {
            'host': '127.0.0.1',
            'backup_url_allowlist': 'file:///srv/cdeadmin/backups/',
        }
        backup = DOLT_ADMIN.plan({
            'resource_kind': 'backup', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'nightly',
                'url': 'file:///srv/cdeadmin/backups/inventory',
            },
            '_provider_route': route,
        })
        statement = backup['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            ('add', 'nightly',
             'file:///srv/cdeadmin/backups/inventory'),
            tuple(statement['parameters']),
        )
        restore = DOLT_ADMIN.plan({
            'resource_kind': 'database', 'operation_id': 'restore_backup',
            'target_resource': {
                'resource_id': 'database:inventory',
                'display_name': 'inventory',
            },
            'draft': {
                'url': 'file:///srv/cdeadmin/backups/inventory',
                'new_database_name': 'inventory_recovered',
                'force': False,
            },
            '_provider_route': route,
        })
        statement = restore['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            ('restore', 'file:///srv/cdeadmin/backups/inventory',
             'inventory_recovered'),
            tuple(statement['parameters']),
        )
        self.assertNotIn(
            'file:///srv',
            restore['command_preview']['statements'][0]['source'],
        )

    def test_foundationdb_control_plane_is_typed_and_cli_compiled(self):
        keys = {
            (item.resource_kind, item.operation_id)
            for item in FOUNDATIONDB_CONTROL_OPERATIONS
        }
        self.assertTrue({
            ('tenant', 'create'), ('process', 'exclude'),
            ('cluster', 'change_coordinators'),
            ('cluster', 'maintenance_on'),
            ('configuration', 'configure'),
        }.issubset(keys))
        self.assertFalse(any(
            field['control'] == 'code'
            for operation in FOUNDATIONDB_CONTROL_OPERATIONS
            for field in operation.fields
        ))
        tenant = FoundationDBBackend._control_command({
            'resource_kind': 'tenant', 'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': 'inventory', 'tenant_group': 'applications'},
        })
        self.assertEqual(
            'tenant create inventory tenant_group=applications',
            tenant['command'],
        )
        coordinators = FoundationDBBackend._control_command({
            'resource_kind': 'cluster',
            'operation_id': 'change_coordinators',
            'target_resource': {
                'resource_id': 'cluster:FoundationDB',
                'display_name': 'FoundationDB',
            },
            'draft': {
                'addresses': ['127.0.0.1:4500', '127.0.0.1:4501'],
                'description': 'cdeadmin_test',
            },
        })
        self.assertEqual(
            'coordinators 127.0.0.1:4500 127.0.0.1:4501 '
            'description=cdeadmin_test', coordinators['command']
        )
        self.assertEqual('high', coordinators['impact']['availability_risk'])

    def test_foundationdb_cli_rejects_command_separators(self):
        backend = object.__new__(FoundationDBBackend)
        with patch.object(
            FoundationDBBackend, '_trusted_file', return_value=sys.executable
        ):
            with self.assertRaisesRegex(
                NativeDistributedError, 'command is invalid'
            ):
                backend._run_cli({
                    'fdbcli_path': sys.executable,
                    'cluster_file': __file__,
                }, 'tenant create safe; kill all')

    def test_foundationdb_backup_tools_are_typed_and_allowlisted(self):
        keys = {
            (item.resource_kind, item.operation_id)
            for item in FOUNDATIONDB_CONTROL_OPERATIONS
        }
        self.assertTrue({
            ('backup', 'start'), ('backup', 'pause'),
            ('backup', 'delete'), ('restore', 'start'),
            ('restore', 'abort'),
        }.issubset(keys))
        request = {
            'resource_kind': 'backup', 'operation_id': 'start',
            'target_resource': None,
            'draft': {
                'destination_url': 'file:///srv/backups/inventory',
                'tag_name': 'nightly', 'snapshot_interval_seconds': 3600,
                'stop_when_restorable': False,
            },
            '_provider_route': {
                'backup_container_allowlist': ['file:///srv/backups/'],
            },
        }
        plan = FoundationDBBackend._control_command(request)
        self.assertEqual('fdbbackup', plan['tool'])
        self.assertEqual([
            'start', '-C', '__cluster_file__', '-t', 'nightly',
            '-d', 'file:///srv/backups/inventory', '-s', '3600',
            '--no-stop-when-done',
        ], plan['arguments'])
        request['_provider_route']['backup_container_allowlist'] = [
            'file:///var/other/'
        ]
        with self.assertRaisesRegex(
            NativeDistributedError, 'outside the endpoint allowlist'
        ):
            FoundationDBBackend._control_command(request)

    def test_tidb_control_plane_is_typed_and_provider_compiled(self):
        keys = {
            (item.resource_kind, item.operation_id)
            for item in TIDB_CONTROL_OPERATIONS
        }
        self.assertTrue({
            ('placement-policy', 'create'),
            ('placement-policy', 'alter'),
            ('resource-group', 'create'),
            ('table', 'configure_placement'),
            ('table', 'set_tiflash_replica'),
            ('job', 'cancel'),
        }.issubset(keys))
        catalog = TIDB_ADMIN.catalog({
            'objects': [
                {'resource_kind': kind, 'operations': []}
                for kind in {
                    item.resource_kind for item in TIDB_CONTROL_OPERATIONS
                }
            ],
        })
        operations = [
            operation
            for resource in catalog['objects']
            for operation in resource['operations']
        ]
        self.assertTrue(all(operation['control_plane'] for operation in (
            operations
        )))
        self.assertFalse(any(
            field['control'] == 'code'
            for operation in operations
            for field in operation['form']['fields']
        ))

        placement = TIDB_ADMIN.plan({
            'resource_kind': 'placement-policy',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'canada_primary', 'primary_region': 'ca-central-1',
                'regions': 'ca-central-1,ca-west-1', 'followers': 4,
                'schedule': 'majority_in_primary',
            },
            '_provider_route': {'host': '127.0.0.1', 'port': 4000},
        })
        statement = placement['provider_payload']['compiled'][
            'statements'][0]
        self.assertEqual(
            'CREATE PLACEMENT POLICY `canada_primary` '
            'PRIMARY_REGION = %s REGIONS = %s FOLLOWERS = %s SCHEDULE = %s',
            statement['source'],
        )
        self.assertEqual(4, len(statement['parameters']))
        self.assertTrue(placement['impact']['data_movement_possible'])

        group = TIDB_ADMIN.plan({
            'resource_kind': 'resource-group',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'batch', 'ru_mode': 'limited', 'ru_per_sec': 2500,
                'priority': 'LOW', 'burstable': True,
            },
            '_provider_route': {'host': '127.0.0.1', 'port': 4000},
        })
        statement = group['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            'CREATE RESOURCE GROUP `batch` RU_PER_SEC = %s, '
            'PRIORITY = LOW, BURSTABLE = TRUE', statement['source']
        )
        self.assertEqual((2500,), tuple(statement['parameters']))

    def test_tidb_control_plane_rejects_untyped_priority(self):
        with self.assertRaises(RelationalClientError):
            TIDB_ADMIN.plan({
                'resource_kind': 'resource-group',
                'operation_id': 'create',
                'target_resource': None,
                'draft': {
                    'name': 'bad', 'ru_mode': 'unlimited',
                    'priority': 'HIGH; DROP DATABASE inventory',
                },
                '_provider_route': {'host': '127.0.0.1', 'port': 4000},
            })

    def test_tidb_br_recovery_is_typed_allowlisted_and_native(self):
        route = {
            'host': '127.0.0.1', 'port': 4000,
            'br_path': '/bin/true',
            'br_pd_addresses': '127.0.0.1:2379,127.0.0.2:2379',
            'br_storage_allowlist': 'local:///srv/cdeadmin/backups/',
        }
        plan = TIDB_ADMIN.plan({
            'resource_kind': 'database', 'operation_id': 'backup_br',
            'target_resource': {
                'resource_id': 'database:inventory',
                'display_path': ['inventory'],
            },
            'draft': {
                'storage_uri': 'local:///srv/cdeadmin/backups/inventory',
            },
            '_provider_route': route,
        })
        action = plan['provider_payload']['compiled']['provider_action']
        self.assertEqual([
            'backup', 'db',
            '--storage=local:///srv/cdeadmin/backups/inventory',
            '--db=inventory', '--redact-info-log=true',
        ], action['arguments'])
        self.assertNotIn(
            'arguments', plan['command_preview']['statements'][0])
        result = SimpleNamespace(returncode=0, stdout='ok', stderr='')
        with patch(
            'pgadmin.cdeadmin.providers.tidb.provider.subprocess.run',
            return_value=result,
        ) as runner:
            response = run_tidb_br(route, action['arguments'])
        command = runner.call_args.args[0]
        self.assertEqual('/usr/bin/true', command[0])
        self.assertIn(
            '--pd=127.0.0.1:2379,127.0.0.2:2379', command)
        self.assertFalse(response['automatic_mutation_retry'])

    def test_tidb_br_restore_has_exact_provider_cancellation(self):
        plan = TIDB_ADMIN.plan({
            'resource_kind': 'table', 'operation_id': 'restore_br',
            'target_resource': {
                'resource_id': 'table:inventory.widgets',
                'display_path': ['inventory', 'widgets'],
            },
            'draft': {
                'storage_uri': 's3://approved/inventory',
            },
            '_provider_route': {
                'host': '127.0.0.1', 'port': 4000,
                'br_storage_allowlist': ['s3://approved/'],
            },
        })
        action = plan['provider_payload']['compiled']['provider_action']
        self.assertEqual([
            'abort', 'restore', 'table',
            '--storage=s3://approved/inventory', '--db=inventory',
            '--table=widgets', '--redact-info-log=true',
        ], action['cancel_arguments'])
        blocked = dict(plan)
        self.assertTrue(blocked['receipt']['provider_finality_authority'])

    def test_tidb_ticdc_lifecycle_is_allowlisted_and_redacted(self):
        route = {
            'host': '127.0.0.1', 'port': 4000,
            'ticdc_path': '/bin/true',
            'ticdc_server': 'http://127.0.0.1:8300',
            'ticdc_sink_allowlist': 'kafka://broker.internal/',
        }
        plan = TIDB_ADMIN.plan({
            'resource_kind': 'changefeed', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'changefeed_id': 'orders-canada',
                'sink_uri': 'kafka://broker.internal/orders',
                'start_ts': '438156275634929669',
            },
            '_provider_route': route,
        })
        action = plan['provider_payload']['compiled']['provider_action']
        self.assertEqual([
            'cli', 'changefeed', 'create', '--changefeed-id',
            'orders-canada', '--sink-uri',
            'kafka://broker.internal/orders', '--start-ts',
            '438156275634929669',
        ], action['arguments'])
        self.assertNotIn(
            'kafka://broker.internal/orders', json.dumps(
                plan['command_preview']))
        result = SimpleNamespace(returncode=0, stdout='{}', stderr='')
        with patch(
            'pgadmin.cdeadmin.providers.tidb.provider.subprocess.run',
            return_value=result,
        ) as runner:
            response = run_tidb_cdc(route, action['arguments'])
        command = runner.call_args.args[0]
        self.assertEqual('/usr/bin/true', command[0])
        self.assertEqual([
            '--server', 'http://127.0.0.1:8300',
        ], command[-2:])
        self.assertFalse(response['automatic_mutation_retry'])

    def test_tidb_ticdc_sink_and_identifier_fail_closed(self):
        base = {
            'resource_kind': 'changefeed', 'operation_id': 'create',
            'target_resource': None,
            '_provider_route': {
                'host': '127.0.0.1', 'port': 4000,
                'ticdc_sink_allowlist': ['kafka://approved/'],
            },
        }
        with self.assertRaisesRegex(
                RelationalClientError, 'outside the endpoint allowlist'):
            TIDB_ADMIN.plan({
                **base,
                'draft': {
                    'changefeed_id': 'orders',
                    'sink_uri': 'kafka://unapproved/orders',
                },
            })
        with self.assertRaisesRegex(
                RelationalClientError, 'control-plane request is invalid'):
            TIDB_ADMIN.plan({
                **base,
                'draft': {
                    'changefeed_id': 'orders;remove-all',
                    'sink_uri': 'kafka://approved/orders',
                },
            })

    def test_cockroach_control_plane_is_typed_and_provider_compiled(self):
        keys = {
            (item.resource_kind, item.operation_id)
            for item in COCKROACH_CONTROL_OPERATIONS
        }
        self.assertTrue({
            ('database', 'configure_zone'), ('job', 'pause'),
            ('job', 'cancel'), ('database', 'backup'),
            ('database', 'set_primary_region'),
            ('database', 'add_region'), ('database', 'drop_region'),
            ('database', 'set_secondary_region'),
            ('database', 'drop_secondary_region'),
            ('database', 'set_survival_goal'),
            ('database', 'set_placement'), ('table', 'set_locality'),
            ('cluster', 'restore_database'),
        }.issubset(keys))
        catalog = COCKROACH_ADMIN.catalog({
            'objects': [
                {'resource_kind': kind, 'operations': []}
                for kind in {
                    item.resource_kind
                    for item in COCKROACH_CONTROL_OPERATIONS
                }
            ],
        })
        self.assertEqual(
            'cdeadmin.distributed-control-plane.v1',
            catalog['distributed_control_plane_contract'],
        )
        operations = [
            operation
            for resource in catalog['objects']
            for operation in resource['operations']
        ]
        self.assertTrue(all(
            operation['automatic_mutation_retry'] is False
            for operation in operations
        ))
        self.assertFalse(any(
            field['control'] == 'code'
            for operation in operations
            for field in operation['form']['fields']
        ))

        zone = COCKROACH_ADMIN.plan({
            'resource_kind': 'database',
            'operation_id': 'configure_zone',
            'target_resource': {
                'resource_id': 'database:inventory',
                'display_path': ['inventory'],
            },
            'draft': {
                'num_replicas': 5,
                'num_voters': 3,
                'constraints': ['+region=ca-central-1'],
            },
            '_provider_route': {'host': '127.0.0.1', 'port': 26257},
        })
        statement = zone['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            'ALTER DATABASE "inventory" CONFIGURE ZONE USING '
            'num_replicas = %s, num_voters = %s, constraints = %s',
            statement['source'],
        )
        self.assertEqual(3, len(statement['parameters']))
        self.assertTrue(zone['impact']['data_movement_possible'])

        pause = COCKROACH_ADMIN.plan({
            'resource_kind': 'job', 'operation_id': 'pause',
            'target_resource': {
                'resource_id': 'job:71', 'display_path': ['71'],
            },
            'draft': {'reason': 'operator maintenance'},
            '_provider_route': {'host': '127.0.0.1', 'port': 26257},
        })
        statement = pause['provider_payload']['compiled']['statements'][0]
        self.assertEqual('PAUSE JOB %s WITH REASON = %s', statement['source'])
        self.assertEqual((71, 'operator maintenance'),
                         tuple(statement['parameters']))

    def test_cockroach_multi_region_controls_compile_exact_sql(self):
        database = {
            'resource_id': 'database:inventory',
            'display_path': ['inventory'],
        }
        route = {'host': '127.0.0.1', 'port': 26257}
        cases = (
            ('set_primary_region', {'region': 'ca-central-1'},
             'ALTER DATABASE "inventory" SET PRIMARY REGION '
             '"ca-central-1"'),
            ('add_region', {'region': 'us-east-1'},
             'ALTER DATABASE "inventory" ADD REGION "us-east-1"'),
            ('drop_region', {'region': 'us-east-1'},
             'ALTER DATABASE "inventory" DROP REGION "us-east-1"'),
            ('set_secondary_region', {'region': 'ca-west-1'},
             'ALTER DATABASE "inventory" SET SECONDARY REGION '
             '"ca-west-1"'),
            ('drop_secondary_region', {},
             'ALTER DATABASE "inventory" DROP SECONDARY REGION'),
            ('set_survival_goal', {'goal': 'region'},
             'ALTER DATABASE "inventory" SURVIVE REGION FAILURE'),
            ('set_placement', {'policy': 'restricted'},
             'ALTER DATABASE "inventory" PLACEMENT RESTRICTED'),
        )
        for operation, draft, source in cases:
            with self.subTest(operation=operation):
                plan = COCKROACH_ADMIN.plan({
                    'resource_kind': 'database',
                    'operation_id': operation,
                    'target_resource': database,
                    'draft': draft, '_provider_route': route,
                })
                statement = plan['provider_payload']['compiled'][
                    'statements'][0]
                self.assertEqual(source, statement['source'])
                self.assertEqual((), statement['parameters'])
                self.assertTrue(plan['impact']['data_movement_possible'])

        table = {
            'resource_id': 'table:public.customers',
            'display_path': ['public', 'customers'],
        }
        plan = COCKROACH_ADMIN.plan({
            'resource_kind': 'table', 'operation_id': 'set_locality',
            'target_resource': table,
            'draft': {
                'locality': 'regional_by_row',
                'region_column': 'home_region',
            }, '_provider_route': route,
        })
        statement = plan['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            'ALTER TABLE "public"."customers" SET LOCALITY '
            'REGIONAL BY ROW AS "home_region"', statement['source']
        )
        self.assertEqual((), statement['parameters'])

    def test_cockroach_backup_destination_must_be_allowlisted(self):
        request = {
            'resource_kind': 'database', 'operation_id': 'backup',
            'target_resource': {
                'resource_id': 'database:inventory',
                'display_path': ['inventory'],
            },
            'draft': {
                'destination_uri': 'nodelocal://1/cdeadmin/inventory',
                'revision_history': 'default',
            },
            '_provider_route': {
                'host': '127.0.0.1', 'port': 26257,
                'backup_destination_allowlist': [
                    'nodelocal://1/cdeadmin/',
                ],
            },
        }
        plan = COCKROACH_ADMIN.plan(request)
        statement = plan['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            'BACKUP DATABASE "inventory" INTO %s', statement['source']
        )
        blocked = dict(request)
        blocked['_provider_route'] = {
            'host': '127.0.0.1', 'port': 26257,
            'backup_destination_allowlist': ['nodelocal://1/approved/'],
        }
        with self.assertRaisesRegex(
            RelationalClientError, 'outside the endpoint allowlist'
        ):
            COCKROACH_ADMIN.plan(blocked)

    def test_cockroach_node_control_uses_bounded_native_cli(self):
        plan = COCKROACH_ADMIN.plan({
            'resource_kind': 'node', 'operation_id': 'decommission',
            'target_resource': {
                'resource_id': 'node:7', 'display_path': ['7'],
            },
            'draft': {'checks': 'strict', 'wait': 'none'},
            '_provider_route': {
                'host': '127.0.0.1', 'port': 26257,
                'cockroach_path': '/bin/true',
                'cockroach_insecure': True,
            },
        })
        compiled = plan['provider_payload']['compiled']
        self.assertEqual(
            ['node', 'decommission', '--checks=strict', '--wait=none', '7'],
            compiled['provider_action']['arguments'],
        )
        self.assertNotIn(
            'arguments', plan['command_preview']['statements'][0])
        result = SimpleNamespace(returncode=0, stdout='[]', stderr='')
        with patch(
            'pgadmin.cdeadmin.providers.cockroachdb.provider.subprocess.run',
            return_value=result,
        ) as runner:
            response = run_cockroach_node(
                plan['provider_payload']['route'],
                compiled['provider_action']['arguments'],
            )
        command = runner.call_args.args[0]
        self.assertEqual('/usr/bin/true', command[0])
        self.assertEqual('7', command[-1])
        self.assertIn('--host=127.0.0.1:26257', command)
        self.assertIn('--format=json', command)
        self.assertIn('--insecure', command)
        self.assertFalse(response['automatic_mutation_retry'])

    def test_cockroach_changefeed_is_typed_parameterized_and_allowlisted(self):
        secret_uri = 'kafka://broker/cdeadmin?token=secret-canary'
        plan = COCKROACH_ADMIN.plan({
            'resource_kind': 'table',
            'operation_id': 'create_changefeed',
            'target_resource': {
                'resource_id': 'table:inventory.public.widgets',
                'display_path': ['inventory', 'public', 'widgets'],
            },
            'draft': {
                'sink_uri': secret_uri, 'initial_scan': 'only',
                'format': 'json', 'envelope': 'wrapped',
                'resolved_interval': '5s',
            },
            '_provider_route': {
                'host': '127.0.0.1', 'port': 26257,
                'changefeed_sink_allowlist': 'kafka://broker/cdeadmin',
            },
        })
        statement = plan['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            'CREATE CHANGEFEED FOR TABLE '
            '"inventory"."public"."widgets" INTO %s WITH '
            'initial_scan = %s, format = %s, envelope = %s, resolved = %s',
            statement['source'],
        )
        self.assertEqual(secret_uri, statement['parameters'][0])
        self.assertNotIn(
            'secret-canary',
            plan['command_preview']['statements'][0]['source'],
        )
        with self.assertRaisesRegex(RelationalClientError, 'table name'):
            COCKROACH_ADMIN.plan({
                'resource_kind': 'changefeed',
                'operation_id': 'add_table',
                'target_resource': {
                    'resource_id': 'changefeed:71',
                    'display_path': ['71'],
                },
                'draft': {
                    'table_name': 'widgets; DROP DATABASE inventory',
                },
                '_provider_route': {
                    'host': '127.0.0.1', 'port': 26257,
                },
            })

    def test_cockroach_node_post_state_uses_exact_membership(self):
        document = [
            {'id': '7', 'membership': 'decommissioned'},
            {'id': '8', 'membership': 'active'},
        ]
        self.assertEqual(
            'decommissioned', cockroach_node_membership(document, 7))
        self.assertEqual('active', cockroach_node_membership(document, 8))
        self.assertIsNone(cockroach_node_membership(document, 9))

    def test_native_key_value_forms_and_identities_are_binary_safe(self):
        catalog = key_value_catalog({
            'objects': [{
                'resource_kind': 'key-range',
                'operations': [
                    {'operation_id': 'insert'},
                    {'operation_id': 'update'},
                    {'operation_id': 'delete'},
                ],
            }],
        }, {'key-range'}, 'native-test')
        operations = {
            item['operation_id']: item
            for item in catalog['objects'][0]['operations']
        }
        self.assertEqual(
            {'key', 'key_encoding', 'value', 'value_encoding'},
            {
                field['field_id']
                for field in operations['insert']['form']['fields']
            },
        )
        self.assertEqual(
            b'\xff\x00', decode_value({
                'key': '/wA=', 'key_encoding': 'base64',
            }, 'key')
        )
        validation = validate_key_value_request({
            'resource_kind': 'key-range', 'operation_id': 'update',
            'draft': {'value': 'next', 'selector': {}},
        }, {'key-range'})
        self.assertEqual(
            'provider_identity_required', validation['errors'][0]['code']
        )
        identities = KeyIdentityStore()
        route = {'host': 'one', 'credential_reference_id': 'vault-one'}
        target = {'resource_kind': 'key-range', 'display_path': ['all']}
        token = identities.issue(route, target, b'key', b'value')
        self.assertEqual(
            (b'key', b'value'), identities.consume(
                {**route, 'credential_reference_id': 'vault-two'},
                target, {'identity_token': token},
            )
        )
        with self.assertRaisesRegex(
            NativeDistributedError, 'stale or invalid'
        ):
            identities.consume(
                route, target, {'identity_token': token}
            )

    def test_ignite_visual_cache_rows_use_provider_native_cas(self):
        _IgniteClient.caches = {}
        backend = IgniteBackend(module=SimpleNamespace(Client=_IgniteClient))
        route = {'host': 'ignite.example', 'port': 10800}
        target = {
            'resource_kind': 'cache', 'display_name': 'gate',
            'display_path': ['gate'],
        }

        def apply(operation, draft):
            return backend.apply_admin_operation({
                'provider_payload': {
                    'resource_kind': 'cache',
                    'operation_id': operation,
                    'target_resource': target,
                    'draft': draft,
                    '_provider_route': route,
                },
            })

        apply('create', {'name': 'gate', 'backups_number': 1})
        apply('insert', {'key': 'one', 'value': 'first'})
        page = backend.read_admin_rows({
            '_provider_route': route, 'target_resource': target, 'limit': 20,
        })
        token = page['rows'][0]['identity_token']
        apply('update', {
            'selector': {'identity_token': token}, 'value': 'second',
        })
        page = backend.read_admin_rows({
            '_provider_route': route, 'target_resource': target, 'limit': 20,
        })
        self.assertEqual('second', page['rows'][0]['values']['value'])
        apply('delete', {
            'selector': {
                'identity_token': page['rows'][0]['identity_token'],
            },
            'confirmation': 'gate',
        })
        self.assertEqual({}, _IgniteClient.caches['gate'].values)
        apply('drop', {'confirmation': 'gate'})

        catalog = NativeDistributedClient(
            IGNITE, backend, {'cache': {'insert', 'update', 'delete'}},
        ).visual_admin_catalog({
            'objects': [{
                'resource_kind': 'cache',
                'operations': [
                    {'operation_id': 'insert'},
                    {'operation_id': 'update'},
                    {'operation_id': 'delete'},
                ],
            }],
        })
        fields = {
            operation['operation_id']: {
                field['field_id'] for field in operation['form']['fields']
            }
            for operation in catalog['objects'][0]['operations']
        }
        self.assertEqual({'key', 'value'}, fields['insert'])
        self.assertEqual({'selector', 'value'}, fields['update'])
        self.assertEqual({'selector', 'confirmation'}, fields['delete'])

    def test_reference_profile_set_is_exact(self):
        profiles = (
            IGNITE, COCKROACHDB, DOLT, FOUNDATIONDB,
            IMMUDB, TIDB, TIKV, VITESS, YUGABYTEDB,
        )
        self.assertEqual({
            'apache_ignite': '2.17.0',
            'cockroachdb': '26.1.3',
            'dolt': '1.86.6',
            'foundationdb': '7.3.77',
            'immudb': '1.11.0',
            'tidb': '8.5.6',
            'tikv': '8.5.6',
            'vitess': '23.0.3',
            'yugabytedb': '2025.2.2.2',
        }, {profile.engine_id: profile.exact_version for profile in profiles})
        self.assertEqual(len(profiles), len({
            profile.provider_id for profile in profiles
        }))
        self.assertIn('view', VITESS.resource_kinds)
        self.assertEqual(
            frozenset({'inspect', 'create', 'alter', 'drop'}),
            VITESS_ADMIN.dialect.supported['view'],
        )
        self.assertEqual(
            frozenset({'inspect', 'create', 'drop'}),
            VITESS_ADMIN.dialect.supported['vindex'],
        )

    def test_immudb_control_plane_covers_native_database_and_security(self):
        keys = {
            (item.resource_kind, item.operation_id)
            for item in IMMUDB_CONTROL_OPERATIONS
        }
        self.assertTrue({
            ('database', 'create'),
            ('database', 'update_settings'),
            ('database', 'load'),
            ('database', 'unload'),
            ('database', 'flush_index'),
            ('database', 'compact_index'),
            ('database', 'truncate_history'),
            ('user', 'create'),
            ('user', 'change_password'),
            ('user', 'set_active'),
            ('permission', 'grant'),
            ('permission', 'revoke'),
            ('permission', 'grant_sql'),
            ('permission', 'revoke_sql'),
        }.issubset(keys))
        self.assertFalse(any(
            field['control'] == 'code'
            for operation in IMMUDB_CONTROL_OPERATIONS
            for field in operation.fields
        ))
        self.assertTrue(IMMUDB_ADMIN.supports('database', 'create'))
        self.assertTrue(IMMUDB_ADMIN.supports('permission', 'grant_sql'))

    def test_immudb_database_settings_do_not_reset_implicit_values(self):
        update = compile_immudb_control({
            'resource_kind': 'database',
            'operation_id': 'update_settings',
            'target_resource': {
                'resource_id': 'database:analytics',
                'display_path': ['analytics'],
            },
            'draft': {
                'max_io_concurrency': 8,
                'index_flush_threshold': 1000,
                'aht_sync_threshold': 32,
                'history_retention_period_ms': 86400000,
                'replication_wait_for_indexing': True,
            },
        })
        settings = update['provider_action']['body']['settings']
        self.assertEqual({'value': 8}, settings['maxIOConcurrency'])
        self.assertEqual(
            {'value': 1000}, settings['indexSettings']['flushThreshold']
        )
        self.assertEqual(
            {'value': 32}, settings['ahtSettings']['syncThreshold']
        )
        self.assertEqual(
            {'value': 86400000},
            settings['truncationSettings']['retentionPeriod'],
        )
        self.assertEqual(
            {'value': True},
            settings['replicationSettings']['waitForIndexing'],
        )
        self.assertNotIn('autoload', settings)
        self.assertNotIn('excludeCommitTime', settings)

    def test_immudb_replica_and_sql_privilege_values_fail_closed(self):
        with self.assertRaisesRegex(
            RelationalClientError, 'primary connection fields'
        ):
            compile_immudb_control({
                'resource_kind': 'database', 'operation_id': 'create',
                'target_resource': None,
                'draft': {'name': 'replica', 'is_replica': True},
            })
        with self.assertRaisesRegex(
            RelationalClientError, 'SQL privileges'
        ):
            compile_immudb_control({
                'resource_kind': 'permission', 'operation_id': 'grant_sql',
                'target_resource': None,
                'draft': {
                    'username': 'operator', 'database': 'analytics',
                    'privileges': ['SELECT', 'EXECUTE'],
                },
            })
        grant = compile_immudb_control({
            'resource_kind': 'permission', 'operation_id': 'grant_sql',
            'target_resource': None,
            'draft': {
                'username': 'operator', 'database': 'analytics',
                'privileges': ['SELECT', 'INSERT'],
            },
        })
        self.assertEqual({
            'action': 'GRANT', 'username': 'operator',
            'database': 'analytics', 'privileges': ['SELECT', 'INSERT'],
        }, grant['provider_action']['body'])

    def test_immudb_identity_joins_wire_and_exact_native_version(self):
        config = RelationalClientConfig(
            profile=IMMUDB, module_name='fake', version_query='SELECT 1',
            connect_arguments=postgresql_route,
            metadata_reader=lambda _connection, _request: [],
        )
        connection = _Connection(('PostgreSQL 14.0 (immudb compatible)',))
        calls = []

        def opener(request, **_options):
            calls.append(request.full_url)
            return _Response({'version': '1.11.0', 'startedAt': 42})

        client = ImmudbDBAPIClient(
            config, module=_ConnectorModule(connection), opener=opener
        )
        identity = client.runtime_identity({
            'route': {
                'host': '127.0.0.1', 'web_host': '127.0.0.1',
                'web_port': 8080,
            },
        }, handle=connection)
        self.assertEqual('immudb', identity['engine_id'])
        self.assertEqual('1.11.0', identity['version'])
        self.assertEqual('immudb:1.11.0:42', identity['build_id'])
        self.assertEqual(
            'http://127.0.0.1:8080/api/serverinfo', calls[0]
        )

    def test_immudb_name_matching_does_not_decode_database_names(self):
        self.assertTrue(immudb_contains_name({
            'databases': [{'name': 'dGVzdA=='}],
        }, 'dGVzdA=='))
        self.assertFalse(immudb_contains_name({
            'databases': [{'name': 'dGVzdA=='}],
        }, 'test'))
        self.assertTrue(immudb_contains_name({
            'users': [{'user': 'dGVzdA=='}],
        }, 'test'))

    def test_vitess_table_and_vindex_commands_are_provider_constructed(self):
        route = {'host': '127.0.0.1', 'database': 'inventory'}
        create = VITESS_ADMIN.plan({
            'resource_kind': 'table', 'operation_id': 'create',
            'draft': {
                'name': 'widgets', 'parent': 'inventory',
                'columns': [{
                    'name': 'id', 'type': 'BIGINT', 'nullable': False,
                    'primary_key': True,
                }],
                'constraints': [], 'register_in_vschema': True,
                'vindex_name': 'xxhash', 'vindex_columns': ['id'],
            },
            '_provider_route': route,
        })
        statements = create['provider_payload']['compiled']['statements']
        self.assertEqual(2, len(statements))
        self.assertEqual(
            'ALTER VSCHEMA ON `widgets` ADD VINDEX `xxhash` (`id`)',
            statements[1]['source'],
        )
        drop = VITESS_ADMIN.plan({
            'resource_kind': 'table', 'operation_id': 'drop',
            'target_resource': {
                'resource_kind': 'table',
                'display_path': ['inventory', 'widgets'],
            },
            'draft': {
                'drop_vschema_registration': True,
                'confirmation': 'widgets',
            },
            '_provider_route': route,
        })
        statements = drop['provider_payload']['compiled']['statements']
        self.assertEqual(
            'ALTER VSCHEMA DROP TABLE `widgets`',
            statements[0]['source'],
        )
        self.assertEqual(
            'DROP TABLE `inventory`.`widgets`', statements[1]['source']
        )
        vindex = VITESS_ADMIN.plan({
            'resource_kind': 'vindex', 'operation_id': 'create',
            'draft': {
                'name': 'lookup_by_name', 'vindex_type': 'lookup_hash',
                'parameters': {
                    'table': 'lookup.names', 'autocommit': True,
                },
            },
            '_provider_route': route,
        })
        source = vindex['provider_payload']['compiled']['statements'][0]
        self.assertIn(
            'ALTER VSCHEMA CREATE VINDEX `lookup_by_name` '
            'USING `lookup_hash`', source['source']
        )
        self.assertIn("`table`='lookup.names'", source['source'])
        self.assertIn('`autocommit`=true', source['source'])

        sequence = VITESS_ADMIN.plan({
            'resource_kind': 'sequence', 'operation_id': 'create',
            'draft': {
                'name': 'widget_seq', 'parent': 'sequence_keyspace',
                'start': 100, 'cache': 50,
            },
            '_provider_route': {
                'host': '127.0.0.1', 'database': 'sequence_keyspace',
            },
        })['provider_payload']['compiled']['statements']
        self.assertEqual(3, len(sequence))
        self.assertIn("COMMENT 'vitess_sequence'", sequence[0]['source'])
        self.assertEqual((100, 50), tuple(sequence[1]['parameters']))
        self.assertEqual(
            'ALTER VSCHEMA ADD SEQUENCE '
            '`sequence_keyspace`.`widget_seq`',
            sequence[2]['source'],
        )
        altered = VITESS_ADMIN.plan({
            'resource_kind': 'sequence', 'operation_id': 'alter',
            'target_resource': {
                'resource_kind': 'sequence', 'display_name': 'widget_seq',
                'display_path': ['sequence_keyspace', 'widget_seq'],
            },
            'draft': {'restart': 500, 'cache': 25},
            '_provider_route': {
                'host': '127.0.0.1', 'database': 'sequence_keyspace',
            },
        })['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            'UPDATE `sequence_keyspace`.`widget_seq` '
            'SET next_id = %s, cache = %s WHERE id = 0',
            altered['source'],
        )
        self.assertEqual((500, 25), tuple(altered['parameters']))
        self.assertFalse(VITESS_ADMIN.supports('sequence', 'rename'))

        index = VITESS_ADMIN.plan({
            'resource_kind': 'index', 'operation_id': 'create',
            'draft': {
                'name': 'widgets_value_idx', 'parent': 'inventory',
                'table': 'inventory.widgets', 'columns': ['value'],
                'unique': False,
            },
            '_provider_route': route,
        })
        self.assertEqual(
            'CREATE INDEX `widgets_value_idx` ON '
            '`inventory`.`widgets` (`value`)',
            index['provider_payload']['compiled']['statements'][0]['source'],
        )

    def test_version_parsers_require_engine_specific_identity(self):
        self.assertEqual(
            '26.1.3', cockroachdb_version((
                'CockroachDB CCL v26.1.3 (x86_64-unknown-linux-gnu)',
            )),
        )
        self.assertEqual('1.86.6', dolt_version(('1.86.6',)))
        self.assertEqual(
            '8.5.6', tidb_version((
                'Release Version: v8.5.6\nEdition: Community',
            )),
        )
        self.assertEqual('23.0.3', vitess_version(('23.0.3-SNAPSHOT',)))
        self.assertEqual(
            '2025.2.2.2', yugabytedb_version((
                'PostgreSQL 15.2-YB-2025.2.2.2-b0 on x86_64, '
                'YugabyteDB',
            )),
        )
        with self.assertRaises(RelationalClientError):
            cockroachdb_version(('PostgreSQL 18.3',))
        with self.assertRaises(RelationalClientError):
            yugabytedb_version(('PostgreSQL 15.2',))

    def test_wire_routes_drop_secrets_and_non_wire_controls(self):
        route = {
            'host': 'db.example', 'port': 3306, 'user': 'operator',
            'database': 'inventory', 'password': 'secret-canary',
            'credential_reference_id': 'vault-one',
            'principal_reference': 'operator-one',
            'vtgate_http_port': 15001, 'server_implementation': 'ignored',
        }
        mysql = mysql_route(route)
        postgres = postgresql_route(route)
        self.assertEqual('db.example', mysql['host'])
        self.assertEqual('inventory', postgres['dbname'])
        for result in (mysql, postgres):
            self.assertNotIn('password', result)
            self.assertNotIn('credential_reference_id', result)
            self.assertNotIn('principal_reference', result)
            self.assertNotIn('server_implementation', result)
            self.assertNotIn('vtgate_http_port', result)

    def test_wire_routes_forward_full_security_session_and_pool_controls(self):
        mysql = mysql_route({
            'route_id': 'mysql-route-one', 'host': 'db.example',
            'auth_plugin': 'caching_sha2_password',
            'ssl_ca': '/certs/ca.pem', 'ssl_cert': '/certs/client.pem',
            'ssl_key': '/certs/client.key', 'ssl_verify_cert': True,
            'ssl_verify_identity': True,
            'tls_versions': ['TLSv1.2', 'TLSv1.3'], 'compress': True,
            'read_timeout': 20, 'write_timeout': 30,
            'autocommit': False, 'time_zone': '+00:00',
            'pool_size': 8, 'pool_reset_session': True,
            'failover': [{'host': 'db-b.example', 'port': 3306}],
        })
        self.assertEqual('caching_sha2_password', mysql['auth_plugin'])
        self.assertEqual('/certs/client.pem', mysql['ssl_cert'])
        self.assertEqual(['TLSv1.2', 'TLSv1.3'], mysql['tls_versions'])
        self.assertEqual(8, mysql['pool_size'])
        self.assertTrue(mysql['pool_name'].startswith('cde_'))
        self.assertEqual('db-b.example', mysql['failover'][0]['host'])

        postgres = postgresql_route({
            'host': 'pg-a,pg-b', 'port': '5432,5432',
            'sslmode': 'verify-full', 'sslrootcert': '/certs/ca.pem',
            'sslcert': '/certs/client.pem', 'sslkey': '/certs/client.key',
            'channel_binding': 'require', 'gssencmode': 'prefer',
            'require_auth': 'scram-sha-256,gss,oauth',
            'target_session_attrs': 'read-write',
            'load_balance_hosts': 'random', 'connect_timeout': 12,
            'keepalives': True, 'application_name': 'CDEadmin',
        })
        self.assertEqual('verify-full', postgres['sslmode'])
        self.assertEqual('require', postgres['channel_binding'])
        self.assertEqual('read-write', postgres['target_session_attrs'])
        self.assertEqual('random', postgres['load_balance_hosts'])

    def test_postgresql_wire_pool_returns_handles_and_closes_generation(self):
        observed = {'returned': [], 'closed': False}
        connection = _Connection()

        class Pool:
            check_connection = object()

            def __init__(self, **options):
                observed['options'] = options

            def getconn(self):
                return connection

            def putconn(self, value):
                observed['returned'].append(value)

            def close(self):
                observed['closed'] = True

        module = SimpleNamespace(connect=lambda **_kwargs: _Connection())
        connector = _PsycopgPoolConnector(
            module, 'pool-generation-one',
            SimpleNamespace(ConnectionPool=Pool),
        )
        lease = connector(
            host='pg.example', dbname='inventory', pool_enabled=True,
            pool_min_size=2, pool_max_size=9,
            pool_acquisition_timeout=12, pool_check_connection=True,
        )
        self.assertEqual(2, observed['options']['min_size'])
        self.assertEqual(9, observed['options']['max_size'])
        self.assertEqual(12, observed['options']['timeout'])
        self.assertIs(Pool.check_connection, observed['options']['check'])
        self.assertNotIn('pool_enabled', observed['options']['kwargs'])
        lease.close()
        lease.close()
        self.assertEqual([connection], observed['returned'])
        connector.close()
        self.assertTrue(observed['closed'])

    def test_postgresql_transaction_defaults_use_typed_driver_methods(self):
        observed = {}
        connection = SimpleNamespace(
            set_autocommit=lambda value: observed.setdefault(
                'autocommit', value
            ),
            set_isolation_level=lambda value: observed.setdefault(
                'isolation', value
            ),
            set_read_only=lambda value: observed.setdefault(
                'read_only', value
            ),
            set_deferrable=lambda value: observed.setdefault(
                'deferrable', value
            ),
        )
        _initialize_postgresql_connection(connection, {
            'autocommit': False,
            'transaction_isolation': 'SERIALIZABLE',
            'transaction_read_only': 'read-only',
            'transaction_deferrable': 'deferrable',
        })
        self.assertFalse(observed['autocommit'])
        self.assertEqual('SERIALIZABLE', observed['isolation'].name)
        self.assertTrue(observed['read_only'])
        self.assertTrue(observed['deferrable'])

    def test_mysql_wire_transaction_defaults_are_allowlisted(self):
        statements = []
        cursor = SimpleNamespace(
            execute=statements.append, close=lambda: None
        )
        connection = SimpleNamespace(cursor=lambda: cursor)
        _initialize_mysql_connection(connection, {
            'transaction_isolation': 'READ COMMITTED',
        })
        self.assertEqual([
            'SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED'
        ], statements)
        with self.assertRaisesRegex(
            RelationalClientError, 'transaction isolation is invalid'
        ):
            _initialize_mysql_connection(connection, {
                'transaction_isolation': 'READ COMMITTED; DROP DATABASE x',
            })

    def test_relational_client_initializes_from_unfiltered_route(self):
        observed = {}
        connection = SimpleNamespace(
            close=lambda: None,
        )
        module = SimpleNamespace(
            connect=lambda **kwargs: (
                observed.setdefault('connector', kwargs), connection
            )[1]
        )
        config = RelationalClientConfig(
            profile=COCKROACHDB,
            module_name='unused',
            version_query='SELECT version()',
            connect_arguments=postgresql_route,
            metadata_reader=lambda _connection, _request: [],
            connection_initializer=lambda _connection, route: (
                observed.setdefault('initializer', route)
            ),
        )
        client = RelationalDBAPIClient(config, module)
        handle = client.open_session({'route': {
            'host': 'pg.example',
            'transaction_isolation': 'SERIALIZABLE',
        }})
        self.assertIs(connection, handle)
        self.assertNotIn(
            'transaction_isolation', observed['connector']
        )
        self.assertEqual(
            'SERIALIZABLE',
            observed['initializer']['transaction_isolation'],
        )

    def test_manifests_expose_provider_owned_connection_controls(self):
        manifest_paths = {
            'apache_ignite': 'apache_ignite/provider_manifest.json',
            'cockroachdb': 'cockroachdb/provider_manifest.json',
            'dolt': 'dolt/provider_manifest.json',
            'foundationdb': 'foundationdb/provider_manifest.json',
            'tidb': 'tidb/provider_manifest.json',
            'tikv': 'tikv/provider_manifest.json',
            'vitess': 'vitess/provider_manifest.json',
            'yugabytedb': 'yugabytedb/provider_manifest.json',
        }
        for engine, relative in manifest_paths.items():
            with self.subTest(engine=engine):
                manifest = json.loads(
                    (PROVIDER_ROOT / relative).read_text(encoding='utf-8')
                )
                self.assertEqual(
                    engine, manifest['identity']['provider_id'].split('.')[-1]
                )
                self.assertEqual('experimental', manifest['support_state'])
                qualified = {
                    'apache_ignite', 'cockroachdb', 'dolt', 'foundationdb',
                    'tidb', 'tikv', 'vitess', 'yugabytedb',
                }
                expected = (
                    'exact_runtime_qualified' if engine in qualified else
                    'exact_runtime_not_available'
                )
                self.assertEqual(
                    expected, manifest['provenance']['runtime_state'])
                if engine == 'yugabytedb':
                    self.assertEqual(
                        'ysql',
                        manifest['registration']['interface']['interface_id'],
                    )
                    self.assertEqual(
                        'passed',
                        manifest['provenance']['dual_interface_gate'],
                    )
        vitess = json.loads(
            (PROVIDER_ROOT / manifest_paths['vitess']).read_text(
                encoding='utf-8'
            )
        )
        fields = {
            item['route_key']
            for item in vitess['registration']['connection_fields']
        }
        self.assertIn('vtgate_http_port', fields)
        self.assertIn('vtgate_http_tls_mode', fields)

    def test_distributed_sql_admin_uses_wire_family_not_engine_name(self):
        route = {'host': '127.0.0.1'}
        tidb = TIDB_ADMIN.plan({
            'resource_kind': 'user', 'operation_id': 'create',
            'draft': {'name': 'analyst', 'password': 'secret-canary'},
            '_provider_route': route,
        })
        source = tidb['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            "CREATE USER 'analyst'@'%' IDENTIFIED BY 'secret-canary'",
            source['source'],
        )
        self.assertNotIn('secret-canary', source['preview_source'])

        cockroach = COCKROACH_ADMIN.plan({
            'resource_kind': 'user', 'operation_id': 'create',
            'draft': {'name': 'analyst', 'password': 'secret-canary'},
            '_provider_route': route,
        })
        source = cockroach['provider_payload']['compiled']['statements'][0]
        self.assertEqual(
            'CREATE USER "analyst" PASSWORD \'secret-canary\'',
            source['source'],
        )
        self.assertNotIn('secret-canary', source['preview_source'])
        self.assertFalse(VITESS_ADMIN.supports('user', 'create'))
        self.assertTrue(VITESS_ADMIN.supports('user', 'inspect'))

    def test_tidb_relational_admin_compiles_native_object_syntax(self):
        route = {'host': '127.0.0.1', 'port': 4000}
        index = TIDB_ADMIN.plan({
            'resource_kind': 'index', 'operation_id': 'create',
            'draft': {
                'name': 'widgets_value_idx', 'parent': 'app',
                'table': 'app.widgets', 'columns': ['value'],
                'unique': False,
            },
            '_provider_route': route,
        })
        self.assertEqual(
            'CREATE INDEX `widgets_value_idx` ON '
            '`app`.`widgets` (`value`)',
            index['provider_payload']['compiled']['statements'][0]['source'],
        )
        view = TIDB_ADMIN.plan({
            'resource_kind': 'view', 'operation_id': 'alter',
            'target_resource': {
                'resource_kind': 'view', 'display_name': 'active_widgets',
                'display_path': ['app', 'active_widgets'],
            },
            'draft': {'query': 'SELECT id FROM widgets'},
            '_provider_route': route,
        })
        self.assertEqual(
            'CREATE OR REPLACE VIEW `app`.`active_widgets` AS '
            'SELECT id FROM widgets',
            view['provider_payload']['compiled']['statements'][0]['source'],
        )
        self.assertTrue(TIDB_ADMIN.supports('sequence', 'alter'))
        self.assertFalse(TIDB_ADMIN.supports('sequence', 'rename'))

    def test_cockroach_relational_admin_compiles_native_object_syntax(self):
        route = {'host': '127.0.0.1', 'port': 26257}
        index = COCKROACH_ADMIN.plan({
            'resource_kind': 'index', 'operation_id': 'create',
            'draft': {
                'name': 'widgets_value_idx', 'parent': 'public',
                'table': 'public.widgets', 'columns': ['value'],
                'unique': False,
            },
            '_provider_route': route,
        })
        self.assertEqual(
            'CREATE INDEX "widgets_value_idx" ON '
            '"public"."widgets" ("value")',
            index['provider_payload']['compiled']['statements'][0]['source'],
        )
        trigger = COCKROACH_ADMIN.plan({
            'resource_kind': 'trigger', 'operation_id': 'create',
            'draft': {
                'name': 'widgets_trigger', 'parent': 'public',
                'table': 'public.widgets', 'timing': 'AFTER',
                'events': ['INSERT'],
                'body': (
                    'FOR EACH ROW EXECUTE FUNCTION public.on_widget()'
                ),
            },
            '_provider_route': route,
        })
        self.assertEqual(
            'CREATE TRIGGER "widgets_trigger" AFTER INSERT ON '
            '"public"."widgets" FOR EACH ROW EXECUTE FUNCTION '
            'public.on_widget()',
            trigger['provider_payload']['compiled']['statements'][0][
                'source'],
        )
        constraint = COCKROACH_ADMIN.plan({
            'resource_kind': 'constraint', 'operation_id': 'drop',
            'target_resource': {
                'resource_id': 'constraint:public:widgets:value_unique',
                'display_name': 'value_unique',
                'display_path': ['public', 'widgets', 'value_unique'],
                'extensions': {'cockroachdb': {'native': {
                    'constraint_type': 'UNIQUE',
                }}},
            },
            'draft': {'confirmation': 'drop-constraint'},
            '_provider_route': route,
        })
        self.assertEqual(
            'DROP INDEX "public"."value_unique" CASCADE',
            constraint['provider_payload']['compiled']['statements'][0][
                'source'],
        )

    def test_optional_catalog_query_rolls_back_before_next_probe(self):
        connection = SimpleNamespace(rollbacks=0)
        connection.rollback = lambda: setattr(
            connection, 'rollbacks', connection.rollbacks + 1
        )
        cursor = SimpleNamespace(connection=connection)

        def fail(_source, _parameters):
            raise RuntimeError('optional catalog is unavailable')

        cursor.execute = fail
        self.assertEqual([], optional_rows(cursor, 'SELECT unavailable'))
        self.assertEqual(1, connection.rollbacks)

    def test_distributed_sql_grid_discovers_wire_family_primary_keys(self):
        mysql_connection = _Connection()
        mysql_connection.cursor_value.row = ('id',)
        mysql_connection.cursor_value.fetchall = lambda: [('id',)]
        self.assertEqual(
            ['id'], TIDB_ADMIN._primary_key(
                mysql_connection, ('inventory', 'widgets')
            )
        )
        mysql_sql = mysql_connection.cursor_value.executed[0][0]
        self.assertIn('information_schema.KEY_COLUMN_USAGE', mysql_sql)

        postgres_connection = _Connection()
        postgres_connection.cursor_value.fetchall = lambda: [('id',)]
        self.assertEqual(
            ['id'], COCKROACH_ADMIN._primary_key(
                postgres_connection, ('public', 'widgets')
            )
        )
        postgres_sql = postgres_connection.cursor_value.executed[0][0]
        self.assertIn('information_schema.table_constraints', postgres_sql)

    def test_vitess_identity_joins_sql_and_native_observations(self):
        connection = _Connection()
        module = _ConnectorModule(connection)
        calls = []

        def opener(request, timeout, context):
            calls.append((request.full_url, timeout, context))
            return _Response({
                'BuildVersion': '23.0.3', 'BuildGitRev': 'revision-one',
            })

        config = RelationalClientConfig(
            profile=VITESS, module_name='fake', version_query='SELECT 1',
            connect_arguments=mysql_route,
            metadata_reader=lambda _connection, _request: [],
        )
        client = VitessDBAPIClient(config, module=module, opener=opener)
        identity = client.runtime_identity({'route': {
            'host': '127.0.0.1', 'port': 15306, 'user': 'operator',
            'vtgate_http_port': 15001,
        }})
        self.assertEqual('vitess', identity['engine_id'])
        self.assertEqual('23.0.3', identity['version'])
        self.assertEqual('vitess:23.0.3:revision-one', identity['build_id'])
        self.assertEqual('SELECT 1', connection.cursor_value.executed[0][0])
        self.assertEqual(
            'http://127.0.0.1:15001/debug/vars', calls[0][0]
        )
        self.assertNotIn('vtgate_http_port', module.arguments)
        self.assertTrue(connection.closed)

    def test_vitess_identity_rejects_non_vitess_and_oversized_payloads(self):
        config = RelationalClientConfig(
            profile=VITESS, module_name='fake', version_query='SELECT 1',
            connect_arguments=mysql_route,
            metadata_reader=lambda _connection, _request: [],
        )
        client = VitessDBAPIClient(
            config, module=_ConnectorModule(_Connection()),
            opener=lambda *_args, **_kwargs: _Response({
                'Version': 'MySQL 9.7.0',
            }),
        )
        with self.assertRaisesRegex(
            RelationalClientError, 'Vitess version is unavailable'
        ):
            client.runtime_identity({'route': {'host': '127.0.0.1'}})

        class LargeResponse(_Response):
            def __init__(self):
                self.payload = b'x' * (
                    VitessDBAPIClient.MAX_IDENTITY_BYTES + 1
                )

        client = VitessDBAPIClient(
            config, module=_ConnectorModule(_Connection()),
            opener=lambda *_args, **_kwargs: LargeResponse(),
        )
        with self.assertRaisesRegex(
            RelationalClientError, 'exceeds size limit'
        ):
            client.runtime_identity({'route': {'host': '127.0.0.1'}})

    def test_native_contract_preserves_provider_transaction_authority(self):
        backend = _Backend()
        client = NativeDistributedClient(
            TIKV, backend, {'raw-key': {'inspect'}},
        )
        handle = client.open_session({'route': {'host': 'one'}})
        observation = client.describe_transaction(handle)
        self.assertEqual('opaque', observation['native_state'])
        self.assertTrue(observation['driver_observation_only'])
        self.assertFalse(observation['finality_interpreted_by_common_code'])
        token = client.execute(handle, {
            'source': json.dumps({'operation': 'get', 'key': 'one'}),
            'parameters': {'consistency': 'provider-native'},
        })
        result = client.describe_result(token)
        self.assertEqual('key_value', result['result_kind'])
        self.assertEqual('one', result['payload']['entries'][0]['key'])
        self.assertEqual(
            'get', backend.commands[0][0]['operation']
        )
        applied = client.apply_admin_operation({'provider_payload': {}})
        self.assertFalse(
            applied['transaction_finality_interpreted_by_common_code']
        )
        client.close()
        self.assertTrue(backend.closed)

    def test_native_contract_fails_closed_on_invalid_results(self):
        backend = _Backend()
        client = NativeDistributedClient(
            FOUNDATIONDB, backend, {'key': {'inspect'}},
        )
        handle = client.open_session({'route': {'cluster_file': 'one'}})
        with self.assertRaisesRegex(
            NativeDistributedError, 'command source is required'
        ):
            client.execute(handle, {'source': ''})
        backend.execute = lambda *_args: NativeResult(
            'key_value', 'entries',
            [None] * (NativeDistributedClient.MAX_RECORDS + 1), {}, {},
        )
        with self.assertRaisesRegex(
            NativeDistributedError, 'record limit'
        ):
            client.execute(handle, {'source': '{"operation":"get"}'})

    def test_tikv_helper_boundary_encodes_and_preserves_finality(self):
        calls = []

        def runner(arguments, **options):
            request = json.loads(options['input'])
            calls.append((arguments, options, request))
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    'records': [{
                        'key_base64': 'a2V5',
                        'value_base64': 'dmFsdWU=',
                        'found': True,
                    }],
                    'native': {
                        'operation': 'get',
                        'transaction_model': 'raw-single-key',
                    },
                    'provider_finality_only': True,
                }).encode('utf-8'),
                stderr=b'',
            )

        backend = TiKVBackend(helper_path=sys.executable, runner=runner)
        route = backend.open_session({'route': {
            'pd_endpoints': '127.0.0.1:2379', 'api_version': 2,
        }})
        result = backend.execute(
            route, {'operation': 'get', 'key': 'key'}, {})
        self.assertEqual('key', result.records[0]['key'])
        self.assertEqual('value', result.records[0]['value'])
        self.assertEqual('a2V5', calls[0][2]['key_base64'])
        self.assertEqual(2, calls[0][2]['api_version'])
        self.assertTrue(result.native['provider_finality_only'])
        self.assertFalse(
            result.native['automatic_mutation_retry_by_cdeadmin'])

    def test_tikv_control_plane_is_typed_and_pd_compiled(self):
        keys = {
            (item.resource_kind, item.operation_id)
            for item in TIKV_CONTROL_OPERATIONS
        }
        self.assertTrue({
            ('cluster', 'set_all_store_limits'),
            ('cluster', 'remove_tombstones'),
            ('keyspace', 'create'),
            ('keyspace', 'update_config'),
            ('keyspace', 'tombstone'),
            ('store', 'evict_leaders'),
            ('store', 'mark_offline'),
            ('store', 'set_labels'),
            ('store', 'set_weights'),
            ('store', 'set_limit'),
            ('region', 'transfer_leader'),
            ('region', 'remove_peer'),
            ('region', 'scatter'),
            ('region', 'merge'),
            ('placement-rule', 'create'),
            ('scheduler', 'create'),
            ('scheduler', 'pause'),
            ('scheduler', 'resume'),
        }.issubset(keys))
        self.assertFalse(any(
            field['control'] == 'code'
            for operation in TIKV_CONTROL_OPERATIONS
            for field in operation.fields
        ))
        backend = TiKVBackend(helper_path=sys.executable)
        request = {
            'resource_kind': 'region',
            'operation_id': 'transfer_leader',
            'target_resource': {
                'resource_id': 'region:91', 'display_path': ['91'],
            },
            'draft': {'to_store_id': 3},
            '_provider_route': {'pd_endpoints': ['127.0.0.1:2379']},
        }
        plan = backend.plan_admin_operation(request)
        self.assertEqual(
            'POST', plan['command_preview']['requests'][0]['method']
        )
        self.assertEqual(
            '/pd/api/v1/operators',
            plan['command_preview']['requests'][0]['path'],
        )
        self.assertNotIn('body', plan['command_preview']['requests'][0])
        self.assertFalse(
            plan['receipt']['automatic_mutation_retry'])

        calls = []

        def opener(http_request, **_options):
            calls.append({
                'method': http_request.get_method(),
                'path': http_request.selector,
                'body': json.loads(http_request.data or b'{}'),
            })
            return _Response({'status': 'created'})

        backend.opener = opener
        result = backend.apply_admin_operation({
            'provider_payload': request,
        })
        self.assertTrue(result['accepted'])
        self.assertEqual({
            'name': 'transfer-leader', 'region_id': 91,
            'to_store_id': 3,
        }, calls[0]['body'])
        self.assertFalse(
            result['automatic_mutation_retry_by_cdeadmin'])

    def test_tikv_extended_pd_controls_compile_exact_requests(self):
        backend = TiKVBackend(helper_path=sys.executable)
        target = {'resource_id': 'store:7', 'display_path': ['7']}
        cases = (
            ({
                'resource_kind': 'store', 'operation_id': 'mark_offline',
                'target_resource': target, 'draft': {},
            }, 'POST', '/pd/api/v1/store/7/state?state=Offline', None),
            ({
                'resource_kind': 'store', 'operation_id': 'set_labels',
                'target_resource': target,
                'draft': {'labels': {'zone': 'ca-east'}, 'force': True},
            }, 'POST', '/pd/api/v1/store/7/label?force=true',
             {'zone': 'ca-east'}),
            ({
                'resource_kind': 'store', 'operation_id': 'set_weights',
                'target_resource': target,
                'draft': {'leader_weight': 1.5, 'region_weight': 2},
            }, 'POST', '/pd/api/v1/store/7/weight',
             {'leader': 1.5, 'region': 2}),
            ({
                'resource_kind': 'store', 'operation_id': 'set_limit',
                'target_resource': target,
                'draft': {'rate': 15, 'limit_type': 'remove-peer'},
            }, 'POST', '/pd/api/v1/store/7/limit',
             {'rate': 15, 'type': 'remove-peer'}),
            ({
                'resource_kind': 'region', 'operation_id': 'split',
                'target_resource': {
                    'resource_id': 'region:91', 'display_path': ['91'],
                },
                'draft': {'policy': 'usekey', 'keys': ['6162']},
            }, 'POST', '/pd/api/v1/operators', {
                'name': 'split-region', 'region_id': 91,
                'policy': 'usekey', 'keys': ['6162'],
            }),
            ({
                'resource_kind': 'region', 'operation_id': 'merge',
                'target_resource': {
                    'resource_id': 'region:91', 'display_path': ['91'],
                },
                'draft': {'target_region_id': 92},
            }, 'POST', '/pd/api/v1/operators', {
                'name': 'merge-region', 'region_id': 91,
                'target_region_id': 92,
            }),
            ({
                'resource_kind': 'scheduler', 'operation_id': 'pause',
                'target_resource': {
                    'resource_id': 'scheduler:balance-leader-scheduler',
                    'display_path': ['balance-leader-scheduler'],
                },
                'draft': {'delay_seconds': 300},
            }, 'POST',
             '/pd/api/v1/schedulers/balance-leader-scheduler',
             {'delay': 300}),
        )
        for request, method, path, body in cases:
            with self.subTest(operation=request['operation_id']):
                compiled = backend._control_request(request)['requests'][0]
                self.assertEqual(method, compiled['method'])
                self.assertEqual(path, compiled['path'])
                self.assertEqual(body, compiled.get('body'))

    def test_tikv_pd_mutation_accepts_bounded_scalar_acknowledgement(self):
        backend = TiKVBackend(
            helper_path=sys.executable,
            opener=lambda *_args, **_kwargs: _Response('Update store label'),
        )
        route = backend._route({'route': {
            'pd_endpoints': ['127.0.0.1:2379'],
        }})
        response = backend._pd_mutate(
            route, 'POST', '/pd/api/v1/store/1/label', {'zone': 'east'}
        )
        self.assertEqual(
            {'provider_value': 'Update store label'},
            response['provider_response'],
        )
        self.assertTrue(response['provider_finality_only'])
        self.assertFalse(response['automatic_mutation_retry_by_cdeadmin'])

    def test_tikv_cluster_and_keyspace_controls_compile_exact_requests(self):
        backend = TiKVBackend(helper_path=sys.executable)
        keyspace = {
            'resource_id': 'keyspace:tenant-one',
            'display_path': ['tenant-one'],
        }
        cases = (
            ({
                'resource_kind': 'cluster',
                'operation_id': 'set_all_store_limits',
                'target_resource': {
                    'resource_id': 'cluster:TiKV',
                    'display_path': ['TiKV'],
                },
                'draft': {
                    'rate': 20, 'limit_type': 'add-peer',
                    'labels': {'zone': 'east'},
                },
            }, 'POST', '/pd/api/v1/stores/limit', {
                'rate': 20, 'type': 'add-peer',
                'labels': {'zone': 'east'},
            }),
            ({
                'resource_kind': 'cluster',
                'operation_id': 'remove_tombstones',
                'target_resource': {
                    'resource_id': 'cluster:TiKV',
                    'display_path': ['TiKV'],
                },
                'draft': {},
            }, 'DELETE', '/pd/api/v1/stores/remove-tombstone', None),
            ({
                'resource_kind': 'keyspace', 'operation_id': 'create',
                'draft': {
                    'name': 'tenant-one', 'config': {'tier': 'gold'},
                }, '_provider_route': {
                    'pd_endpoints': ['127.0.0.1:2379'], 'api_version': 2,
                },
            }, 'POST', '/pd/api/v2/keyspaces', {
                'name': 'tenant-one', 'config': {'tier': 'gold'},
            }),
            ({
                'resource_kind': 'keyspace',
                'operation_id': 'update_config',
                'target_resource': keyspace,
                'draft': {'config': {'tier': 'silver', 'obsolete': None}},
                '_provider_route': {
                    'pd_endpoints': ['127.0.0.1:2379'], 'api_version': 2,
                },
            }, 'PATCH', '/pd/api/v2/keyspaces/tenant-one/config', {
                'config': {'tier': 'silver', 'obsolete': None},
            }),
            ({
                'resource_kind': 'keyspace', 'operation_id': 'disable',
                'target_resource': keyspace, 'draft': {},
                '_provider_route': {
                    'pd_endpoints': ['127.0.0.1:2379'], 'api_version': 2,
                },
            }, 'PUT', '/pd/api/v2/keyspaces/tenant-one/state', {
                'state': 'DISABLED',
            }),
        )
        for request, method, path, body in cases:
            with self.subTest(operation=request['operation_id']):
                compiled = backend._control_request(request)['requests'][0]
                self.assertEqual(method, compiled['method'])
                self.assertEqual(path, compiled['path'])
                self.assertEqual(body, compiled.get('body'))

        request = {
            'resource_kind': 'keyspace', 'operation_id': 'create',
            'draft': {'name': 'tenant-one', 'config': {}},
            '_provider_route': {
                'pd_endpoints': ['127.0.0.1:2379'], 'api_version': 1,
            },
        }
        with self.assertRaisesRegex(
            NativeDistributedError, 'requires API version 2'
        ):
            backend._control_request(request)

    def test_tikv_split_control_requires_exact_policy_and_hex_keys(self):
        backend = TiKVBackend(helper_path=sys.executable)
        request = {
            'resource_kind': 'region', 'operation_id': 'split',
            'target_resource': {
                'resource_id': 'region:91', 'display_path': ['91'],
            },
            'draft': {'policy': 'usekey', 'keys': ['not-hex']},
        }
        checked = backend.validate_admin_operation(request)
        self.assertEqual(
            'invalid_control_plane_request', checked['errors'][0]['code']
        )

    def test_tikv_placement_rule_validation_is_fail_closed(self):
        backend = TiKVBackend(helper_path=sys.executable)
        request = {
            'resource_kind': 'placement-rule', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'group_id': 'cdeadmin', 'rule_id': 'canada',
                'role': 'voter', 'count': 3,
                'constraints': [{
                    'key': 'region', 'op': 'shell', 'values': ['ca'],
                }],
            },
            '_provider_route': {'pd_endpoints': ['127.0.0.1:2379']},
        }
        checked = backend.validate_admin_operation(request)
        self.assertFalse(not checked['errors'])
        with self.assertRaisesRegex(
            NativeDistributedError, 'label constraint'
        ):
            backend.plan_admin_operation(request)

    def test_tikv_helper_boundary_rejects_invalid_control_data(self):
        backend = TiKVBackend(helper_path=sys.executable)
        with self.assertRaisesRegex(
            NativeDistributedError, 'endpoint is invalid'
        ):
            backend.open_session({'route': {
                'pd_endpoints': ['user:secret@127.0.0.1:2379'],
            }})
        with self.assertRaisesRegex(
            NativeDistributedError, 'API version'
        ):
            backend.open_session({'route': {
                'pd_endpoints': ['127.0.0.1:2379'], 'api_version': 3,
            }})
        route = backend._route({'route': {
            'pd_endpoints': ['127.0.0.1:2379'],
        }})
        with self.assertRaisesRegex(
            NativeDistributedError, 'scan limit'
        ):
            backend._helper_request(route, {
                'operation': 'scan', 'limit': 10001,
            })

    def test_tikv_helper_boundary_rejects_common_finality_claim(self):
        def runner(_arguments, **_options):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    'records': [], 'native': {},
                    'provider_finality_only': False,
                }).encode('utf-8'),
                stderr=b'',
            )

        backend = TiKVBackend(helper_path=sys.executable, runner=runner)
        route = backend.open_session({'route': {
            'pd_endpoints': ['127.0.0.1:2379'],
        }})
        with self.assertRaisesRegex(
            NativeDistributedError, 'finality boundary'
        ):
            backend.execute(route, {'operation': 'get', 'key': 'key'}, {})


if __name__ == '__main__':
    unittest.main()
