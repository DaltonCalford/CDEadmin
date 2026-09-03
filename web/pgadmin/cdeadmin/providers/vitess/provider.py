"""Vitess 23.0.3 VTGate distributed relational provider."""

import json
import re
import ssl
import urllib.request
from collections.abc import Mapping
from dataclasses import replace

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
    RelationalDBAPIClient,
    RelationalClientError,
)
from ..distributed_sql import (
    create_sql_client,
    mysql_catalog,
    optional_rows,
    resource,
    sql_administration,
)
from ..relational_admin import RelationalAdministration
from ..distributed_control_plane import DistributedSQLControlPlane
from .control_plane import (
    OPERATIONS as CONTROL_OPERATIONS,
    cancel_action,
    compile_action,
    execute_action,
    inspect_action,
    post_validate_action,
)


PROFILE = PilotProfile(
    'org.cdeadmin.vitess', 'vitess-native', 'vitess', 'Vitess', '23.0.3',
    'mysql_wire', 'distributed-relational', 'vitess-sql', 'Vitess SQL',
    'vitess-vtgate-transaction-native', 'tabular',
    (
        'cluster', 'cell', 'keyspace', 'shard', 'tablet', 'vtgate',
        'vttablet', 'vschema', 'database', 'table', 'view', 'column', 'index',
        'vindex', 'routing-rule', 'workflow', 'vreplication-stream',
        'materialize', 'online-ddl', 'backup', 'user', 'role', 'privilege',
    ),
    ('mysql-client', 'vtctldclient', 'vtadmin', 'backup-restore'),
    semantic_sql_dialect={
        'language_profile': 'vitess-sql', 'quote_open': '`',
        'quote_close': '`', 'supports_rollup': False,
    },
)


_BASE_ADMINISTRATION = sql_administration('vitess', 'mysql', (
    'cell', 'keyspace', 'shard', 'tablet', 'vtgate', 'vttablet',
    'vschema', 'routing-rule', 'workflow',
    'vreplication-stream', 'materialize', 'online-ddl', 'backup',
), provider_read_only=('user', 'role', 'privilege', 'view'))


def _version(row):
    value = str(row[0] if row else '')
    match = re.search(r'(\d+\.\d+\.\d+)', value)
    if match is None:
        raise RelationalClientError('Vitess version is unavailable')
    return match.group(1)


class VitessAdministration(RelationalAdministration):
    """Compile structured Vitess VSchema operations for sharded tables."""

    def validate(self, request):
        result = super().validate(request)
        errors = result['errors']
        kind = request.get('resource_kind')
        operation = request.get('operation_id')
        draft = request.get('draft')
        if not isinstance(draft, Mapping):
            return result
        if kind == 'table' and operation == 'create' and draft.get(
            'register_in_vschema', True
        ):
            route_keyspace = self._route_keyspace(request)
            parent = draft.get('parent')
            if parent and route_keyspace and str(parent) != route_keyspace:
                errors.append({
                    'field_id': 'parent',
                    'code': 'route_keyspace_mismatch',
                    'message': (
                        'The table parent must match the routed keyspace.'
                    ),
                })
            if not draft.get('vindex_name'):
                errors.append({
                    'field_id': 'vindex_name',
                    'code': 'required_for_vschema_registration',
                    'message': 'A primary vindex name is required.',
                })
            columns = draft.get('vindex_columns')
            if not isinstance(columns, list) or not columns:
                errors.append({
                    'field_id': 'vindex_columns',
                    'code': 'required_for_vschema_registration',
                    'message': (
                        'At least one primary vindex column is required.'
                    ),
                })
        if kind == 'vindex' and operation == 'create' and not draft.get(
            'vindex_type'
        ):
            errors.append({
                'field_id': 'vindex_type',
                'code': 'required',
                'message': 'A Vitess vindex type is required.',
            })
        return result

    def _form(self, kind, operation):
        if kind == 'vindex' and operation == 'create':
            return {
                'form_id': 'vindex.create',
                'title': 'Create global vindex',
                'fields': [
                    self._field('name', 'Name', 'text', True),
                    self._field(
                        'vindex_type', 'Vindex type', 'text', True,
                        'For example: xxhash, hash, lookup_hash.',
                    ),
                    self._field(
                        'parameters', 'Vindex parameters', 'json', False,
                        'Structured key/value parameters passed after WITH.',
                        {},
                    ),
                ],
            }
        value = super()._form(kind, operation)
        if kind == 'table' and operation == 'create':
            value['fields'].extend((
                self._field(
                    'register_in_vschema', 'Register in VSchema',
                    'boolean', False,
                    'Required for routed writes in a sharded keyspace.', True,
                ),
                self._field(
                    'vindex_name', 'Primary vindex name', 'text', False,
                    'Use an existing vindex name, or supply a type to create '
                    'it while attaching the table.', 'xxhash',
                ),
                self._field(
                    'vindex_columns', 'Primary vindex columns', 'json', False,
                    'Ordered column-name array used to route table rows.',
                ),
                self._field(
                    'vindex_type', 'New vindex type', 'text', False,
                    'Leave blank when attaching an existing global vindex.',
                ),
                self._field(
                    'vindex_parameters', 'New vindex parameters', 'json',
                    False, 'Structured key/value parameters.', {},
                ),
            ))
        elif kind == 'table' and operation == 'drop':
            value['fields'].insert(0, self._field(
                'drop_vschema_registration', 'Remove VSchema registration',
                'boolean', False,
                'Remove the table routing definition before physical DDL.',
                True,
            ))
        return value

    def _normalize_draft(self, kind, operation, draft):
        value = super()._normalize_draft(kind, operation, draft)
        if operation == 'create' and kind in {'table', 'vindex'}:
            options = value['options']
            for key in (
                'register_in_vschema', 'vindex_name', 'vindex_columns',
                'vindex_type', 'vindex_parameters', 'parameters',
            ):
                if key in value:
                    options[key] = value.pop(key)
        return value

    def _compile(self, request):
        if (
            request['resource_kind'] == 'table' and
            request['operation_id'] == 'drop' and
            request['draft'].get('drop_vschema_registration', True)
        ):
            path = self._target_path(request['target_resource'])
            self._require_target_keyspace(request, path)
            table = self._quote(path[-1])
            return {'statements': [
                {
                    'source': f'ALTER VSCHEMA DROP TABLE {table}',
                    'parameters': (),
                },
                super()._compile_drop(request),
            ]}
        return super()._compile(request)

    def _compile_create(self, request):
        kind = request['resource_kind']
        draft = request['draft']
        options = draft.get('options') or {}
        if kind == 'vindex':
            name = self._quote(self._identifier(draft['name']))
            vindex_type = self._quote(self._identifier(
                options.get('vindex_type')
            ))
            source = (
                f'ALTER VSCHEMA CREATE VINDEX {name} USING {vindex_type}'
            )
            source += self._vindex_parameters(options.get('parameters'))
            return [{'source': source, 'parameters': ()}]
        statements = super()._compile_create(request)
        if kind != 'table' or not options.get(
            'register_in_vschema', True
        ):
            return statements
        name = self._quote(self._identifier(draft['name']))
        vindex = self._quote(self._identifier(options.get('vindex_name')))
        columns = self._identifier_list(options.get('vindex_columns'))
        source = (
            f'ALTER VSCHEMA ON {name} ADD VINDEX {vindex} ({columns})'
        )
        vindex_type = options.get('vindex_type')
        if vindex_type:
            source += ' USING ' + self._quote(self._identifier(vindex_type))
        source += self._vindex_parameters(options.get('vindex_parameters'))
        statements.append({'source': source, 'parameters': ()})
        return statements

    def _compile_drop(self, request):
        if request['resource_kind'] == 'vindex':
            path = self._target_path(request['target_resource'])
            self._require_target_keyspace(request, path)
            name = self._quote(path[-1])
            return {
                'source': f'ALTER VSCHEMA DROP VINDEX {name}',
                'parameters': (),
            }
        return super()._compile_drop(request)

    @staticmethod
    def _route_keyspace(request):
        route = request.get('_provider_route')
        database = route.get('database') if isinstance(route, Mapping) else ''
        return str(database or '').split(':', 1)[0].split('@', 1)[0]

    def _require_target_keyspace(self, request, path):
        route_keyspace = self._route_keyspace(request)
        if len(path) > 1 and route_keyspace and path[-2] != route_keyspace:
            raise RelationalClientError(
                'Vitess target belongs to another routed keyspace'
            )

    def _vindex_parameters(self, parameters):
        if parameters in (None, {}):
            return ''
        if not isinstance(parameters, Mapping):
            raise RelationalClientError(
                'vindex parameters must be a structured object'
            )
        fragments = []
        for key, value in parameters.items():
            name = self._quote(self._identifier(key))
            if isinstance(value, bool):
                literal = 'true' if value else 'false'
            elif isinstance(value, int) and not isinstance(value, bool):
                literal = str(value)
            elif isinstance(value, str) and '\x00' not in value:
                literal = "'" + value.replace("'", "''") + "'"
            else:
                raise RelationalClientError(
                    'vindex parameter values must be text, integer, or boolean'
                )
            fragments.append(f'{name}={literal}')
        return ' WITH ' + ', '.join(fragments)


_VITESS_SUPPORTED = dict(_BASE_ADMINISTRATION.dialect.supported)
_VITESS_SUPPORTED['vindex'] = frozenset({'inspect', 'create', 'drop'})
_VITESS_DIALECT = replace(
    _BASE_ADMINISTRATION.dialect,
    supported=_VITESS_SUPPORTED,
)
_CONTROL_ADMINISTRATION = DistributedSQLControlPlane(
    _VITESS_DIALECT, CONTROL_OPERATIONS, compile_action,
    inspector=inspect_action, canceller=cancel_action,
    post_validator=post_validate_action, action_executor=execute_action,
)


class VitessControlAdministration(VitessAdministration):
    """Combine VSchema SQL administration with vtctld control actions."""

    def supports(self, resource_kind, operation_id):
        return _CONTROL_ADMINISTRATION.control_plane.supports(
            resource_kind, operation_id
        ) or super().supports(resource_kind, operation_id)

    def catalog(self, catalog):
        return _CONTROL_ADMINISTRATION.control_plane.apply(
            super().catalog(catalog)
        )

    def validate(self, request):
        if _CONTROL_ADMINISTRATION.control_plane.supports(
                request.get('resource_kind'), request.get('operation_id')):
            return _CONTROL_ADMINISTRATION.validate(request)
        return super().validate(request)

    def plan(self, request):
        if _CONTROL_ADMINISTRATION.control_plane.supports(
                request.get('resource_kind'), request.get('operation_id')):
            return _CONTROL_ADMINISTRATION.plan(request)
        return super().plan(request)

    def apply(self, client, request):
        plan = request.get('plan') or {}
        if _CONTROL_ADMINISTRATION.control_plane.supports(
                plan.get('resource_kind'), plan.get('operation_id')):
            return _CONTROL_ADMINISTRATION.apply(client, request)
        return super().apply(client, request)

    def inspect_operation(self, client, request):
        return _CONTROL_ADMINISTRATION.inspect_operation(client, request)

    def cancel_operation(self, client, request):
        return _CONTROL_ADMINISTRATION.cancel_operation(client, request)

    def validate_operation_post_state(self, client, request):
        return _CONTROL_ADMINISTRATION.validate_operation_post_state(
            client, request
        )


ADMINISTRATION = VitessControlAdministration(_VITESS_DIALECT)


class VitessDBAPIClient(RelationalDBAPIClient):
    """Verify both the VTGate MySQL wire and its native build identity."""

    MAX_IDENTITY_BYTES = 1024 * 1024

    def __init__(self, config, module=None, opener=None):
        super().__init__(config, module)
        self._opener = opener or urllib.request.urlopen

    @staticmethod
    def _http_route(request):
        route = request.get('route')
        if not isinstance(route, Mapping):
            raise RelationalClientError('Vitess route is required')
        host = str(route.get('vtgate_http_host') or route.get('host') or '')
        if not host or any(character in host for character in '/?#@'):
            raise RelationalClientError('Vitess HTTP host is invalid')
        try:
            port = int(route.get('vtgate_http_port', 15001))
            timeout = float(route.get('vtgate_http_timeout', 10))
        except (TypeError, ValueError):
            raise RelationalClientError(
                'Vitess HTTP route values are invalid'
            ) from None
        if not 1 <= port <= 65535 or not 0 < timeout <= 30:
            raise RelationalClientError(
                'Vitess HTTP route values are outside approved bounds'
            )
        tls_mode = str(route.get('vtgate_http_tls_mode', 'disable'))
        if tls_mode not in {'disable', 'require', 'verify-ca', 'verify-full'}:
            raise RelationalClientError('Vitess HTTP TLS mode is invalid')
        return route, host, port, timeout, tls_mode

    @staticmethod
    def _ssl_context(route, tls_mode):
        if tls_mode == 'disable':
            return None
        if tls_mode == 'require':
            return ssl._create_unverified_context()
        context = ssl.create_default_context(
            cafile=route.get('vtgate_http_ca_file')
        )
        context.check_hostname = tls_mode == 'verify-full'
        return context

    def _native_identity(self, request):
        route, host, port, timeout, tls_mode = self._http_route(request)
        address = (
            f'[{host}]' if ':' in host and not host.startswith('[') else host
        )
        scheme = 'http' if tls_mode == 'disable' else 'https'
        url = f'{scheme}://{address}:{port}/debug/vars'
        try:
            with self._opener(
                urllib.request.Request(url, method='GET'),
                timeout=timeout,
                context=self._ssl_context(route, tls_mode),
            ) as response:
                payload = response.read(self.MAX_IDENTITY_BYTES + 1)
            if len(payload) > self.MAX_IDENTITY_BYTES:
                raise RelationalClientError(
                    'Vitess identity response exceeds size limit'
                )
            document = json.loads(payload)
        except RelationalClientError:
            raise
        except Exception as exc:
            raise RelationalClientError(
                f'Vitess native identity verification failed '
                f'({type(exc).__name__})'
            ) from None
        if not isinstance(document, Mapping):
            raise RelationalClientError('Vitess native identity is invalid')
        version = _version((document.get('BuildVersion'),))
        revision = str(document.get('BuildGitRev') or 'unknown')
        return version, revision

    def runtime_identity(self, request, handle=None):
        temporary = handle is None
        connection = handle or self._connect(request)
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute('SELECT 1')
            row = cursor.fetchone()
            if not row or row[0] != 1:
                raise RelationalClientError(
                    'Vitess SQL endpoint verification failed'
                )
            version, revision = self._native_identity(request)
            return {
                'engine_id': PROFILE.engine_id,
                'version': version,
                'build_id': f'{PROFILE.engine_id}:{version}:{revision}',
                'protocol_id': PROFILE.protocol_id,
            }
        except RelationalClientError:
            raise
        except Exception as exc:
            raise RelationalClientError(
                f'Vitess profile verification failed '
                f'({type(exc).__name__})'
            ) from None
        finally:
            if cursor is not None:
                self._safe_close(cursor)
            if temporary:
                self._forget_and_close(connection)


def _extras(cursor, _request, generation):
    values = [resource('vtgate', [], 'connected-vtgate', generation)]
    commands = (
        ('keyspace', 'SHOW VITESS_KEYSPACES'),
        ('shard', 'SHOW VITESS_SHARDS'),
        ('tablet', 'SHOW VITESS_TABLETS'),
    )
    for kind, source in commands:
        for row in optional_rows(cursor, source):
            name = row[0]
            values.append(resource(kind, [], name, generation, {
                'details': [str(item) for item in row[1:]],
            }))
    route = _request.get('route') or _request.get('_provider_route') or {}
    current_keyspace = str(route.get('database') or '').split(
        ':', 1
    )[0].split('@', 1)[0]
    for row in optional_rows(cursor, 'SHOW VSCHEMA VINDEXES'):
        keyspace, name, vindex_type, parameters, owner = row
        if current_keyspace and str(keyspace) != current_keyspace:
            continue
        values.append(resource('vschema', [], keyspace, generation))
        values.append(resource('vindex', [keyspace], name, generation, {
            'vindex_type': str(vindex_type),
            'parameters': str(parameters),
            'owner': str(owner),
        }))
    return values


class VitessProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    return VitessProvider(
        context,
        permissions,
        client or create_sql_client(
            PROFILE,
            permissions,
            wire='mysql',
            version_query='SELECT 1',
            version_parser=_version,
            metadata_reader=lambda connection, request: mysql_catalog(
                connection, request, 'Vitess', _extras
            ),
            administration=ADMINISTRATION,
            client_class=VitessDBAPIClient,
        ),
    )
