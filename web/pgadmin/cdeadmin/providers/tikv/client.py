"""Pinned TiKV client-go helper boundary for native key/value access."""

from __future__ import annotations

import base64
import binascii
import copy
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import urllib.parse
import urllib.request

from pgadmin.cdeadmin.visual_admin import (
    ControlPlaneCatalog,
    ControlPlaneOperation,
    control_plane_field as cp_field,
)
from ..distributed_sql import resource
from ..native_key_value import (
    KeyIdentityStore, decode_value, key_value_catalog,
    validate_key_value_request,
)
from ..native_distributed import NativeDistributedError, NativeResult


def _choice(field_id, label, values, **options):
    return cp_field(
        field_id, label, 'select', options=[
            {'value': value, 'label': title} for value, title in values
        ], **options
    )


CONTROL_OPERATIONS = (
    ControlPlaneOperation(
        'cluster', 'set_all_store_limits', 'Set all store rate limits',
        'admin', 'maintenance_admin', (
            cp_field('rate', 'Operations per minute', 'number', True,
                     minimum=0.000001),
            _choice('limit_type', 'Operation type', (
                ('add-peer', 'Add peer'),
                ('remove-peer', 'Remove peer'),
                ('both', 'Add and remove peer'),
            ), required=True, default='both'),
            cp_field('labels', 'Only stores with labels', 'json', False,
                     default={}, json_type='object'),
        ), impact_scope='cluster', post_state_required=False
    ),
    ControlPlaneOperation(
        'cluster', 'remove_tombstones', 'Remove tombstone store records',
        'destructive', 'topology_admin', impact_scope='cluster',
        post_state_required=False
    ),
    ControlPlaneOperation(
        'keyspace', 'create', 'Create keyspace', 'admin',
        'topology_admin', (
            cp_field('name', 'Keyspace name', 'text', True,
                     max_length=256, pattern=r'[A-Za-z0-9_.-]+'),
            cp_field('config', 'Initial configuration', 'json', False,
                     default={}, json_type='object'),
        ), target_required=False, impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'keyspace', 'update_config', 'Update keyspace configuration',
        'admin', 'topology_admin', (
            cp_field('config', 'Configuration merge patch', 'json', True,
                     json_type='object'),
        ), impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'keyspace', 'enable', 'Enable keyspace', 'admin',
        'topology_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'keyspace', 'disable', 'Disable keyspace', 'destructive',
        'topology_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'keyspace', 'archive', 'Archive keyspace', 'destructive',
        'topology_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'keyspace', 'tombstone', 'Tombstone keyspace', 'destructive',
        'topology_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'store', 'evict_leaders', 'Evict leaders from store', 'admin',
        'maintenance_admin', impact_scope='cluster', long_running=True,
        cancellable=True
    ),
    ControlPlaneOperation(
        'store', 'mark_offline', 'Mark store offline', 'destructive',
        'topology_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'store', 'bring_up', 'Bring store up', 'admin',
        'topology_admin', impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'store', 'set_labels', 'Set store labels', 'admin',
        'topology_admin', (
            cp_field('labels', 'Store labels', 'json', True,
                     json_type='object'),
            cp_field('force', 'Replace existing labels', 'boolean', False,
                     default=False),
        ), impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'store', 'delete_label', 'Delete store label', 'destructive',
        'topology_admin', (
            cp_field('label_key', 'Label key', 'text', True,
                     max_length=256, pattern=r'[A-Za-z0-9_.:/-]+'),
        ), impact_scope='cluster'
    ),
    ControlPlaneOperation(
        'store', 'set_weights', 'Set store scheduling weights', 'admin',
        'topology_admin', (
            cp_field('leader_weight', 'Leader weight', 'number', True,
                     minimum=0),
            cp_field('region_weight', 'Region weight', 'number', True,
                     minimum=0),
        ), impact_scope='cluster', post_state_required=False
    ),
    ControlPlaneOperation(
        'store', 'set_limit', 'Set store scheduling rate limit', 'admin',
        'maintenance_admin', (
            cp_field('rate', 'Operations per minute', 'number', True,
                     minimum=0.000001),
            _choice('limit_type', 'Operation type', (
                ('add-peer', 'Add peer'),
                ('remove-peer', 'Remove peer'),
                ('both', 'Add and remove peer'),
            ), required=True, default='both'),
        ), impact_scope='cluster', post_state_required=False
    ),
    ControlPlaneOperation(
        'region', 'transfer_leader', 'Transfer Region leader', 'admin',
        'topology_admin', (
            cp_field('to_store_id', 'Destination store ID', 'number', True,
                     minimum=1),
        ), impact_scope='region', long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'region', 'add_peer', 'Add Region voting peer', 'admin',
        'replication_admin', (
            cp_field('store_id', 'Store ID', 'number', True, minimum=1),
        ), impact_scope='region', long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'region', 'add_learner', 'Add Region learner', 'admin',
        'replication_admin', (
            cp_field('store_id', 'Store ID', 'number', True, minimum=1),
        ), impact_scope='region', long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'region', 'remove_peer', 'Remove Region peer', 'destructive',
        'replication_admin', (
            cp_field('store_id', 'Store ID', 'number', True, minimum=1),
        ), impact_scope='region', long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'region', 'transfer_peer', 'Transfer Region peer', 'admin',
        'replication_admin', (
            cp_field('from_store_id', 'Source store ID', 'number', True,
                     minimum=1),
            cp_field('to_store_id', 'Destination store ID', 'number', True,
                     minimum=1),
        ), impact_scope='region', long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'region', 'split', 'Split Region', 'admin', 'topology_admin',
        (
            _choice('policy', 'Split policy', (
                ('approximate', 'Approximate'),
                ('scan', 'Scan'),
                ('usekey', 'Explicit keys'),
            ), required=True, default='approximate'),
            cp_field('keys', 'Split keys (hex)', 'json', False, default=[],
                     json_type='array', visible_when={
                         'field_id': 'policy', 'equals': 'usekey',
                     }),
        ), impact_scope='region', long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'region', 'scatter', 'Scatter Region peers', 'admin',
        'topology_admin', (
            cp_field('group', 'Scatter group', 'text', False,
                     max_length=256),
        ), impact_scope='region', long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'region', 'merge', 'Merge Region into adjacent Region',
        'destructive', 'topology_admin', (
            cp_field('target_region_id', 'Target Region ID', 'number', True,
                     minimum=1),
        ), impact_scope='region', long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'placement-rule', 'create', 'Create placement rule', 'admin',
        'topology_admin', (
            cp_field('group_id', 'Rule group', 'text', True,
                     max_length=256,
                     pattern=r'[A-Za-z0-9_.:-]+'),
            cp_field('rule_id', 'Rule ID', 'text', True, max_length=256,
                     pattern=r'[A-Za-z0-9_.:-]+'),
            _choice('role', 'Replica role', (
                ('voter', 'Voter'), ('leader', 'Leader'),
                ('follower', 'Follower'), ('learner', 'Learner'),
            ), required=True, default='voter'),
            cp_field('count', 'Replica count', 'number', True,
                     minimum=1, maximum=15),
            cp_field('start_key', 'Start key (hex)', 'text', False,
                     max_length=8192, pattern=r'[0-9A-Fa-f]*'),
            cp_field('end_key', 'End key (hex)', 'text', False,
                     max_length=8192, pattern=r'[0-9A-Fa-f]*'),
            cp_field('location_labels', 'Location labels', 'json', False,
                     default=[], json_type='array'),
            cp_field('constraints', 'Label constraints', 'json', False,
                     default=[], json_type='array'),
            cp_field('isolation_level', 'Isolation label', 'text', False,
                     max_length=256),
            cp_field('rule_index', 'Rule index', 'number', False,
                     minimum=0),
            cp_field('override', 'Override lower-index rules', 'boolean',
                     False, default=False),
        ), target_required=False, impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'placement-rule', 'alter', 'Alter placement rule', 'admin',
        'topology_admin', (
            _choice('role', 'Replica role', (
                ('voter', 'Voter'), ('leader', 'Leader'),
                ('follower', 'Follower'), ('learner', 'Learner'),
            ), required=True, default='voter'),
            cp_field('count', 'Replica count', 'number', True,
                     minimum=1, maximum=15),
            cp_field('start_key', 'Start key (hex)', 'text', False,
                     max_length=8192, pattern=r'[0-9A-Fa-f]*'),
            cp_field('end_key', 'End key (hex)', 'text', False,
                     max_length=8192, pattern=r'[0-9A-Fa-f]*'),
            cp_field('location_labels', 'Location labels', 'json', False,
                     default=[], json_type='array'),
            cp_field('constraints', 'Label constraints', 'json', False,
                     default=[], json_type='array'),
            cp_field('isolation_level', 'Isolation label', 'text', False,
                     max_length=256),
            cp_field('rule_index', 'Rule index', 'number', False,
                     minimum=0),
            cp_field('override', 'Override lower-index rules', 'boolean',
                     False, default=False),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'placement-rule', 'drop', 'Drop placement rule', 'destructive',
        'topology_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'scheduler', 'create', 'Enable scheduler', 'admin',
        'maintenance_admin', (
            _choice('scheduler_name', 'Scheduler', (
                ('balance-leader-scheduler', 'Balance leaders'),
                ('balance-region-scheduler', 'Balance Regions'),
                ('balance-hot-region-scheduler', 'Balance hot Regions'),
                ('shuffle-leader-scheduler', 'Shuffle leaders'),
            ), required=True),
        ), target_required=False, impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'scheduler', 'drop', 'Disable scheduler', 'destructive',
        'maintenance_admin', impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'scheduler', 'pause', 'Pause scheduler', 'admin',
        'maintenance_admin', (
            cp_field('delay_seconds', 'Pause duration (seconds)', 'number',
                     True, minimum=1, maximum=86400),
        ), impact_scope='cluster', long_running=True
    ),
    ControlPlaneOperation(
        'scheduler', 'resume', 'Resume scheduler', 'admin',
        'maintenance_admin', impact_scope='cluster'
    ),
)

CONTROL_CATALOG = ControlPlaneCatalog('tikv', CONTROL_OPERATIONS)

ADMIN_OPERATIONS = {
    'cluster': {'inspect'}, 'store': {'inspect'},
    'region': {'inspect'}, 'peer': {'inspect'},
    'keyspace': {'inspect'},
    'key-range': {'inspect', 'insert', 'update', 'delete'},
    'raw-key': {'inspect', 'insert', 'update', 'delete'},
    'ttl': {'inspect', 'create', 'alter', 'drop'},
    'transaction': {'inspect'}, 'lock': {'inspect'},
    'placement-rule': {'inspect'}, 'scheduler': {'inspect'},
    'configuration': {'inspect'}, 'backup': {'inspect'},
    'restore': {'inspect'}, 'import-job': {'inspect'},
    'coprocessor': {'inspect'},
}
for _control_operation in CONTROL_OPERATIONS:
    ADMIN_OPERATIONS.setdefault(
        _control_operation.resource_kind, {'inspect'}
    ).add(_control_operation.operation_id)


class TiKVBackend:
    """Forward bounded operations to the provider-owned TiKV helper.

    The helper uses TiKV's native client-go transaction implementation. This
    Python boundary neither retries mutations nor interprets commit finality.
    """

    CLIENT_REVISION = 'v2.0.8-0.20260319064229-5cba4fc2f3a9'
    DEFAULT_HELPER_PATH = '/usr/libexec/cdeadmin/cdeadmin-tikv-helper'
    MAX_HTTP_BYTES = 4 * 1024 * 1024
    MAX_HELPER_BYTES = 12 * 1024 * 1024
    MAX_RECORDS = 10000
    MAX_ENDPOINTS = 16

    def __init__(self, helper_path=None, runner=None, opener=None):
        self.helper_path = str(
            helper_path or os.environ.get('CDEADMIN_TIKV_HELPER_PATH') or
            self.DEFAULT_HELPER_PATH
        )
        self.runner = runner or subprocess.run
        self.opener = opener or urllib.request.urlopen
        self._row_identities = KeyIdentityStore()

    @classmethod
    def _endpoints(cls, route):
        endpoints = route.get('pd_endpoints')
        if endpoints is None:
            endpoints = [f'{route.get("host", "127.0.0.1")}:2379']
        elif isinstance(endpoints, str):
            endpoints = [item.strip() for item in endpoints.split(',')]
        if not isinstance(endpoints, (list, tuple)) or not (
                1 <= len(endpoints) <= cls.MAX_ENDPOINTS):
            raise NativeDistributedError(
                'TiKV PD endpoints must contain 1 to 16 entries')
        result = []
        for endpoint in endpoints:
            endpoint = str(endpoint).strip()
            parsed = urllib.parse.urlsplit(f'//{endpoint}')
            try:
                port = parsed.port
            except ValueError as exc:
                raise NativeDistributedError(
                    'TiKV PD endpoint is invalid') from exc
            if (not endpoint or len(endpoint) > 512 or
                    parsed.username is not None or
                    parsed.password is not None or not parsed.hostname or
                    port is None or parsed.path or parsed.query or
                    parsed.fragment or any(
                        character in endpoint for character in '\x00\r\n\t'
                    )):
                raise NativeDistributedError('TiKV PD endpoint is invalid')
            result.append(endpoint)
        return result

    @classmethod
    def _route(cls, request):
        route = request.get('route') or request.get('_provider_route')
        if not isinstance(route, dict):
            raise NativeDistributedError('TiKV route is required')
        result = copy.deepcopy(route)
        result['pd_endpoints'] = cls._endpoints(result)
        try:
            result['api_version'] = int(result.get('api_version', 1))
        except (TypeError, ValueError) as exc:
            raise NativeDistributedError(
                'TiKV API version must be 1 or 2') from exc
        if result['api_version'] not in (1, 2):
            raise NativeDistributedError('TiKV API version must be 1 or 2')
        enable_ttl = result.get(
            'enable_ttl', result['api_version'] == 2)
        if not isinstance(enable_ttl, bool):
            raise NativeDistributedError(
                'TiKV TTL enablement must be boolean')
        result['enable_ttl'] = (
            True if result['api_version'] == 2 else enable_ttl
        )
        result['pd_http_scheme'] = str(
            result.get('pd_http_scheme', 'http')).lower()
        if result['pd_http_scheme'] not in ('http', 'https'):
            raise NativeDistributedError(
                'TiKV PD HTTP scheme must be http or https')
        timeout = result.get('operation_timeout', 30)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not (
                1 <= timeout <= 3600):
            raise NativeDistributedError(
                'TiKV operation timeout is outside approved bounds')
        result['operation_timeout'] = timeout
        result['transaction_mode'] = result.get(
            'transaction_mode', 'optimistic')
        if result['transaction_mode'] not in {'optimistic', 'pessimistic'}:
            raise NativeDistributedError(
                'TiKV transaction mode is invalid')
        certificate = result.get('tls_certificate')
        key = result.get('tls_key')
        if bool(certificate) != bool(key):
            raise NativeDistributedError(
                'TiKV TLS certificate and key must be provided together')
        for field in ('tls_ca', 'tls_certificate', 'tls_key'):
            if result.get(field):
                path = Path(str(result[field])).expanduser().resolve()
                if not path.is_file():
                    raise NativeDistributedError(
                        f'TiKV {field} file is unavailable')
                result[field] = str(path)
        return result

    def _validated_helper_path(self):
        path = Path(self.helper_path).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise NativeDistributedError(
                'the trusted CDEadmin TiKV helper is unavailable')
        return str(path)

    @staticmethod
    def _http_context(route):
        if route['pd_http_scheme'] == 'http':
            return None
        context = ssl.create_default_context(cafile=route.get('tls_ca'))
        if route.get('tls_certificate'):
            context.load_cert_chain(
                route['tls_certificate'], route['tls_key'])
        return context

    def _pd_document(self, route, path, method='GET', body=None):
        endpoint = route['pd_endpoints'][0]
        url = f'{route["pd_http_scheme"]}://{endpoint}{path}'
        data = None
        headers = {'Accept': 'application/json'}
        if body is not None:
            data = json.dumps(
                body, separators=(',', ':'), sort_keys=True
            ).encode('utf-8')
            if len(data) > self.MAX_HTTP_BYTES:
                raise NativeDistributedError(
                    'TiKV PD request exceeds size limit')
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with self.opener(
                request, timeout=10, context=self._http_context(route)
            ) as response:
                payload = response.read(self.MAX_HTTP_BYTES + 1)
        except Exception as exc:
            raise NativeDistributedError(
                'TiKV PD metadata request failed') from exc
        if len(payload) > self.MAX_HTTP_BYTES:
            raise NativeDistributedError(
                'TiKV PD metadata response exceeds size limit')
        if not payload:
            return {}
        try:
            value = json.loads(payload)
        except (TypeError, ValueError):
            value = {'provider_message': payload.decode(
                'utf-8', errors='replace')[:4096]
            }
        if not isinstance(value, (dict, list)):
            if method != 'GET':
                value = {'provider_value': value}
            else:
                raise NativeDistributedError('TiKV PD response is invalid')
        return value

    def _pd(self, route, path):
        value = self._pd_document(route, path)
        if not isinstance(value, dict):
            raise NativeDistributedError(
                'TiKV PD metadata response is invalid')
        return value

    def _pd_mutate(self, route, method, path, body=None):
        if method not in {'POST', 'DELETE', 'PATCH', 'PUT'}:
            raise NativeDistributedError(
                'TiKV PD mutation method is not admitted')
        return {
            'request_accepted_by_pd_http': True,
            'provider_response': self._pd_document(
                route, path, method=method, body=body
            ),
            'provider_finality_only': True,
            'automatic_mutation_retry_by_cdeadmin': False,
        }

    def runtime_identity(self, request, _handle=None):
        route = self._route(request)
        stores = self._pd(route, '/pd/api/v1/stores').get('stores', [])
        versions = {
            str(row.get('store', {}).get('version', '')).lstrip('v')
            for row in stores if isinstance(row, dict)
        }
        versions.discard('')
        if len(versions) != 1:
            raise NativeDistributedError('TiKV runtime version is ambiguous')
        version = versions.pop()
        return {
            'engine_id': 'tikv', 'version': version,
            'build_id': f'tikv:{version}', 'protocol_id': 'tikv_grpc',
            'client_revision': self.CLIENT_REVISION,
        }

    def open_session(self, request):
        self._validated_helper_path()
        return self._route(request)

    def list_resources(self, request):
        route = self._route(request)
        generation = str(request.get('capability_generation') or 'current')
        values = [resource('cluster', [], 'TiKV', generation)]
        if route['api_version'] == 2:
            keyspaces = self._pd(
                route, '/pd/api/v2/keyspaces?limit=10000'
            ).get('keyspaces', [])
            for row in keyspaces[:2000]:
                if isinstance(row, dict) and row.get('name'):
                    values.append(resource(
                        'keyspace', [], row['name'], generation, row
                    ))
        values.append(resource(
            'key-range', [], 'bounded-key-browser', generation, {
                'maximum_page_size': self.MAX_RECORDS,
            },
        ))
        values.append(resource(
            'ttl', [], 'key-expiration-browser', generation, {
                'ttl_enabled': route['enable_ttl'],
                'api_version': route['api_version'],
            },
        ))
        for row in self._pd(
                route, '/pd/api/v1/stores').get('stores', [])[:1000]:
            if not isinstance(row, dict):
                continue
            store = row.get('store', {})
            if isinstance(store, dict) and store.get('id') is not None:
                values.append(resource(
                    'store', [], store['id'], generation, store))
        regions = self._pd(route, '/pd/api/v1/regions').get('regions', [])
        for row in regions[:2000]:
            if len(values) >= self.MAX_RECORDS - 256:
                break
            if isinstance(row, dict) and row.get('id') is not None:
                values.append(resource(
                    'region', [], row['id'], generation, row
                ))
                for peer in row.get('peers', []):
                    if len(values) >= self.MAX_RECORDS - 256:
                        break
                    if isinstance(peer, dict) and peer.get('id') is not None:
                        values.append(resource(
                            'peer', [row['id']], peer['id'], generation,
                            peer,
                        ))
        schedulers = self._pd_document(
            route, '/pd/api/v1/schedulers'
        )
        if isinstance(schedulers, dict):
            schedulers = schedulers.get('schedulers', [])
        for name in schedulers if isinstance(schedulers, list) else []:
            if len(values) >= self.MAX_RECORDS - 2:
                break
            if isinstance(name, str):
                values.append(resource(
                    'scheduler', [], name, generation
                ))
        rules = self._pd_document(route, '/pd/api/v1/config/rules')
        if isinstance(rules, dict):
            rules = rules.get('rules', [])
        for row in rules if isinstance(rules, list) else []:
            if len(values) >= self.MAX_RECORDS - 1:
                break
            if not isinstance(row, dict) or not row.get('group_id') or not (
                    row.get('id')):
                continue
            values.append(resource(
                'placement-rule', [row['group_id']], row['id'], generation,
                row,
            ))
        values.append(resource(
            'configuration', [], 'storage-api', generation, {
                'api_version': route['api_version'],
                'ttl_enabled': route['enable_ttl'],
                'transaction_mode': route['transaction_mode'],
            },
        ))
        return values

    @staticmethod
    def describe_transaction(handle):
        return {
            'native_state': 'tikv-client-go-helper-session',
            'transaction_model': 'tikv-provider-native',
            'automatic_mutation_retry_by_cdeadmin': False,
            'configured_transaction_mode': handle['transaction_mode'],
            'ttl_enabled': handle['enable_ttl'],
            'operation_timeout_seconds': handle['operation_timeout'],
            'isolation': 'snapshot-isolation',
        }

    @staticmethod
    def _encoded(command, name, required=False):
        encoded_name = f'{name}_base64'
        if encoded_name in command:
            value = command[encoded_name]
            if not isinstance(value, str):
                raise NativeDistributedError(
                    f'TiKV {encoded_name} must be text')
            try:
                base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise NativeDistributedError(
                    f'TiKV {encoded_name} is invalid') from exc
            return value
        if name not in command:
            if required:
                raise NativeDistributedError(f'TiKV {name} is required')
            return ''
        value = command[name]
        if not isinstance(value, str):
            raise NativeDistributedError(f'TiKV {name} must be text')
        return base64.b64encode(value.encode('utf-8')).decode('ascii')

    @classmethod
    def _helper_request(cls, route, command):
        if not isinstance(command, dict):
            raise NativeDistributedError('TiKV command must be JSON')
        operation = command.get('operation')
        if operation not in {
                'get', 'put', 'put_with_ttl', 'get_key_ttl', 'delete',
                'compare_and_swap', 'scan', 'transaction'}:
            raise NativeDistributedError('TiKV operation is unsupported')
        value = {
            'operation': operation,
            'pd_endpoints': list(route['pd_endpoints']),
            'api_version': route['api_version'],
            'enable_ttl': route['enable_ttl'],
            'operation_timeout_seconds': route['operation_timeout'],
            'transaction_mode': route['transaction_mode'],
        }
        for field in ('tls_ca', 'tls_certificate', 'tls_key'):
            if route.get(field):
                value[field] = route[field]
        if operation in {
                'get', 'put', 'put_with_ttl', 'get_key_ttl', 'delete',
                'compare_and_swap'}:
            value['key_base64'] = cls._encoded(command, 'key', True)
        if operation in {'put', 'put_with_ttl', 'compare_and_swap'}:
            value['value_base64'] = cls._encoded(command, 'value', True)
        if operation == 'put_with_ttl':
            ttl = command.get('ttl_seconds')
            if isinstance(ttl, bool) or not isinstance(ttl, int) or not (
                    1 <= ttl <= 315360000):
                raise NativeDistributedError(
                    'TiKV TTL must be between 1 and 315360000 seconds')
            value['ttl_seconds'] = ttl
        if operation == 'compare_and_swap' and (
                'previous_value' in command or
                'previous_value_base64' in command):
            value['previous_value_base64'] = cls._encoded(
                command, 'previous_value')
        if operation == 'scan':
            value['start_key_base64'] = cls._encoded(command, 'start_key')
            value['end_key_base64'] = cls._encoded(command, 'end_key')
            limit = command.get('limit', 100)
            if isinstance(limit, bool) or not isinstance(limit, int) or not (
                    1 <= limit <= cls.MAX_RECORDS):
                raise NativeDistributedError(
                    'TiKV scan limit is outside approved bounds')
            value['limit'] = limit
            include_ttl = command.get('include_ttl', False)
            if not isinstance(include_ttl, bool):
                raise NativeDistributedError(
                    'TiKV include_ttl must be boolean')
            value['include_ttl'] = include_ttl
        if operation == 'transaction':
            if route['api_version'] == 1 and route['enable_ttl']:
                raise NativeDistributedError(
                    'TiKV TxnKV requires API v2 when RawKV TTL is enabled')
            keys = command.get('keys', [])
            mutations = command.get('mutations', [])
            if not isinstance(keys, list) or not isinstance(mutations, list):
                raise NativeDistributedError(
                    'TiKV transaction keys and mutations must be lists')
            if len(keys) > cls.MAX_RECORDS or len(mutations) > cls.MAX_RECORDS:
                raise NativeDistributedError(
                    'TiKV transaction exceeds record limit')
            value['keys_base64'] = [
                cls._encoded({'key': item}, 'key', True) for item in keys
            ]
            value['mutations'] = []
            for mutation in mutations:
                if not isinstance(mutation, dict) or mutation.get(
                        'operation') not in {'set', 'delete'}:
                    raise NativeDistributedError(
                        'TiKV transaction mutation is unsupported')
                item = {
                    'operation': mutation['operation'],
                    'key_base64': cls._encoded(mutation, 'key', True),
                }
                if mutation['operation'] == 'set':
                    item['value_base64'] = cls._encoded(
                        mutation, 'value', True)
                value['mutations'].append(item)
        return value

    @staticmethod
    def _display_value(value):
        if value is None:
            return None
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise NativeDistributedError(
                'TiKV helper returned invalid base64') from exc
        try:
            return decoded.decode('utf-8')
        except UnicodeDecodeError:
            return None

    @classmethod
    def _records(cls, values):
        if not isinstance(values, list) or len(values) > cls.MAX_RECORDS:
            raise NativeDistributedError(
                'TiKV helper result exceeds record limit')
        result = []
        for value in values:
            if not isinstance(value, dict):
                raise NativeDistributedError(
                    'TiKV helper result is invalid')
            item = copy.deepcopy(value)
            if 'key_base64' in item:
                item['key'] = cls._display_value(item['key_base64'])
            if item.get('found') is False:
                item['value'] = None
            elif 'value_base64' in item:
                item['value'] = cls._display_value(item['value_base64'])
            if 'previous_value_base64' in item:
                item['previous_value'] = cls._display_value(
                    item['previous_value_base64'])
            result.append(item)
        return result

    def _run_helper(self, route, command):
        request = self._helper_request(route, command)
        payload = json.dumps(
            request, separators=(',', ':'), sort_keys=True).encode('utf-8')
        try:
            completed = self.runner(
                [self._validated_helper_path()], input=payload,
                capture_output=True, check=False,
                timeout=route['operation_timeout'] + 5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise NativeDistributedError(
                'TiKV helper execution failed') from exc
        output = completed.stdout or b''
        if isinstance(output, str):
            output = output.encode('utf-8')
        if len(output) > self.MAX_HELPER_BYTES:
            raise NativeDistributedError(
                'TiKV helper response exceeds size limit')
        try:
            response = json.loads(output)
        except (TypeError, ValueError) as exc:
            raise NativeDistributedError(
                'TiKV helper response is invalid') from exc
        if not isinstance(response, dict):
            raise NativeDistributedError('TiKV helper response is invalid')
        if completed.returncode != 0 or response.get('error'):
            raise NativeDistributedError(
                str(response.get('error') or 'TiKV helper request failed'))
        if response.get('provider_finality_only') is not True:
            raise NativeDistributedError(
                'TiKV helper finality boundary is invalid')
        native = response.get('native')
        if not isinstance(native, dict):
            raise NativeDistributedError(
                'TiKV helper native observation is invalid')
        native = copy.deepcopy(native)
        native.update({
            'client_revision': self.CLIENT_REVISION,
            'provider_finality_only': True,
            'automatic_mutation_retry_by_cdeadmin': False,
        })
        return self._records(response.get('records')), native

    def execute(self, handle, command, _parameters):
        rows, native = self._run_helper(handle, command)
        return NativeResult(
            'key_value', 'entries', rows,
            {'fields': [
                'key', 'value', 'found', 'accepted', 'previous_value',
                'swapped', 'ttl_seconds_remaining', 'key_base64',
                'value_base64',
            ]}, native,
        )

    @staticmethod
    def cancel(_token):
        return False

    def describe_security(self, request):
        route = self._route(request)
        generation = str(
            request.get('capability_generation') or 'current')
        return resource(
            'configuration', [], 'transport-security', generation,
            {
                'authorization_model': 'tikv-mutual-tls',
                'transport_encrypted': bool(route.get('tls_ca')),
            },
        )

    def visual_admin_catalog(self, catalog):
        value = key_value_catalog(
            catalog, {'raw-key', 'key-range'}, 'tikv'
        )
        value = CONTROL_CATALOG.apply(value)
        value['transaction_authority'] = 'tikv-client-go-native'
        value['experience_families'] = ['key_value']
        value['native_outcomes_are_opaque'] = True
        value['automatic_mutation_retry'] = False
        available_operations = {
            kind: sorted(operations)
            for kind, operations in ADMIN_OPERATIONS.items()
        }

        def declaration(*resource_kinds, reason, operation_obligations=None):
            return {
                'status': 'supported',
                'resource_kinds': list(resource_kinds),
                'operation_obligations': copy.deepcopy(
                    operation_obligations or {
                        kind: available_operations.get(kind, [])
                        for kind in resource_kinds
                    }
                ),
                'reason': reason,
                'evidence': ['tikv-8.5.6-native-client-go-and-pd-api'],
            }

        value['concept_declarations'] = {'key_value': {
            'key_browsing': declaration(
                'key-range', 'raw-key',
                reason=(
                    'Bounded native RawKV scans expose ordered byte keys and '
                    'provider-issued row identities.'
                ),
                operation_obligations={'key-range': ['inspect']},
            ),
            'data_type_editing': declaration(
                'key-range', 'raw-key',
                reason=(
                    'RawKV byte keys and values use explicit UTF-8 or base64 '
                    'forms without inventing Redis data types.'
                ),
                operation_obligations={
                    'key-range': ['insert', 'update', 'delete'],
                },
            ),
            'ttl_inspection': declaration(
                'ttl',
                reason=(
                    'Native RawKV GetKeyTTL is available when storage TTL is '
                    'enabled for API v1 or intrinsically by API v2.'
                ),
                operation_obligations={'ttl': ['inspect']},
            ),
            'expiration_management': declaration(
                'ttl',
                reason=(
                    'Native PutWithTTL sets expiration; removing expiration '
                    'performs the provider-defined value rewrite with TTL '
                    'zero.'
                ),
                operation_obligations={
                    'ttl': ['create', 'alter', 'drop'],
                },
            ),
            'streams': 'not_applicable',
            'pubsub': 'not_applicable',
            'consumer_groups': 'not_applicable',
            'modules': 'not_applicable',
            'acls': 'not_applicable',
            'replication': declaration(
                'region', 'peer', 'placement-rule',
                reason=(
                    'PD Regions, peers and placement rules are TiKV native '
                    'replication and replica-placement controls.'
                ),
            ),
            'sentinel_or_cluster_state': declaration(
                'cluster', 'store', 'region', 'peer', 'scheduler',
                'configuration',
                reason=(
                    'PD cluster, store, Region, peer, scheduler and storage '
                    'configuration surfaces replace Redis Sentinel semantics.'
                ),
            ),
        }}
        ttl_forms = {
            'inspect': (
                'Inspect key expiration', [
                    {'field_id': 'key', 'label': 'Key', 'control': 'text',
                     'required': True},
                    {'field_id': 'key_encoding', 'label': 'Key encoding',
                     'control': 'select', 'required': False,
                     'default': 'utf8', 'options': [
                         {'value': 'utf8', 'label': 'UTF-8'},
                         {'value': 'base64', 'label': 'Base64'},
                     ]},
                ],
            ),
            'create': ('Set key expiration', None),
            'alter': ('Replace key expiration', None),
            'drop': ('Remove key expiration', None),
        }
        replacement_fields = [
            {'field_id': 'key', 'label': 'Key', 'control': 'text',
             'required': True},
            {'field_id': 'key_encoding', 'label': 'Key encoding',
             'control': 'select', 'required': False, 'default': 'utf8',
             'options': [
                 {'value': 'utf8', 'label': 'UTF-8'},
                 {'value': 'base64', 'label': 'Base64'},
             ]},
            {'field_id': 'value', 'label': 'Replacement value',
             'control': 'text', 'required': True},
            {'field_id': 'value_encoding', 'label': 'Value encoding',
             'control': 'select', 'required': False, 'default': 'utf8',
             'options': [
                 {'value': 'utf8', 'label': 'UTF-8'},
                 {'value': 'base64', 'label': 'Base64'},
             ]},
        ]
        for resource in value.get('objects', []):
            if resource.get('resource_kind') != 'ttl':
                continue
            for operation in resource.get('operations', []):
                operation_id = operation.get('operation_id')
                if operation_id not in ttl_forms:
                    continue
                title, fields = ttl_forms[operation_id]
                if fields is None:
                    fields = copy.deepcopy(replacement_fields)
                    if operation_id != 'drop':
                        fields.append({
                            'field_id': 'ttl_seconds',
                            'label': 'TTL (seconds)', 'control': 'number',
                            'required': True, 'minimum': 1,
                            'maximum': 315360000,
                        })
                operation['form'] = {
                    'form_id': f'tikv-ttl-{operation_id}',
                    'title': title,
                    'fields': fields,
                }
        return value

    @staticmethod
    def validate_admin_operation(request):
        if CONTROL_CATALOG.supports(
                request.get('resource_kind'), request.get('operation_id')):
            checked = CONTROL_CATALOG.validate(request)
            if checked['errors']:
                return checked
            try:
                TiKVBackend._control_request(request)
            except NativeDistributedError as exc:
                return {'errors': [{
                    'field_id': None,
                    'code': 'invalid_control_plane_request',
                    'message': str(exc),
                }]}
            return {'errors': []}
        if request.get('resource_kind') == 'ttl':
            operation = request.get('operation_id')
            if operation not in {'inspect', 'create', 'alter', 'drop'}:
                return {'errors': []}
            try:
                route = TiKVBackend._route(request)
                if not route['enable_ttl']:
                    raise NativeDistributedError(
                        'TiKV TTL is disabled for this API v1 route')
                draft = request.get('draft') or {}
                decode_value(draft, 'key')
                if operation != 'inspect':
                    decode_value(draft, 'value')
                ttl = draft.get('ttl_seconds')
                if operation in {'create', 'alter'} and (
                    isinstance(ttl, bool) or not isinstance(ttl, int) or
                    not 1 <= ttl <= 315360000
                ):
                    raise NativeDistributedError(
                        'TiKV TTL must be between 1 and 315360000 seconds')
            except NativeDistributedError as exc:
                return {'errors': [{
                    'field_id': None, 'code': 'invalid_ttl_request',
                    'message': str(exc),
                }]}
            return {'errors': []}
        return validate_key_value_request(
            request, {'raw-key', 'key-range'}
        )

    @staticmethod
    def plan_admin_operation(request):
        if CONTROL_CATALOG.supports(
                request.get('resource_kind'), request.get('operation_id')):
            compiled = TiKVBackend._control_request(request)
            return {
                'command_preview': {
                    'operation': request['operation_id'],
                    'resource_kind': request['resource_kind'],
                    'requests': [{
                        'method': item['method'], 'path': item['path'],
                        'provider_constructed': True,
                    } for item in compiled['requests']],
                    'provider_constructed': True,
                },
                'provider_payload': copy.deepcopy(dict(request)),
                'warnings': copy.deepcopy(compiled.get('warnings', [])),
                'impact': copy.deepcopy(compiled['impact']),
                'receipt': {
                    'planner': 'tikv-pd-control-plane.v1',
                    'provider_finality_authority': True,
                    'automatic_mutation_retry': False,
                },
            }
        if request.get('resource_kind') == 'ttl':
            operation = request['operation_id']
            return {
                'command_preview': {
                    'operation': operation,
                    'resource_kind': 'ttl',
                    'native_operation': (
                        'get_key_ttl' if operation == 'inspect' else
                        'put_with_ttl' if operation in {'create', 'alter'}
                        else 'put'
                    ),
                    'provider_constructed': True,
                },
                'provider_payload': copy.deepcopy(dict(request)),
                'warnings': ([] if operation in {'inspect', 'create'} else [{
                    'code': 'value_rewrite_required',
                    'message': (
                        'TiKV changes expiration by replacing the supplied '
                        'value; the native RawKV API has no TTL-only mutation.'
                    ),
                }]),
                'receipt': {
                    'planner': 'tikv-native-ttl.v1',
                    'provider_finality_authority': True,
                    'automatic_mutation_retry': False,
                },
            }
        return {
            'command_preview': {
                'operation': request['operation_id'],
                'resource_kind': request['resource_kind'],
                'provider_constructed': True,
            },
            'provider_payload': copy.deepcopy(dict(request)),
            'warnings': [],
            'receipt': {'planner': 'tikv-native'},
        }

    def apply_admin_operation(self, request):
        payload = request['provider_payload']
        operation = payload['operation_id']
        if CONTROL_CATALOG.supports(
                payload.get('resource_kind'), operation):
            route = self._route(payload)
            compiled = self._control_request(payload)
            pre_dispatch_observation = None
            if payload.get('resource_kind') == 'region':
                regions = self._pd(
                    route, '/pd/api/v1/regions'
                ).get('regions', [])
                records = self._pd_document(
                    route, '/pd/api/v1/operators/records'
                )
                if not isinstance(records, list):
                    records = []
                target_region_id = self._target_value(
                    payload, numeric=True
                )
                pre_dispatch_observation = {
                    'region_ids': [
                        item.get('id') for item in regions
                        if isinstance(item, dict) and
                        item.get('id') is not None
                    ][:self.MAX_RECORDS],
                    'target_region': next((
                        copy.deepcopy(item) for item in regions
                        if isinstance(item, dict) and
                        item.get('id') == target_region_id
                    ), None),
                    'operator_records': [
                        item[:4096] for item in records[-512:]
                        if isinstance(item, str)
                    ],
                }
            results = []
            for item in compiled['requests']:
                results.append(self._pd_mutate(
                    route, item['method'], item['path'], item.get('body')
                ))
            result = {
                'accepted': True,
                'provider_responses': results,
                'impact': copy.deepcopy(compiled['impact']),
                'provider_finality_only': True,
                'automatic_mutation_retry_by_cdeadmin': False,
            }
            if pre_dispatch_observation is not None:
                result['pre_dispatch_observation'] = (
                    pre_dispatch_observation
                )
            return result
        if payload.get('resource_kind') == 'ttl':
            route = self._route(payload)
            if not route['enable_ttl']:
                raise NativeDistributedError(
                    'TiKV TTL is disabled for this API v1 route')
            draft = payload.get('draft') or {}
            command = {
                'operation': (
                    'get_key_ttl' if operation == 'inspect' else
                    'put_with_ttl' if operation in {'create', 'alter'}
                    else 'put'
                ),
                'key_base64': base64.b64encode(
                    decode_value(draft, 'key')
                ).decode('ascii'),
            }
            if operation != 'inspect':
                command['value_base64'] = base64.b64encode(
                    decode_value(draft, 'value')
                ).decode('ascii')
            if operation in {'create', 'alter'}:
                command['ttl_seconds'] = draft['ttl_seconds']
            rows, native = self._run_helper(route, command)
            native['expiration_change_semantics'] = (
                'read_only' if operation == 'inspect' else
                'value_rewrite_with_ttl' if operation in {'create', 'alter'}
                else 'value_rewrite_with_ttl_zero'
            )
            return {
                'accepted': True, 'records': rows, 'native': native,
                'native_operation': operation,
                'provider_finality_only': True,
                'automatic_mutation_retry_by_cdeadmin': False,
            }
        if operation == 'inspect':
            target = payload.get('target_resource') or {}
            resource_id = target.get('resource_id')
            matching = [
                item for item in self.list_resources(payload)
                if item.get('resource_id') == resource_id
            ]
            if len(matching) != 1:
                raise NativeDistributedError(
                    'TiKV native resource observation is unavailable')
            return {
                'accepted': True, 'resource': matching[0],
                'provider_observation_only': True,
                'provider_finality_only': True,
                'automatic_mutation_retry_by_cdeadmin': False,
            }
        if payload['resource_kind'] not in {'raw-key', 'key-range'} or (
                operation not in {
                    'insert', 'update', 'delete'
                }
        ):
            raise NativeDistributedError(
                'TiKV admin operation is unsupported')
        draft = payload.get('draft') or {}
        route = self._route(payload)
        target = payload.get('target_resource') or {
            'resource_kind': payload['resource_kind'],
            'display_path': ['bounded-key-browser'],
        }
        if operation == 'insert':
            command = {
                'operation': 'compare_and_swap',
                'key_base64': base64.b64encode(
                    decode_value(draft, 'key')
                ).decode('ascii'),
                'value_base64': base64.b64encode(
                    decode_value(draft, 'value')
                ).decode('ascii'),
            }
        elif operation == 'update':
            key, original = self._row_identities.consume(
                route, target, draft.get('selector')
            )
            command = {
                'operation': (
                    'compare_and_swap'
                ),
                'key_base64': base64.b64encode(key).decode('ascii'),
                'previous_value_base64': base64.b64encode(
                    original
                ).decode('ascii'),
            }
            command['value_base64'] = base64.b64encode(
                decode_value(draft, 'value')
            ).decode('ascii')
        else:
            key, _original = self._row_identities.consume(
                route, target, draft.get('selector')
            )
            command = {
                'operation': 'delete',
                'key_base64': base64.b64encode(key).decode('ascii'),
            }
        rows, native = self._run_helper(route, command)
        if operation in {'insert', 'update'} and (
                not rows or rows[0].get('swapped') is not True):
            raise NativeDistributedError(
                'TiKV key changed after it was displayed'
                if operation != 'insert' else 'TiKV key already exists'
            )
        if operation == 'delete':
            native['provider_native_single_delete'] = True
            native['conditional_delete_claimed'] = False
        return {
            'accepted': True, 'records': rows,
            'native': native, 'native_operation': operation,
            'provider_finality_only': True,
            'automatic_mutation_retry_by_cdeadmin': False,
        }

    @staticmethod
    def _target_value(request, numeric=False, position=-1):
        target = request.get('target_resource')
        if not isinstance(target, dict):
            raise NativeDistributedError('TiKV control target is required')
        path = target.get('display_path') or [target.get('display_name')]
        try:
            value = path[position]
        except (IndexError, TypeError):
            raise NativeDistributedError(
                'TiKV control target path is invalid') from None
        if numeric:
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise NativeDistributedError(
                    'TiKV control target requires a numeric ID') from None
            if value < 1:
                raise NativeDistributedError(
                    'TiKV control target ID must be positive')
        elif not isinstance(value, str) or not value:
            raise NativeDistributedError(
                'TiKV control target name is invalid')
        return value

    @staticmethod
    def _rule_identity(request):
        draft = request.get('draft') or {}
        if request.get('operation_id') == 'create':
            group_id = draft.get('group_id')
            rule_id = draft.get('rule_id')
        else:
            group_id = TiKVBackend._target_value(request, position=-2)
            rule_id = TiKVBackend._target_value(request)
        pattern = re.compile(r'^[A-Za-z0-9_.:-]{1,256}$')
        if not isinstance(group_id, str) or not pattern.fullmatch(group_id):
            raise NativeDistributedError('TiKV rule group is invalid')
        if not isinstance(rule_id, str) or not pattern.fullmatch(rule_id):
            raise NativeDistributedError('TiKV rule ID is invalid')
        return group_id, rule_id

    @staticmethod
    def _rule_body(request):
        draft = request.get('draft') or {}
        group_id, rule_id = TiKVBackend._rule_identity(request)
        role = draft.get('role')
        if role not in {'voter', 'leader', 'follower', 'learner'}:
            raise NativeDistributedError('TiKV placement role is invalid')
        count = draft.get('count')
        if isinstance(count, bool) or not isinstance(count, int) or not (
                1 <= count <= 15):
            raise NativeDistributedError(
                'TiKV placement replica count is invalid')
        body = {
            'group_id': group_id, 'id': rule_id, 'role': role,
            'count': count,
            'start_key': draft.get('start_key', ''),
            'end_key': draft.get('end_key', ''),
            'location_labels': copy.deepcopy(
                draft.get('location_labels') or []
            ),
            'label_constraints': copy.deepcopy(
                draft.get('constraints') or []
            ),
            'override': bool(draft.get('override', False)),
        }
        for name in ('start_key', 'end_key'):
            value = body[name]
            if not isinstance(value, str) or len(value) % 2 or len(
                    value) > 8192 or any(
                        character not in '0123456789abcdefABCDEF'
                        for character in value):
                raise NativeDistributedError(
                    f'TiKV {name} must be even-length hexadecimal')
        labels = body['location_labels']
        if not isinstance(labels, list) or len(labels) > 64 or not all(
                isinstance(value, str) and 0 < len(value) <= 256
                for value in labels):
            raise NativeDistributedError(
                'TiKV location labels are invalid')
        constraints = body['label_constraints']
        if not isinstance(constraints, list) or len(constraints) > 64:
            raise NativeDistributedError(
                'TiKV label constraints are invalid')
        for constraint in constraints:
            if not isinstance(constraint, dict) or set(
                    constraint).difference({'key', 'op', 'values'}):
                raise NativeDistributedError(
                    'TiKV label constraint is invalid')
            values = constraint.get('values', [])
            if constraint.get('op') not in {
                    'in', 'notIn', 'exists', 'notExists'} or not isinstance(
                        constraint.get('key'), str) or not isinstance(
                            values, list) or len(values) > 64 or not all(
                                isinstance(value, str) for value in values):
                raise NativeDistributedError(
                    'TiKV label constraint is invalid')
        isolation = draft.get('isolation_level')
        if isolation:
            if not isinstance(isolation, str) or len(isolation) > 256:
                raise NativeDistributedError(
                    'TiKV isolation label is invalid')
            body['isolation_level'] = isolation
        index = draft.get('rule_index')
        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int) or (
                    index < 0):
                raise NativeDistributedError('TiKV rule index is invalid')
            body['index'] = index
        return body

    @staticmethod
    def _operator_request(name, region_id, draft):
        body = {'name': name, 'region_id': region_id}
        if name == 'merge-region':
            body['source_region_id'] = body.pop('region_id')
        fields = {
            'transfer-leader': ('to_store_id',),
            'add-peer': ('store_id',),
            'add-learner': ('store_id',),
            'remove-peer': ('store_id',),
            'transfer-peer': ('from_store_id', 'to_store_id'),
            'split-region': (),
            'scatter-region': (),
            'merge-region': ('target_region_id',),
        }[name]
        for field in fields:
            value = draft.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or (
                    value < 1):
                raise NativeDistributedError(
                    f'TiKV {field} must be a positive integer')
            body[field] = value
        if name == 'split-region':
            policy = draft.get('policy')
            if policy not in {'approximate', 'scan', 'usekey'}:
                raise NativeDistributedError(
                    'TiKV split policy is invalid')
            body['policy'] = policy
            keys = draft.get('keys') or []
            if not isinstance(keys, list) or len(keys) > 1024:
                raise NativeDistributedError(
                    'TiKV split keys are invalid')
            for key in keys:
                if not isinstance(key, str) or len(key) % 2 or len(
                        key) > 8192 or any(
                            character not in '0123456789abcdefABCDEF'
                            for character in key):
                    raise NativeDistributedError(
                        'TiKV split keys must be even-length hexadecimal')
            if policy == 'usekey' and not keys:
                raise NativeDistributedError(
                    'TiKV explicit-key split requires at least one key')
            if policy != 'usekey' and keys:
                raise NativeDistributedError(
                    'TiKV split keys require the explicit-key policy')
            if keys:
                body['keys'] = list(keys)
        elif name == 'scatter-region':
            group = draft.get('group')
            if group:
                if not isinstance(group, str) or len(group) > 256:
                    raise NativeDistributedError(
                        'TiKV scatter group is invalid')
                body['group'] = group
        return body

    @staticmethod
    def _store_labels(draft):
        labels = draft.get('labels')
        if not isinstance(labels, dict) or not labels or len(labels) > 64:
            raise NativeDistributedError('TiKV store labels are invalid')
        if not all(
            isinstance(key, str) and 0 < len(key) <= 256 and
            isinstance(value, str) and len(value) <= 256
            for key, value in labels.items()
        ):
            raise NativeDistributedError('TiKV store labels are invalid')
        return copy.deepcopy(labels)

    @staticmethod
    def _keyspace_name(request, creating=False):
        if creating:
            name = (request.get('draft') or {}).get('name')
        else:
            name = TiKVBackend._target_value(request)
        if not isinstance(name, str) or not re.fullmatch(
                r'[A-Za-z0-9_.-]{1,256}', name):
            raise NativeDistributedError('TiKV keyspace name is invalid')
        return name

    @staticmethod
    def _keyspace_config(request, required=False):
        config = (request.get('draft') or {}).get('config')
        if config is None and not required:
            return {}
        if not isinstance(config, dict) or len(config) > 256 or (
                required and not config):
            raise NativeDistributedError(
                'TiKV keyspace configuration is invalid')
        for key, value in config.items():
            if not isinstance(key, str) or not 0 < len(key) <= 256 or (
                    value is not None and (
                        not isinstance(value, str) or len(value) > 4096
                    )):
                raise NativeDistributedError(
                    'TiKV keyspace configuration is invalid')
        return copy.deepcopy(config)

    @staticmethod
    def _require_api_v2(request):
        route = TiKVBackend._route(request)
        if route['api_version'] != 2:
            raise NativeDistributedError(
                'TiKV keyspace administration requires API version 2')
        return route

    @staticmethod
    def _control_request(request):
        kind = request.get('resource_kind')
        operation = request.get('operation_id')
        draft = request.get('draft') or {}
        values = []
        if kind == 'cluster' and operation == 'set_all_store_limits':
            rate = draft.get('rate')
            limit_type = draft.get('limit_type')
            if isinstance(rate, bool) or not isinstance(
                    rate, (int, float)) or rate <= 0:
                raise NativeDistributedError(
                    'TiKV store limit rate is invalid')
            if limit_type not in {'add-peer', 'remove-peer', 'both'}:
                raise NativeDistributedError(
                    'TiKV store limit type is invalid')
            body = {'rate': rate}
            if limit_type != 'both':
                body['type'] = limit_type
            labels = draft.get('labels') or {}
            if labels:
                body['labels'] = TiKVBackend._store_labels({'labels': labels})
            values.append({
                'method': 'POST', 'path': '/pd/api/v1/stores/limit',
                'body': body,
            })
        elif kind == 'cluster' and operation == 'remove_tombstones':
            values.append({
                'method': 'DELETE',
                'path': '/pd/api/v1/stores/remove-tombstone',
            })
        elif kind == 'keyspace':
            TiKVBackend._require_api_v2(request)
            creating = operation == 'create'
            name = TiKVBackend._keyspace_name(request, creating)
            quoted_name = urllib.parse.quote(name, safe='')
            if creating:
                values.append({
                    'method': 'POST', 'path': '/pd/api/v2/keyspaces',
                    'body': {
                        'name': name,
                        'config': TiKVBackend._keyspace_config(request),
                    },
                })
            elif operation == 'update_config':
                values.append({
                    'method': 'PATCH',
                    'path': (
                        f'/pd/api/v2/keyspaces/{quoted_name}/config'
                    ),
                    'body': {'config': TiKVBackend._keyspace_config(
                        request, required=True
                    )},
                })
            elif operation in {'enable', 'disable', 'archive', 'tombstone'}:
                state = {
                    'enable': 'ENABLED', 'disable': 'DISABLED',
                    'archive': 'ARCHIVED', 'tombstone': 'TOMBSTONE',
                }[operation]
                values.append({
                    'method': 'PUT',
                    'path': f'/pd/api/v2/keyspaces/{quoted_name}/state',
                    'body': {'state': state},
                })
        elif kind == 'store' and operation == 'evict_leaders':
            store_id = TiKVBackend._target_value(request, numeric=True)
            values.append({
                'method': 'POST', 'path': '/pd/api/v1/schedulers',
                'body': {
                    'name': 'evict-leader-scheduler',
                    'store_id': store_id,
                },
            })
        elif kind == 'store':
            store_id = TiKVBackend._target_value(request, numeric=True)
            quoted_store = urllib.parse.quote(str(store_id), safe='')
            if operation in {'mark_offline', 'bring_up'}:
                state = 'Offline' if operation == 'mark_offline' else 'Up'
                values.append({
                    'method': 'POST',
                    'path': (
                        f'/pd/api/v1/store/{quoted_store}/state?' +
                        urllib.parse.urlencode({'state': state})
                    ),
                })
            elif operation == 'set_labels':
                suffix = '?force=true' if draft.get('force') is True else ''
                values.append({
                    'method': 'POST',
                    'path': (
                        f'/pd/api/v1/store/{quoted_store}/label{suffix}'
                    ),
                    'body': TiKVBackend._store_labels(draft),
                })
            elif operation == 'delete_label':
                label_key = draft.get('label_key')
                if not isinstance(label_key, str) or not re.fullmatch(
                        r'[A-Za-z0-9_.:/-]{1,256}', label_key):
                    raise NativeDistributedError(
                        'TiKV store label key is invalid')
                values.append({
                    'method': 'DELETE',
                    'path': f'/pd/api/v1/store/{quoted_store}/label',
                    'body': label_key,
                })
            elif operation == 'set_weights':
                leader = draft.get('leader_weight')
                region = draft.get('region_weight')
                if any(
                    isinstance(value, bool) or
                    not isinstance(value, (int, float)) or value < 0
                    for value in (leader, region)
                ):
                    raise NativeDistributedError(
                        'TiKV store weights are invalid')
                values.append({
                    'method': 'POST',
                    'path': f'/pd/api/v1/store/{quoted_store}/weight',
                    'body': {'leader': leader, 'region': region},
                })
            elif operation == 'set_limit':
                rate = draft.get('rate')
                limit_type = draft.get('limit_type')
                if isinstance(rate, bool) or not isinstance(
                        rate, (int, float)) or rate <= 0:
                    raise NativeDistributedError(
                        'TiKV store limit rate is invalid')
                if limit_type not in {'add-peer', 'remove-peer', 'both'}:
                    raise NativeDistributedError(
                        'TiKV store limit type is invalid')
                body = {'rate': rate}
                if limit_type != 'both':
                    body['type'] = limit_type
                values.append({
                    'method': 'POST',
                    'path': f'/pd/api/v1/store/{quoted_store}/limit',
                    'body': body,
                })
        elif kind == 'region':
            region_id = TiKVBackend._target_value(request, numeric=True)
            names = {
                'transfer_leader': 'transfer-leader',
                'add_peer': 'add-peer',
                'add_learner': 'add-learner',
                'remove_peer': 'remove-peer',
                'transfer_peer': 'transfer-peer',
                'split': 'split-region',
                'scatter': 'scatter-region',
                'merge': 'merge-region',
            }
            if operation not in names:
                raise NativeDistributedError(
                    'TiKV Region operation is unavailable')
            values.append({
                'method': 'POST', 'path': '/pd/api/v1/operators',
                'body': TiKVBackend._operator_request(
                    names[operation], region_id, draft
                ),
            })
        elif kind == 'placement-rule':
            group_id, rule_id = TiKVBackend._rule_identity(request)
            if operation in {'create', 'alter'}:
                values.append({
                    'method': 'POST', 'path': '/pd/api/v1/config/rule',
                    'body': TiKVBackend._rule_body(request),
                })
            elif operation == 'drop':
                values.append({
                    'method': 'DELETE',
                    'path': '/pd/api/v1/config/rule/' + '/'.join(
                        urllib.parse.quote(value, safe='')
                        for value in (group_id, rule_id)
                    ),
                })
        elif kind == 'scheduler':
            if operation == 'create':
                name = draft.get('scheduler_name')
                admitted = {
                    'balance-leader-scheduler',
                    'balance-region-scheduler',
                    'balance-hot-region-scheduler',
                    'shuffle-leader-scheduler',
                }
                if name not in admitted:
                    raise NativeDistributedError(
                        'TiKV scheduler is not admitted')
                values.append({
                    'method': 'POST', 'path': '/pd/api/v1/schedulers',
                    'body': {'name': name},
                })
            elif operation == 'drop':
                name = TiKVBackend._target_value(request)
                values.append({
                    'method': 'DELETE',
                    'path': '/pd/api/v1/schedulers/' +
                            urllib.parse.quote(name, safe=''),
                })
            elif operation in {'pause', 'resume'}:
                name = TiKVBackend._target_value(request)
                delay = 0
                if operation == 'pause':
                    delay = draft.get('delay_seconds')
                    if isinstance(delay, bool) or not isinstance(
                            delay, int) or not 1 <= delay <= 86400:
                        raise NativeDistributedError(
                            'TiKV scheduler pause duration is invalid')
                values.append({
                    'method': 'POST',
                    'path': '/pd/api/v1/schedulers/' +
                            urllib.parse.quote(name, safe=''),
                    'body': {'delay': delay},
                })
        if not values:
            raise NativeDistributedError(
                'TiKV control-plane operation is unavailable')
        return {
            'requests': values,
            'impact': {
                'scope': 'region' if kind == 'region' else 'cluster',
                'target_resource_id': (
                    request.get('target_resource') or {}
                ).get('resource_id'),
                'availability_risk': (
                    'high' if operation in {
                        'remove_peer', 'drop', 'mark_offline', 'merge',
                        'remove_tombstones', 'disable', 'archive',
                        'tombstone',
                    }
                    else 'medium'
                ),
                'data_movement_possible': kind in {
                    'cluster', 'store', 'region', 'keyspace',
                    'placement-rule', 'scheduler'
                },
            },
        }

    @staticmethod
    def _operation_parts(request):
        plan = request.get('plan')
        payload = request.get('provider_payload')
        if not isinstance(plan, dict) or not isinstance(payload, dict):
            raise NativeDistributedError(
                'TiKV provider operation handle is invalid')
        return plan, payload

    def inspect_admin_operation(self, request):
        plan, payload = self._operation_parts(request)
        route = self._route(payload)
        kind = plan['resource_kind']
        operation = plan['operation_id']
        if kind == 'store' and operation == 'evict_leaders':
            document = self._pd_document(
                route,
                '/pd/api/v1/scheduler-config/'
                'evict-leader-scheduler/list',
            )
        elif kind == 'cluster':
            path = (
                '/pd/api/v1/stores/limit'
                if operation == 'set_all_store_limits'
                else '/pd/api/v1/stores'
            )
            document = self._pd_document(route, path)
        elif kind == 'keyspace':
            self._require_api_v2(payload)
            name = self._keyspace_name(
                payload, creating=operation == 'create'
            )
            document = self._pd(
                route, '/pd/api/v2/keyspaces/' +
                urllib.parse.quote(name, safe='')
            )
        elif kind == 'store':
            store_id = self._target_value(payload, numeric=True)
            document = self._pd(
                route, f'/pd/api/v1/store/{store_id}'
            )
        elif kind == 'scheduler':
            path = '/pd/api/v1/schedulers'
            if operation in {'pause', 'resume'}:
                name = self._target_value(payload)
                path += '/diagnostic/' + urllib.parse.quote(name, safe='')
            document = self._pd_document(route, path)
        elif kind == 'region':
            region_id = self._target_value(payload, numeric=True)
            regions = self._pd(
                route, '/pd/api/v1/regions'
            ).get('regions', [])
            document = next((
                copy.deepcopy(item) for item in regions
                if isinstance(item, dict) and item.get('id') == region_id
            ), {'id': region_id, 'region_absent': True})
            records = self._pd_document(
                route, '/pd/api/v1/operators/records'
            )
            document['_cdeadmin_region_ids'] = [
                item.get('id') for item in regions
                if isinstance(item, dict) and item.get('id') is not None
            ][:self.MAX_RECORDS]
            document['_cdeadmin_operator_records'] = [
                item[:4096] for item in (
                    records[-512:]
                    if isinstance(records, list) else []
                ) if isinstance(item, str)
            ]
        elif kind == 'placement-rule':
            document = self._pd_document(
                route, '/pd/api/v1/config/rules'
            )
        else:
            raise NativeDistributedError(
                'TiKV provider operation observation is unavailable')
        return {
            'resource_kind': kind,
            'operation_id': operation,
            'provider_document': copy.deepcopy(document),
            'provider_observation_only': True,
            'provider_finality_authority': True,
        }

    def cancel_admin_operation(self, request):
        plan, payload = self._operation_parts(request)
        route = self._route(payload)
        kind = plan['resource_kind']
        if kind == 'store' and plan['operation_id'] == 'evict_leaders':
            store_id = self._target_value(payload, numeric=True)
            path = (
                '/pd/api/v1/schedulers/evict-leader-scheduler-' +
                str(store_id)
            )
        elif kind == 'region':
            region_id = self._target_value(payload, numeric=True)
            path = f'/pd/api/v1/operators/{region_id}'
        else:
            raise NativeDistributedError(
                'TiKV operation is not declared cancellable')
        return self._pd_mutate(route, 'DELETE', path)

    @staticmethod
    def _document_list(document, field):
        if isinstance(document, list):
            return document
        if isinstance(document, dict) and isinstance(
                document.get(field), list):
            return document[field]
        return []

    def validate_admin_post_state(self, request):
        plan, payload = self._operation_parts(request)
        observation = self.inspect_admin_operation(request)
        document = observation['provider_document']
        kind = plan['resource_kind']
        operation = plan['operation_id']
        draft = payload.get('draft') or {}
        confirmed = False
        reason = 'provider_state_does_not_match_requested_state'
        if kind == 'store' and operation == 'evict_leaders':
            store_id = self._target_value(payload, numeric=True)
            ranges = document.get('store-id-ranges', {}) if isinstance(
                document, dict
            ) else {}
            confirmed = str(store_id) in ranges
        elif kind == 'keyspace' and isinstance(document, dict):
            expected_name = self._keyspace_name(
                payload, creating=operation == 'create'
            )
            confirmed = document.get('name') == expected_name
            if confirmed and operation == 'update_config':
                observed = document.get('config') or {}
                confirmed = all(
                    (
                        key not in observed if value is None
                        else observed.get(key) == value
                    )
                    for key, value in self._keyspace_config(
                        payload, required=True
                    ).items()
                )
            elif confirmed and operation in {
                    'enable', 'disable', 'archive', 'tombstone'}:
                state = {
                    'enable': 'ENABLED', 'disable': 'DISABLED',
                    'archive': 'ARCHIVED', 'tombstone': 'TOMBSTONE',
                }[operation]
                confirmed = str(document.get('state', '')).upper() == (
                    state
                )
        elif kind == 'store':
            store = document.get('store', {}) if isinstance(
                document, dict) else {}
            if operation in {'mark_offline', 'bring_up'}:
                expected = (
                    'Offline' if operation == 'mark_offline' else 'Up'
                )
                confirmed = str(store.get('state_name', '')).lower() == (
                    expected.lower()
                )
            elif operation == 'set_labels':
                observed = {
                    item.get('key'): item.get('value')
                    for item in store.get('labels', [])
                    if isinstance(item, dict) and item.get('key') is not None
                }
                confirmed = all(
                    observed.get(key) == value
                    for key, value in self._store_labels(draft).items()
                )
            elif operation == 'delete_label':
                confirmed = all(
                    item.get('key') != draft.get('label_key')
                    for item in store.get('labels', [])
                    if isinstance(item, dict)
                )
        elif kind == 'scheduler':
            name = draft.get('scheduler_name') if operation == 'create' else (
                self._target_value(payload)
            )
            if operation in {'create', 'drop'}:
                schedulers = self._document_list(document, 'schedulers')
                confirmed = (
                    (name in schedulers) == (operation == 'create')
                )
            elif operation in {'pause', 'resume'}:
                status = str(document.get('status', '')).lower() if (
                    isinstance(document, dict)
                ) else ''
                confirmed = (
                    status == 'paused' if operation == 'pause' else
                    status in {'normal', 'pending', 'scheduling'}
                )
        elif kind == 'placement-rule':
            group_id, rule_id = self._rule_identity(payload)
            rules = self._document_list(document, 'rules')
            matching = [
                item for item in rules if isinstance(item, dict) and
                item.get('group_id') == group_id and item.get('id') == rule_id
            ]
            confirmed = bool(matching) != (operation == 'drop')
            if confirmed and operation in {'create', 'alter'}:
                requested = self._rule_body(payload)
                observed = matching[0]
                defaults = {
                    'start_key': '', 'end_key': '',
                    'location_labels': [], 'label_constraints': [],
                    'override': False, 'isolation_level': '', 'index': 0,
                }
                confirmed = all(
                    observed.get(field, defaults.get(field)) ==
                    requested.get(field, defaults.get(field))
                    for field in {
                        'group_id', 'id', 'role', 'count', 'start_key',
                        'end_key', 'location_labels', 'label_constraints',
                        'override', 'isolation_level', 'index',
                    }
                )
        elif kind == 'region' and isinstance(document, dict):
            peers = document.get('peers') or []
            leader = document.get('leader') or {}
            stores = {
                item.get('store_id') for item in peers
                if isinstance(item, dict)
            }
            peer_roles = {
                item.get('store_id'): str(
                    item.get('role_name', '')
                ).lower()
                for item in peers if isinstance(item, dict)
            }
            if operation == 'transfer_leader':
                confirmed = leader.get('store_id') == draft.get(
                    'to_store_id')
            elif operation == 'add_peer':
                confirmed = peer_roles.get(
                    draft.get('store_id')
                ) in {'voter', 'incomingvoter'}
            elif operation == 'add_learner':
                confirmed = peer_roles.get(
                    draft.get('store_id')
                ) in {'learner', 'demotinglearner'}
            elif operation == 'remove_peer':
                confirmed = draft.get('store_id') not in stores
            elif operation == 'transfer_peer':
                confirmed = (
                    draft.get('from_store_id') not in stores and
                    draft.get('to_store_id') in stores
                )
            elif operation in {'split', 'scatter', 'merge'}:
                result = request.get('provider_result') or {}
                baseline = result.get('pre_dispatch_observation') or {}
                previous_records = set(
                    baseline.get('operator_records') or []
                )
                current_records = document.get(
                    '_cdeadmin_operator_records') or []
                native_terms = {
                    'split': ('split',),
                    'scatter': ('scatter',),
                    'merge': ('merge',),
                }[operation]
                region_id = self._target_value(payload, numeric=True)
                target_region = draft.get('target_region_id')
                terminal_record = next((
                    item for item in current_records
                    if item not in previous_records and
                    ' finished ' in item and (
                        (
                            operation == 'merge' and
                            f'merge: region {region_id} to '
                            f'{target_region}' in item
                        ) or (
                            operation != 'merge' and
                            f'region:{region_id}(' in item and
                            any(
                                term in item.lower()
                                for term in native_terms
                            )
                        )
                    )
                ), None)
                before_ids = set(baseline.get('region_ids') or [])
                after_ids = set(
                    document.get('_cdeadmin_region_ids') or []
                )
                if operation == 'split':
                    confirmed = bool(
                        terminal_record and len(after_ids) > len(before_ids)
                    )
                elif operation == 'merge':
                    confirmed = bool(
                        terminal_record and region_id not in after_ids and
                        target_region in after_ids and
                        len(after_ids) < len(before_ids)
                    )
                else:
                    confirmed = bool(
                        terminal_record and region_id in after_ids and
                        peers and leader.get('store_id') in stores
                    )
                if not confirmed:
                    reason = (
                        f'{operation}_requires_new_finished_operator_record_'
                        'and_matching_region_lineage'
                    )
        return {
            'confirmed': confirmed,
            'reason': None if confirmed else reason,
            'observation': observation,
            'provider_finality_authority': True,
        }

    def read_admin_rows(self, request):
        route = self._route(request)
        target = request.get('target_resource') or {
            'resource_kind': 'key-range',
            'display_path': ['bounded-key-browser'],
        }
        rows, native = self._run_helper(route, {
            'operation': 'scan',
            'start_key': request.get('start_key', ''),
            'end_key': request.get('end_key', ''),
            'limit': request.get('limit', 100),
            'include_ttl': route['enable_ttl'],
        })
        for row in rows:
            try:
                key = base64.b64decode(row['key_base64'], validate=True)
                value = base64.b64decode(
                    row['value_base64'], validate=True
                )
            except (KeyError, ValueError, binascii.Error) as exc:
                raise NativeDistributedError(
                    'TiKV key page identity is invalid'
                ) from exc
            row['values'] = {
                'key': row.get('key'), 'key_base64': row['key_base64'],
                'value': row.get('value'),
                'value_base64': row['value_base64'],
                'ttl_seconds_remaining': row.get(
                    'ttl_seconds_remaining'),
            }
            row['identity_token'] = self._row_identities.issue(
                route, target, key, value
            )
        return {
            'schema': 'cdeadmin.native-row-page.v1',
            'columns': [
                {'name': 'key', 'key': True, 'editable': False},
                {'name': 'key_base64', 'key': False, 'editable': False},
                {'name': 'value', 'key': False, 'editable': True},
                {'name': 'value_base64', 'key': False, 'editable': True},
                {'name': 'ttl_seconds_remaining', 'key': False,
                 'editable': False},
            ],
            'rows': rows, 'native': native, 'editable': True,
            'complete': len(rows) < request.get('limit', 100),
            'identity_policy': 'provider-key-and-original-value',
            'transaction_finality_interpreted_by_common_code': False,
        }

    @staticmethod
    def complete(_request):
        return []

    def close(self):
        self._row_identities.clear()
