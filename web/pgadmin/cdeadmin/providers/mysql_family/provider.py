"""Shared MySQL wire, distinct MySQL and MariaDB semantic profiles."""

import copy
import hashlib
import json
import os
import queue
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from importlib import resources as package_resources

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
    RelationalClientConfig,
    RelationalDBAPIClient,
    RelationalClientError,
    RelationalCredentialError,
    ProviderToolError,
    ProviderToolGrant,
    ProviderToolRunner,
)
from ..relational_admin import (
    RelationalAdministration,
    RelationalAdminDialect,
)
from ..distributed_sql import mysql_route
from ..form_contracts import provider_form_contract


MYSQL_PROFILE = PilotProfile(
    'org.cdeadmin.mysql', 'mysql-native', 'mysql', 'MySQL', '9.7.0',
    'mysql_wire', 'relational', 'mysql-sql', 'MySQL SQL',
    'mysql-session-autocommit', 'tabular',
    ('server', 'database', 'table', 'column', 'view', 'materialized-view',
     'index', 'constraint', 'trigger', 'event', 'procedure', 'function',
     'partition', 'tablespace', 'user', 'role', 'privilege', 'plugin',
     'replication-channel', 'resource-group', 'metric'),
    ('mysql-shell', 'backup', 'replication', 'account-administration'),
    semantic_sql_dialect={
        'contract_complete': True,
        'language_profile': 'mysql-sql', 'quote_open': '`',
        'quote_close': '`', 'supports_rollup': True,
        'rollup_style': 'with_rollup', 'rollup_allows_order_by': False,
        'limit_style': 'limit',
        'true_literal': 'TRUE', 'false_literal': 'FALSE',
        'time_operations': (
            'as_of', 'range', 'period_to_date', 'period_comparison',
        ),
        'window_operations': (
            'running_sum', 'moving_sum', 'moving_average', 'lag', 'delta',
            'percent_change', 'rank', 'dense_rank',
        ),
    },
    dialect_contract_id='mysql.dialect.9.7.0.v1',
    dialect_evidence=(
        'mysql-9.7.0-source-and-runtime-inventory',
        'mysql-9.7.0-task-live-execution',
    ),
    dialect_contract_file='mysql_dialect_9_7_0.json',
    metrics_contract_file='mysql_metrics_9_7_0.json',
    query_plan_templates=(
        ('MySQL JSON query plan', 'EXPLAIN FORMAT=JSON {source}'),
        ('MySQL execution plan', 'EXPLAIN ANALYZE {source}'),
    ),
)
MARIADB_PROFILE = PilotProfile(
    'org.cdeadmin.mariadb', 'mariadb-native', 'mariadb', 'MariaDB', '12.2.2',
    'mysql_wire', 'relational', 'mariadb-sql', 'MariaDB SQL',
    'mariadb-session-transaction', 'tabular',
    ('server', 'database', 'table', 'column', 'view', 'index', 'constraint',
     'sequence', 'trigger', 'event', 'procedure', 'function', 'package',
     'partition', 'tablespace', 'user', 'role', 'privilege',
     'plugin', 'replication-channel', 'server-link', 'metric', 'session',
     'system-variable', 'lock', 'lock-wait', 'table-storage', 'binary-log',
     'binary-log-event', 'binary-log-status', 'log-configuration',
     'general-log-entry', 'slow-query', 'tls-configuration'),
    ('mariadb-client', 'mariadb-dump', 'mariadb-upgrade', 'replication',
     'user-administration'),
    semantic_sql_dialect={
        'contract_complete': True,
        'language_profile': 'mariadb-sql', 'quote_open': '`',
        'quote_close': '`', 'supports_rollup': True,
        'rollup_style': 'with_rollup', 'rollup_allows_order_by': False,
        'limit_style': 'limit',
        'true_literal': 'TRUE', 'false_literal': 'FALSE',
        'time_operations': (
            'as_of', 'range', 'period_to_date', 'period_comparison',
        ),
        'window_operations': (
            'running_sum', 'moving_sum', 'moving_average', 'lag', 'delta',
            'percent_change', 'rank', 'dense_rank',
        ),
    },
    dialect_contract_id='mariadb.dialect.12.2.2.v1',
    dialect_evidence=(
        'mariadb-12.2.2-source-and-runtime-inventory',
        'mariadb-12.2.2-task-live-execution',
    ),
    dialect_contract_file='mariadb_dialect_12_2_2.json',
    metrics_contract_file='mariadb_metrics_12_2_2.json',
    starter_source=(
        'SELECT id, value, event_date\n'
        'FROM qualification\n'
        'ORDER BY id\n'
        'LIMIT 100'
    ),
    source_presets=(
        ('Server identity',
         'SELECT VERSION() AS version, @@hostname AS hostname, '
         'CURRENT_USER() AS current_user'),
        ('Current database',
         'SELECT DATABASE() AS current_database, '
         '@@transaction_isolation AS transaction_isolation'),
        ('Qualification rows',
         'SELECT id, value, event_date FROM qualification '
         'ORDER BY id LIMIT 100'),
    ),
    query_plan_templates=(
        ('MariaDB JSON query plan', 'EXPLAIN FORMAT=JSON {source}'),
        ('MariaDB JSON execution analysis',
         'ANALYZE FORMAT=JSON {source}'),
    ),
)


def _administration(profile):
    common = {
        'server': frozenset({'inspect'}),
        'partition': frozenset({'inspect'}),
        'tablespace': frozenset({'inspect'}),
        'replication-channel': frozenset({'inspect'}),
        'resource-group': frozenset({'inspect'}),
        'server-link': frozenset({'inspect'}),
        'database': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'table': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
            'insert', 'update', 'delete',
        }),
        'view': frozenset({'inspect', 'create', 'drop'}),
        'materialized-view': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'column': frozenset({'inspect', 'create', 'rename', 'drop'}),
        'constraint': frozenset({'inspect', 'create', 'drop'}),
        'index': frozenset({'inspect', 'create', 'drop'}),
        'trigger': frozenset({'inspect', 'create', 'drop'}),
        'procedure': frozenset({'inspect', 'create', 'drop'}),
        'function': frozenset({'inspect', 'create', 'drop'}),
        'event': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'sequence': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'role': frozenset({
            'inspect', 'create', 'drop',
        }),
        'user': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'privilege': frozenset({'inspect', 'grant', 'revoke'}),
        'plugin': frozenset({'inspect', 'create', 'drop'}),
        'metric': frozenset({'inspect'}),
    }
    if profile is MYSQL_PROFILE:
        common.pop('sequence')
        common['database'] = common['database'] | frozenset({
            'analyze_tables', 'check_tables', 'optimize_tables',
            'repair_tables', 'checksum_tables', 'backup_logical',
            'restore_logical',
        })
    else:
        common.pop('materialized-view')
        common['server'] = frozenset({'inspect', 'check_upgrade_required'})
        for resource_kind in (
                'session', 'system-variable', 'lock', 'lock-wait',
                'table-storage', 'binary-log', 'binary-log-status',
                'binary-log-event', 'log-configuration',
                'general-log-entry', 'slow-query', 'tls-configuration'):
            common[resource_kind] = frozenset({'inspect'})
        common['system-variable'] = frozenset({'inspect', 'set_global'})
        common['session'] = frozenset({
            'inspect', 'terminate_query', 'terminate_connection',
        })
        common['binary-log'] = frozenset({'inspect', 'purge_before'})
        common['binary-log-status'] = frozenset({'inspect', 'rotate'})
        common['role'] = frozenset({
            'inspect', 'create', 'grant', 'revoke', 'set_default', 'drop',
        })
        common['replication-channel'] = frozenset({
            'inspect', 'create', 'alter', 'start', 'stop', 'reset',
        })
        common['database'] = common['database'] | frozenset({
            'analyze_tables', 'check_objects', 'optimize_tables',
            'repair_objects', 'checksum_tables', 'backup_logical',
            'restore_logical',
        })
        common['package'] = frozenset({
            'inspect', 'create', 'alter', 'drop',
        })
    return RelationalAdministration(RelationalAdminDialect(
        engine_id=profile.engine_id,
        quote_open='`',
        quote_close='`',
        parameter='%s',
        supported=common,
        supports_cascade=profile is not MARIADB_PROFILE,
        not_applicable_concepts=frozenset({
            'domains', 'types',
            *(
                {'sequences'} if profile is MYSQL_PROFILE
                else {'materialized_views'}
            ),
        }),
        concept_resource_kinds={'schemas': ('database',)},
        database_forms=provider_form_contract({
            'profile_id': profile.profile_id,
            'engine_id': profile.engine_id,
            'display_name': profile.engine_name,
            'route_kind': 'network',
            'connection_fields': [],
            'secret_fields': [],
        })['database']['forms'],
    ))


MYSQL_ADMINISTRATION = _administration(MYSQL_PROFILE)
MARIADB_ADMINISTRATION = _administration(MARIADB_PROFILE)


def _metric_records(profile):
    """Load only the metrics admitted by the exact provider contract."""
    artifact = package_resources.files(__package__).joinpath(
        profile.metrics_contract_file
    )
    document = json.loads(artifact.read_text(encoding='utf-8'))
    observations = {
        item['observation_id']: item
        for item in document['native_observations']
    }
    return [{
        **observations[item['observation_id']], **item,
    } for item in document['metrics']]


class _MariaDBConnectorFacade:
    """Route MariaDB pooled connections through its native pool object."""

    _POOL_ARGUMENTS = frozenset({
        'pool_name', 'pool_size', 'pool_reset_connection',
        'pool_validation_interval',
    })

    def __init__(self, module, pool_namespace):
        self.module = module
        self.pool_namespace = str(pool_namespace)
        self._pools = {}

    def __call__(self, *args, **kwargs):
        options = dict(kwargs)
        pool_options = {
            key: options.pop(key)
            for key in tuple(options)
            if key in self._POOL_ARGUMENTS
        }
        if not pool_options.get('pool_size'):
            return self.module.connect(*args, **options)
        if args:
            raise RelationalClientError(
                'MariaDB pooled connections require named arguments'
            )
        pool_name = pool_options.get('pool_name')
        if not pool_name:
            pool_name = 'cde_' + hashlib.sha256(
                self.pool_namespace.encode('utf-8')
            ).hexdigest()[:24]
            pool_options['pool_name'] = pool_name
        pool = self._pools.get(pool_name)
        if pool is None:
            factory = getattr(self.module, 'ConnectionPool', None)
            if not callable(factory):
                raise RelationalClientError(
                    'MariaDB connector has no native connection pool'
                )
            pool = factory(**pool_options, **options)
            self._pools[pool_name] = pool
        return pool.get_connection()

    def close(self):
        for pool in tuple(self._pools.values()):
            close = getattr(pool, 'close', None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self._pools.clear()


@dataclass
class _MariaDBStreamToken:
    """Provider-private state for one MariaDB streamed execution."""

    connection: object
    request: dict
    stream_reference: str = field(default_factory=lambda: str(uuid.uuid4()))
    cursor: object | None = None
    columns: tuple = ()
    rowcount: int | None = None
    cancel_requested: bool = False
    cancelled: bool = False
    closed: bool = False
    error: RelationalClientError | None = None
    pages: queue.Queue = field(
        default_factory=lambda: queue.Queue(maxsize=2), repr=False
    )
    ready: threading.Event = field(default_factory=threading.Event, repr=False)
    finished: threading.Event = field(
        default_factory=threading.Event, repr=False
    )
    worker: threading.Thread | None = field(default=None, repr=False)


class MariaDBDBAPIClient(RelationalDBAPIClient):
    """DB-API client using MariaDB's explicit ``ConnectionPool`` API."""

    _STREAM_PAGE_SIZE = 500
    _INITIAL_POLL_WAIT_SECONDS = 0.25

    def __init__(self, config, pool_namespace, module=None, tool_runner=None):
        super().__init__(config, module)
        self._mariadb_connector = _MariaDBConnectorFacade(
            self.module, pool_namespace
        )
        self._connector = self._mariadb_connector
        self._stream_tokens = []
        self._session_requests = {}
        self._stream_lock = threading.RLock()
        self._tool_runner = tool_runner or ProviderToolRunner({
            'mariadb-dump': os.environ.get(
                'CDEADMIN_MARIADB_DUMP_BINARY', 'mariadb-dump'
            ),
            'mariadb-client': os.environ.get(
                'CDEADMIN_MARIADB_CLIENT_BINARY', 'mariadb'
            ),
            'mariadb-upgrade': os.environ.get(
                'CDEADMIN_MARIADB_UPGRADE_BINARY', 'mariadb-upgrade'
            ),
        })

    def open_session(self, request):
        connection = super().open_session(request)
        self._session_requests[id(connection)] = copy.deepcopy(dict(request))
        return connection

    def _active_token(self, connection):
        with self._stream_lock:
            return next((
                token for token in self._stream_tokens
                if token.connection is connection and not (
                    token.finished.is_set()
                )
            ), None)

    @staticmethod
    def _execution_error(exc):
        native_identity = []
        for attribute in ('errno', 'sqlstate'):
            value = getattr(exc, attribute, None)
            if isinstance(value, (int, str)) and str(value).strip():
                native_identity.append(f'{attribute}={value}')
        detail = '; ' + ', '.join(native_identity) if native_identity else ''
        return RelationalClientError(
            f'relational execution failed ({type(exc).__name__}{detail})'
        )

    @staticmethod
    def _put_stream_page(token, rows, complete):
        page = {'rows': list(rows), 'complete': bool(complete)}
        while not token.cancel_requested:
            try:
                token.pages.put(page, timeout=0.1)
                token.ready.set()
                return True
            except queue.Full:
                continue
        return False

    def _run_stream(self, token, source, parameters):
        cursor = None
        try:
            cursor = token.connection.cursor(buffered=False)
            token.cursor = cursor
            if parameters:
                cursor.execute(source, parameters)
            else:
                cursor.execute(source)
            description = getattr(cursor, 'description', None) or ()
            token.columns = tuple({
                'name': str(column[0]),
                'native_type': None if len(column) < 2 else str(column[1]),
            } for column in description)
            try:
                rowcount = getattr(cursor, 'rowcount', None)
            except Exception:
                rowcount = None
            token.rowcount = rowcount if isinstance(rowcount, int) else None
            if not description:
                self._put_stream_page(token, (), True)
                return

            current = list(cursor.fetchmany(self._STREAM_PAGE_SIZE))
            if not current:
                self._put_stream_page(token, (), True)
                return
            while not token.cancel_requested:
                following = list(cursor.fetchmany(self._STREAM_PAGE_SIZE))
                if not self._put_stream_page(
                        token, current, not following):
                    break
                if not following:
                    break
                current = following
        except Exception as exc:
            if not token.cancel_requested:
                token.error = self._execution_error(exc)
        finally:
            if cursor is not None:
                self._safe_close(cursor)
            token.finished.set()
            token.ready.set()

    def execute(self, handle, request):
        source = request.get('source')
        if not isinstance(source, str) or not source.strip():
            raise RelationalClientError('relational query source is required')
        parameters = request.get('parameters', ())
        if not isinstance(parameters, (dict, list, tuple)):
            raise RelationalClientError(
                'relational query parameters must be a mapping or sequence'
            )
        if self._active_token(handle) is not None:
            raise RelationalClientError(
                'MariaDB session already has an active streamed execution'
            )
        session_request = self._session_requests.get(id(handle))
        if session_request is None:
            raise RelationalClientError('MariaDB session route is unavailable')
        token = _MariaDBStreamToken(handle, copy.deepcopy(session_request))
        worker = threading.Thread(
            target=self._run_stream,
            args=(token, source, copy.deepcopy(parameters)),
            name=f'cdeadmin-mariadb-query-{token.stream_reference[:8]}',
            daemon=True,
        )
        token.worker = worker
        with self._stream_lock:
            self._stream_tokens.append(token)
        worker.start()
        return token

    def describe_result(self, token):
        with self._stream_lock:
            if not isinstance(token, _MariaDBStreamToken) or (
                    token not in self._stream_tokens):
                raise RelationalClientError(
                    'MariaDB streamed result token is invalid'
                )
        if token.closed:
            raise RelationalClientError(
                'MariaDB streamed result token is closed'
            )
        token.ready.wait(self._INITIAL_POLL_WAIT_SECONDS)
        try:
            page = token.pages.get_nowait()
        except queue.Empty:
            page = None
        if token.error is not None:
            token.closed = True
            raise token.error
        if page is None:
            complete = token.finished.is_set()
            rows = []
        else:
            complete = bool(page['complete'])
            rows = page['rows']
        if complete:
            token.closed = True
        return {
            'result_kind': (
                self.config.result_kind or self.config.profile.result_kind
            ),
            'schema': {'columns': list(token.columns)},
            'payload': {
                'rows': copy.deepcopy(rows),
                'rowcount': token.rowcount,
                'cancelled': token.cancelled,
                'streamed_by_provider': True,
            },
            'stream_reference': token.stream_reference,
            'complete': complete,
        }

    def cancel(self, token):
        with self._stream_lock:
            if not isinstance(token, _MariaDBStreamToken) or (
                    token not in self._stream_tokens):
                raise RelationalClientError(
                    'MariaDB streamed result token is invalid'
                )
        if token.closed or token.finished.is_set():
            return False
        thread_id = getattr(token.connection, 'thread_id', None)
        if isinstance(thread_id, bool) or not isinstance(thread_id, int) or (
                thread_id <= 0):
            raise RelationalClientError(
                'MariaDB connection has no native thread identity'
            )
        control = None
        cursor = None
        token.cancel_requested = True
        try:
            def control_arguments(route):
                values = dict(self.config.connect_arguments(route))
                for name in _MariaDBConnectorFacade._POOL_ARGUMENTS:
                    values.pop(name, None)
                return values

            control = self._invoke_connector(
                token.request, self.module.connect,
                connect_arguments=control_arguments,
            )
            cursor = control.cursor()
            cursor.execute(f'KILL QUERY {thread_id}')
        except Exception as exc:
            token.cancel_requested = False
            raise RelationalClientError(
                'MariaDB native query cancellation failed '
                f'({type(exc).__name__})'
            ) from None
        finally:
            if cursor is not None:
                self._safe_close(cursor)
            if control is not None:
                self._safe_close(control)
        token.cancelled = True
        token.ready.set()
        return True

    def control_transaction(self, handle, action):
        if self._active_token(handle) is not None:
            raise RelationalClientError(
                'MariaDB transaction control is unavailable while a '
                'streamed execution is active'
            )
        return super().control_transaction(handle, action)

    def close_session(self, handle):
        if self._active_token(handle) is not None:
            raise RelationalClientError(
                'MariaDB session close is unavailable while a streamed '
                'execution is active'
            )
        self._session_requests.pop(id(handle), None)
        return super().close_session(handle)

    def close(self):
        for token in tuple(self._stream_tokens):
            if not token.finished.is_set():
                try:
                    self.cancel(token)
                except RelationalClientError:
                    pass
            if token.worker is not None:
                token.worker.join(timeout=2)
            token.closed = True
        self._stream_tokens.clear()
        self._session_requests.clear()
        super().close()
        self._mariadb_connector.close()

    @staticmethod
    def _workspace_path(workspace, value):
        if not isinstance(workspace, str) or not workspace:
            raise RelationalClientError('MariaDB tool workspace is required')
        root = Path(workspace).resolve(strict=False)
        if not root.is_absolute() or root in {Path('/'), Path.home()}:
            raise RelationalClientError('MariaDB tool workspace is unsafe')
        if not isinstance(value, str) or not value.strip() or '\x00' in value:
            raise RelationalClientError('MariaDB dump path is invalid')
        candidate = Path(value.strip())
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError:
            raise RelationalClientError(
                'MariaDB dump path escapes the tool workspace'
            ) from None
        if candidate == root:
            raise RelationalClientError(
                'MariaDB dump path must be a child path'
            )
        return root, candidate

    @staticmethod
    def _metadata_path(path):
        return path.with_name(path.name + '.cdeadmin.json')

    @staticmethod
    def _file_sha256(path):
        digest = hashlib.sha256()
        size = 0
        with path.open('rb') as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _defaults_file(password):
        if not isinstance(password, str) or '\x00' in password:
            raise RelationalCredentialError(
                'MariaDB tool credential is invalid'
            )
        escaped = (
            password.replace('\\', '\\\\').replace('"', '\\"')
            .replace('\n', '\\n').replace('\r', '\\r')
        )
        return f'[client]\npassword="{escaped}"\n'.encode('utf-8')

    def _tool_grant(self, route, executable_id):
        timeout = route.get('tool_timeout', 3600)
        if isinstance(timeout, bool) or not isinstance(timeout, int):
            raise RelationalClientError('MariaDB tool timeout is invalid')
        try:
            return ProviderToolGrant(
                executable_id, route.get('tool_workspace'),
                route.get('host'), int(route.get('port', 3306)),
                timeout_seconds=timeout,
            )
        except (ProviderToolError, TypeError, ValueError) as exc:
            raise RelationalClientError(str(exc)) from None

    def _tool_secret(self, route, callback):
        references = dict(route.get('credential_references') or {})
        primary = route.get('credential_reference_id')
        if primary is not None:
            references.setdefault('database_password', primary)
        reference = references.get('database_password')
        if reference is None:
            return callback('')
        principal = route.get('principal_reference')
        if not isinstance(principal, str) or not principal.strip() or not (
                callable(self.config.secret_acquirer)):
            raise RelationalCredentialError(
                'MariaDB tool credential binding is unavailable'
            )
        try:
            lease = self.config.secret_acquirer(
                str(reference), principal.strip(), 'provider_tool',
                'database_password',
            )
        except Exception:
            raise RelationalCredentialError(
                'MariaDB tool credential is unavailable'
            ) from None
        with lease:
            return lease.use(
                lambda view: callback(bytes(view).decode('utf-8'))
            )

    @staticmethod
    def _connection_arguments(route, options):
        arguments = [
            '--user=' + str(route.get('user') or route.get('username') or ''),
            '--default-character-set=' + str(
                options.get('default_character_set', 'utf8mb4')
            ),
            '--max-allowed-packet=' + str(
                int(options.get('max_allowed_packet', 1073741824))
            ),
        ]
        if route.get('unix_socket'):
            arguments.extend([
                '--protocol=socket', '--socket=' + str(route['unix_socket']),
            ])
        else:
            arguments.extend([
                '--protocol=tcp', '--host=' + str(route['host']),
                '--port=' + str(route.get('port', 3306)),
            ])
        ssl_requested = route.get('ssl') is True or any(
            route.get(key) for key in (
                'ssl_key', 'ssl_cert', 'ssl_ca', 'ssl_capath', 'ssl_cipher',
                'ssl_crlpath', 'tls_version',
            )
        )
        arguments.append('--ssl' if ssl_requested else '--skip-ssl')
        for key, native in (
            ('ssl_key', 'ssl-key'), ('ssl_cert', 'ssl-cert'),
            ('ssl_ca', 'ssl-ca'), ('ssl_capath', 'ssl-capath'),
            ('ssl_cipher', 'ssl-cipher'), ('ssl_crlpath', 'ssl-crlpath'),
            ('tls_version', 'tls-version'),
        ):
            value = route.get(key)
            if value:
                arguments.append(f'--{native}={value}')
        if ssl_requested:
            arguments.append(
                '--ssl-verify-server-cert'
                if route.get('ssl_verify_cert') is True else
                '--skip-ssl-verify-server-cert'
            )
        if options.get('compress_connection') is True:
            arguments.append('--compress')
        return arguments

    @staticmethod
    def _inventory(connection, database):
        cursor = connection.cursor()
        try:
            cursor.execute('SELECT VERSION()')
            version = _version(cursor.fetchone())
            cursor.execute(
                'SELECT TABLE_NAME, TABLE_TYPE FROM '
                'information_schema.TABLES WHERE TABLE_SCHEMA = ? '
                'ORDER BY TABLE_NAME',
                (database,),
            )
            objects = [
                {'name': str(name), 'type': str(object_type)}
                for name, object_type in cursor.fetchall()
            ]
            return {'runtime_version': version, 'objects': objects}
        finally:
            cursor.close()

    def _invoke_tool(
            self, route, executable_id, arguments, password, *,
            input_path=None, accepted_return_codes=frozenset({0})):
        grant = self._tool_grant(route, executable_id)
        try:
            version_observation = self._tool_runner.run(grant, ['--version'])
            version_text = (
                str(version_observation.get('stdout') or '') + ' ' +
                str(version_observation.get('stderr') or '')
            )
            version_return_codes = (
                {0, 1} if executable_id == 'mariadb-upgrade' else {0}
            )
            if version_observation.get(
                    'return_code') not in version_return_codes or (
                    '12.2.2-MariaDB' not in version_text):
                raise RelationalClientError(
                    'MariaDB tool must be from the exact 12.2.2 release'
                )
            result = self._tool_runner.run(
                grant, arguments, input_path=input_path,
                secret_config=self._defaults_file(password),
                secret_argument='--defaults-file={path}',
                secret_suffix='.cnf', secret_argument_position=0,
                redact_values=(password,),
            )
        except ProviderToolError as exc:
            raise RelationalClientError(str(exc)) from None
        if result['return_code'] not in accepted_return_codes:
            raise RelationalClientError(
                f'{executable_id} failed; inspect the redacted provider '
                'observation'
            )
        return {
            **result, 'stdout': '', 'stderr': '',
            'tool_version_identity': '12.2.2-MariaDB',
        }

    @staticmethod
    def _upgrade_check_arguments(route):
        arguments = [
            '--check-if-upgrade-is-needed',
            '--user=' + str(route.get('user') or route.get('username') or ''),
        ]
        if route.get('unix_socket'):
            arguments.extend([
                '--protocol=socket', '--socket=' + str(route['unix_socket']),
            ])
        else:
            arguments.extend([
                '--protocol=tcp', '--host=' + str(route['host']),
                '--port=' + str(route.get('port', 3306)),
            ])
        ssl_requested = route.get('ssl') is True or any(
            route.get(key) for key in (
                'ssl_key', 'ssl_cert', 'ssl_ca', 'ssl_capath', 'ssl_cipher',
                'ssl_crlpath', 'tls_version',
            )
        )
        arguments.append('--ssl' if ssl_requested else '--skip-ssl')
        for key, native in (
            ('ssl_key', 'ssl-key'), ('ssl_cert', 'ssl-cert'),
            ('ssl_ca', 'ssl-ca'), ('ssl_capath', 'ssl-capath'),
            ('ssl_cipher', 'ssl-cipher'), ('ssl_crlpath', 'ssl-crlpath'),
            ('tls_version', 'tls-version'),
        ):
            value = route.get(key)
            if value:
                arguments.append(f'--{native}={value}')
        if ssl_requested:
            arguments.append(
                '--ssl-verify-server-cert'
                if route.get('ssl_verify_cert') is True else
                '--skip-ssl-verify-server-cert'
            )
        return arguments

    def _backup_arguments(self, route, options, path):
        arguments = self._connection_arguments(route, options)
        arguments.extend([
            '--quick', '--result-file=' + str(path),
        ])
        if options.get('include_schema', True) is False:
            arguments.append('--no-create-info')
        if options.get('include_data', True) is False:
            arguments.append('--no-data')
        if options.get('single_transaction', True):
            arguments.append('--single-transaction')
        elif options.get('lock_all_tables', False):
            arguments.append('--lock-all-tables')
        else:
            arguments.append('--skip-lock-tables')
        switches = (
            ('add_drop_database', '--add-drop-database', None),
            ('add_drop_table', '--add-drop-table', '--skip-add-drop-table'),
            ('routines', '--routines', None),
            ('events', '--events', None),
            ('triggers', '--triggers', '--skip-triggers'),
            ('dump_history', '--dump-history', None),
            ('hex_blob', '--hex-blob', None),
            ('order_by_primary', '--order-by-primary', None),
            ('extended_insert', '--extended-insert',
             '--skip-extended-insert'),
            ('complete_insert', '--complete-insert', None),
            ('comments', '--comments', '--skip-comments'),
            ('dump_date', '--dump-date', '--skip-dump-date'),
            ('tz_utc', '--tz-utc', '--skip-tz-utc'),
            ('flush_logs', '--flush-logs', None),
        )
        defaults = {
            'add_drop_database': True, 'add_drop_table': True,
            'routines': True, 'events': True, 'triggers': True,
            'dump_history': False, 'hex_blob': True,
            'order_by_primary': False, 'extended_insert': True,
            'complete_insert': False, 'comments': True, 'dump_date': True,
            'tz_utc': True, 'flush_logs': False,
        }
        for field_id, enabled, disabled in switches:
            if options.get(field_id, defaults[field_id]):
                arguments.append(enabled)
            elif disabled:
                arguments.append(disabled)
        if options.get('as_of'):
            arguments.append('--as-of=' + str(options['as_of']))
        position = options.get('replication_position', 'NONE')
        if position != 'NONE':
            arguments.append(
                '--master-data=1' if position == 'EXECUTABLE'
                else '--master-data=2'
            )
        if options.get('gtid'):
            arguments.append('--gtid')
        arguments.extend(['--databases', str(route['database'])])
        return arguments

    def _restore_arguments(self, route, options):
        arguments = self._connection_arguments(route, options)
        arguments.extend(['--batch', '--skip-auto-rehash'])
        arguments.append(
            '--abort-source-on-error'
            if options.get('abort_on_error', True) else '--force'
        )
        if options.get('binary_mode', True):
            arguments.append('--binary-mode')
        if options.get('show_warnings', True):
            arguments.append('--show-warnings')
        return arguments

    def run_database_operation(
            self, connection, route, operation_id, options):
        if operation_id == 'check_upgrade_required':
            if not isinstance(options, dict) or options:
                raise RelationalClientError(
                    'MariaDB upgrade check does not accept options'
                )

            def check_upgrade(password):
                observation = self._invoke_tool(
                    route, 'mariadb-upgrade',
                    self._upgrade_check_arguments(route), password,
                    accepted_return_codes=frozenset({0, 1}),
                )
                return {
                    **observation,
                    'operation_id': operation_id,
                    'upgrade_required': observation['return_code'] == 0,
                    'upgrade_check_completed': True,
                    'check_is_read_only': True,
                    'driver_observation_only': True,
                    'transaction_finality_interpreted_by_common_code': False,
                }

            return self._tool_secret(route, check_upgrade)
        if operation_id not in {'backup_logical', 'restore_logical'}:
            return super().run_database_operation(
                connection, route, operation_id, options
            )
        if not isinstance(options, dict):
            raise RelationalClientError('MariaDB tool options are invalid')
        _root, path = self._workspace_path(
            route.get('tool_workspace'), options['path']
        )
        metadata_path = self._metadata_path(path)
        database = str(route.get('database') or '')
        if operation_id == 'backup_logical':
            if path.exists() or metadata_path.exists():
                raise RelationalClientError(
                    'MariaDB backup artifact already exists'
                )
            owns_connection = connection is None
            inventory_connection = connection or self._connect({
                'route': route
            })
            try:
                before = self._inventory(inventory_connection, database)
            finally:
                if owns_connection:
                    self._forget_and_close(inventory_connection)

            def backup(password):
                try:
                    observation = self._invoke_tool(
                        route, 'mariadb-dump',
                        self._backup_arguments(route, options, path),
                        password,
                    )
                    if not path.is_file():
                        raise RelationalClientError(
                            'MariaDB backup produced no dump file'
                        )
                    digest, size = self._file_sha256(path)
                    if size == 0:
                        raise RelationalClientError(
                            'MariaDB backup produced an empty dump file'
                        )
                    metadata = {
                        'schema': 'cdeadmin.mariadb-logical-backup.v1',
                        'engine_id': 'mariadb',
                        'reference_version': MARIADB_PROFILE.exact_version,
                        'database': database,
                        'dump_file': path.name,
                        'dump_sha256': digest,
                        'dump_size': size,
                        'source_inventory': before,
                        'backup_options': {
                            key: value for key, value in options.items()
                            if key != 'path'
                        },
                    }
                    with metadata_path.open('x', encoding='utf-8') as stream:
                        json.dump(metadata, stream, indent=2, sort_keys=True)
                        stream.write('\n')
                    return {
                        **observation, 'operation_id': operation_id,
                        'artifact_path': str(path),
                        'metadata_path': str(metadata_path),
                        'artifact_sha256': digest,
                        'artifact_total_bytes': size,
                        'mariadb_dump_completed': True,
                        'driver_observation_only': True,
                        'transaction_finality_interpreted_by_common_code':
                            False,
                    }
                except Exception:
                    path.unlink(missing_ok=True)
                    metadata_path.unlink(missing_ok=True)
                    raise

            return self._tool_secret(route, backup)
        if not path.is_file() or not metadata_path.is_file():
            raise RelationalClientError(
                'MariaDB restore source lacks its CDEadmin metadata'
            )
        try:
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise RelationalClientError(
                'MariaDB restore metadata is invalid'
            ) from None
        metadata_mismatch = any((
            metadata.get('schema') != 'cdeadmin.mariadb-logical-backup.v1',
            metadata.get('engine_id') != 'mariadb',
            metadata.get('reference_version') != MARIADB_PROFILE.exact_version,
            metadata.get('database') != database,
            metadata.get('dump_file') != path.name,
        ))
        if metadata_mismatch:
            raise RelationalClientError(
                'MariaDB restore metadata does not match the target'
            )
        digest, size = self._file_sha256(path)
        if digest != metadata.get('dump_sha256') or size != metadata.get(
                'dump_size'):
            raise RelationalClientError(
                'MariaDB restore dump checksum does not match metadata'
            )
        if options.get('dry_run'):
            return {
                'operation_id': operation_id, 'dry_run': True,
                'artifact_path': str(path), 'artifact_sha256': digest,
                'artifact_total_bytes': size,
                'metadata_validated': True,
                'driver_observation_only': True,
                'transaction_finality_interpreted_by_common_code': False,
            }

        def restore(password):
            observation = self._invoke_tool(
                route, 'mariadb-client',
                self._restore_arguments(route, options), password,
                input_path=path,
            )
            verification = self._connect({'route': route})
            try:
                after = self._inventory(verification, database)
            finally:
                self._forget_and_close(verification)
            expected = metadata.get('source_inventory', {}).get('objects')
            if not isinstance(expected, list) or after['objects'] != expected:
                raise RelationalClientError(
                    'MariaDB restore object postcondition failed'
                )
            return {
                **observation, 'operation_id': operation_id,
                'artifact_path': str(path), 'artifact_sha256': digest,
                'artifact_total_bytes': size,
                'restore_postcondition': {
                    'runtime_version': after['runtime_version'],
                    'expected_objects': expected,
                    'observed_objects': after['objects'],
                    'object_postcondition_passed': True,
                },
                'mariadb_restore_completed': True,
                'driver_observation_only': True,
                'transaction_finality_interpreted_by_common_code': False,
            }

        return self._tool_secret(route, restore)


class MySQLDBAPIClient(RelationalDBAPIClient):
    """MySQL DB-API plus a constrained MySQL Shell administration port."""

    _OPTION_NAMES = {
        'backup_logical': {
            'consistent': 'consistent',
            'skip_consistency_checks': 'skipConsistencyChecks',
            'ddl_only': 'ddlOnly', 'data_only': 'dataOnly',
            'checksum': 'checksum', 'chunking': 'chunking',
            'bytes_per_chunk': 'bytesPerChunk', 'threads': 'threads',
            'max_rate': 'maxRate', 'show_progress': 'showProgress',
            'default_character_set': 'defaultCharacterSet',
            'compression': 'compression', 'tz_utc': 'tzUtc',
            'events': 'events', 'routines': 'routines',
            'libraries': 'libraries', 'triggers': 'triggers',
            'include_tables': 'includeTables',
            'exclude_tables': 'excludeTables',
            'include_events': 'includeEvents',
            'exclude_events': 'excludeEvents',
            'include_routines': 'includeRoutines',
            'exclude_routines': 'excludeRoutines',
            'include_libraries': 'includeLibraries',
            'exclude_libraries': 'excludeLibraries',
            'include_triggers': 'includeTriggers',
            'exclude_triggers': 'excludeTriggers',
            'partitions': 'partitions', 'where': 'where',
            'compatibility': 'compatibility',
            'target_version': 'targetVersion',
            'skip_upgrade_checks': 'skipUpgradeChecks',
            'dry_run': 'dryRun',
        },
        'restore_logical': {
            'analyze_tables': 'analyzeTables',
            'background_threads': 'backgroundThreads',
            'character_set': 'characterSet', 'checksum': 'checksum',
            'create_invisible_pks': 'createInvisiblePKs',
            'defer_table_indexes': 'deferTableIndexes',
            'disable_bulk_load': 'disableBulkLoad',
            'drop_existing_objects': 'dropExistingObjects',
            'dry_run': 'dryRun', 'exclude_events': 'excludeEvents',
            'exclude_libraries': 'excludeLibraries',
            'exclude_routines': 'excludeRoutines',
            'exclude_schemas': 'excludeSchemas',
            'exclude_tables': 'excludeTables',
            'exclude_triggers': 'excludeTriggers',
            'exclude_users': 'excludeUsers',
            'handle_grant_errors': 'handleGrantErrors',
            'ignore_existing_objects': 'ignoreExistingObjects',
            'ignore_version': 'ignoreVersion',
            'include_events': 'includeEvents',
            'include_libraries': 'includeLibraries',
            'include_routines': 'includeRoutines',
            'include_schemas': 'includeSchemas',
            'include_tables': 'includeTables',
            'include_triggers': 'includeTriggers',
            'include_users': 'includeUsers',
            'load_data': 'loadData', 'load_ddl': 'loadDdl',
            'load_indexes': 'loadIndexes', 'load_users': 'loadUsers',
            'max_bytes_per_transaction': 'maxBytesPerTransaction',
            'progress_file': 'progressFile',
            'reset_progress': 'resetProgress', 'schema': 'schema',
            'session_init_sql': 'sessionInitSql',
            'show_metadata': 'showMetadata',
            'show_progress': 'showProgress', 'skip_binlog': 'skipBinlog',
            'threads': 'threads', 'update_gtid_set': 'updateGtidSet',
            'wait_dump_timeout': 'waitDumpTimeout',
        },
    }

    def __init__(self, config, module=None, tool_runner=None):
        super().__init__(config, module)
        self._tool_runner = tool_runner or ProviderToolRunner({
            'mysql-shell': os.environ.get(
                'CDEADMIN_MYSQLSH_BINARY', 'mysqlsh'
            ),
        })

    @staticmethod
    def _workspace_path(workspace, value, label):
        if not isinstance(workspace, str) or not workspace:
            raise RelationalClientError('MySQL Shell workspace is required')
        root = Path(workspace).resolve(strict=False)
        if not root.is_absolute() or root in {Path('/'), Path.home()}:
            raise RelationalClientError('MySQL Shell workspace is unsafe')
        if not isinstance(value, str) or not value or '\x00' in value:
            raise RelationalClientError(f'{label} is invalid')
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError:
            raise RelationalClientError(
                f'{label} escapes the MySQL Shell workspace'
            ) from None
        if candidate == root:
            raise RelationalClientError(f'{label} must be a child path')
        return root, candidate

    def _tool_grant(self, route):
        timeout = route.get('tool_timeout', 3600)
        if isinstance(timeout, bool) or not isinstance(timeout, int):
            raise RelationalClientError('MySQL Shell timeout is invalid')
        try:
            return ProviderToolGrant(
                'mysql-shell', route.get('tool_workspace'),
                route.get('host'), int(route.get('port', 3306)),
                timeout_seconds=timeout,
            )
        except (ProviderToolError, TypeError, ValueError) as exc:
            raise RelationalClientError(str(exc)) from None

    def _tool_secrets(self, route, callback):
        references = dict(route.get('credential_references') or {})
        primary = route.get('credential_reference_id')
        if primary is not None:
            references.setdefault('database_password', primary)
        bindings = [
            (kind, references[kind]) for kind in (
                'database_password', 'database_password_2',
                'database_password_3',
            ) if kind in references
        ]
        if not bindings:
            return callback([])
        principal = route.get('principal_reference')
        if not isinstance(principal, str) or not principal.strip() or not (
                callable(self.config.secret_acquirer)):
            raise RelationalCredentialError(
                'MySQL Shell credential binding is unavailable'
            )

        def acquire(index, values):
            if index == len(bindings):
                return callback(values)
            kind, reference = bindings[index]
            try:
                lease = self.config.secret_acquirer(
                    str(reference), principal.strip(), 'provider_tool', kind
                )
            except Exception:
                raise RelationalCredentialError(
                    'MySQL Shell credentials are unavailable'
                ) from None
            with lease:
                return lease.use(lambda view: acquire(
                    index + 1,
                    [*values, bytes(view).decode('utf-8')],
                ))

        return acquire(0, [])

    @staticmethod
    def _shell_connection_arguments(route):
        arguments = [
            '--no-defaults', '--mysql', '--js',
            '--host=' + str(route['host']),
            '--port=' + str(route.get('port', 3306)),
            '--user=' + str(route.get('user') or route.get('username') or ''),
            '--passwords-from-stdin',
        ]
        if route.get('ssl_disabled'):
            arguments.append('--ssl-mode=DISABLED')
        elif route.get('ssl_verify_identity'):
            arguments.append('--ssl-mode=VERIFY_IDENTITY')
        elif route.get('ssl_verify_cert'):
            arguments.append('--ssl-mode=VERIFY_CA')
        else:
            arguments.append('--ssl-mode=REQUIRED')
        mappings = (
            ('ssl_ca', '--ssl-ca='), ('ssl_cert', '--ssl-cert='),
            ('ssl_key', '--ssl-key='), ('ssl_cipher', '--ssl-cipher='),
        )
        for key, prefix in mappings:
            if route.get(key):
                arguments.append(prefix + str(route[key]))
        versions = route.get('tls_versions')
        if isinstance(versions, list) and versions:
            arguments.append('--tls-version=' + ','.join(versions))
        suites = route.get('tls_ciphersuites')
        if isinstance(suites, list) and suites:
            arguments.append('--tls-ciphersuites=' + ':'.join(suites))
        arguments.append(
            '--compress=REQUIRED' if route.get('compress')
            else '--compress=DISABLED'
        )
        return arguments

    @classmethod
    def _shell_options(cls, operation, options):
        mapping = cls._OPTION_NAMES[operation]
        return {
            native: options[field]
            for field, native in mapping.items()
            if field in options and options[field] is not None and
            options[field] != ''
        }

    @staticmethod
    def _artifact_manifest(path):
        files = []
        total = 0
        for item in sorted(path.rglob('*')):
            if not item.is_file():
                continue
            digest = hashlib.sha256()
            size = 0
            with item.open('rb') as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
            total += size
            files.append({
                'path': str(item.relative_to(path)),
                'size': size, 'sha256': digest.hexdigest(),
            })
        return files, total

    @staticmethod
    def _selected_restore_schemas(path, options):
        try:
            metadata = json.loads((path / '@.json').read_text(
                encoding='utf-8'
            ))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise RelationalClientError(
                'MySQL Shell dump metadata is invalid'
            ) from None
        schemas = metadata.get('schemas')
        if not isinstance(schemas, list) or not schemas or not all(
                isinstance(item, str) and item for item in schemas):
            raise RelationalClientError(
                'MySQL Shell dump has no verifiable schema inventory'
            )
        includes = options.get('include_schemas')
        excludes = set(options.get('exclude_schemas') or [])
        if includes:
            schemas = [item for item in schemas if item in set(includes)]
        schemas = [item for item in schemas if item not in excludes]
        if options.get('schema'):
            if len(schemas) != 1:
                raise RelationalClientError(
                    'MySQL Shell target schema requires one selected schema'
                )
            schemas = [options['schema']]
        if not schemas:
            raise RelationalClientError(
                'MySQL Shell restore selected no schemas to verify'
            )
        return schemas

    def _verify_restore(self, route, path, options):
        schemas = self._selected_restore_schemas(path, options)
        verification = self._connect({'route': route})
        cursor = None
        try:
            cursor = verification.cursor()
            cursor.execute('SELECT VERSION()')
            version = _version(cursor.fetchone())
            cursor.execute(
                'SELECT SCHEMA_NAME FROM information_schema.SCHEMATA '
                'WHERE SCHEMA_NAME IN (' +
                ','.join(['%s'] * len(schemas)) + ')', tuple(schemas),
            )
            observed = sorted(str(row[0]) for row in cursor.fetchall())
            if observed != sorted(schemas):
                raise RelationalClientError(
                    'MySQL Shell restore schema postcondition failed'
                )
            return {
                'runtime_version': version,
                'expected_schemas': sorted(schemas),
                'observed_schemas': observed,
                'schema_postcondition_passed': True,
            }
        finally:
            if cursor is not None:
                cursor.close()
            self._forget_and_close(verification)

    def run_database_operation(
            self, connection, route, operation_id, options):
        if operation_id not in {'backup_logical', 'restore_logical'}:
            return super().run_database_operation(
                connection, route, operation_id, options
            )
        if not isinstance(options, dict):
            raise RelationalClientError('MySQL Shell options are invalid')
        _root, path = self._workspace_path(
            route.get('tool_workspace'), options['path'],
            'MySQL Shell dump path',
        )
        if operation_id == 'backup_logical':
            if path.exists():
                raise RelationalClientError(
                    'MySQL Shell backup path already exists'
                )
            database = str(route.get('database') or '')
            method = 'dumpSchemas'
            operands = [[database], str(path)]
        else:
            if not path.is_dir() or not (path / '@.json').is_file() or not (
                    path / '@.done.json').is_file():
                raise RelationalClientError(
                    'MySQL Shell restore source is not a completed dump'
                )
            method = 'loadDump'
            operands = [str(path)]
        effective_options = dict(options)
        progress_file = effective_options.get('progress_file')
        if progress_file:
            _progress_root, progress_path = self._workspace_path(
                route.get('tool_workspace'), progress_file,
                'MySQL Shell progress file',
            )
            effective_options['progress_file'] = str(progress_path)
        if operation_id == 'restore_logical':
            self._selected_restore_schemas(path, effective_options)
        shell_options = self._shell_options(operation_id, effective_options)
        source = 'util.%s(%s)' % (
            method,
            ', '.join(json.dumps(value, separators=(',', ':')) for value in (
                *operands, shell_options,
            )),
        )
        arguments = self._shell_connection_arguments(route)
        grant = self._tool_grant(route)

        def invoke(passwords):
            input_bytes = (
                ''.join(password + '\n' for password in passwords)
            ).encode('utf-8')
            try:
                observation = self._tool_runner.run(
                    grant, arguments, input_bytes=input_bytes,
                    secret_config=source.encode('utf-8'),
                    secret_argument='--file={path}', secret_suffix='.js',
                    redact_values=tuple(passwords),
                )
            except ProviderToolError as exc:
                raise RelationalClientError(str(exc)) from None
            if observation['return_code'] != 0:
                raise RelationalClientError(
                    'MySQL Shell operation failed; inspect the redacted '
                    'provider observation'
                )
            if effective_options.get('dry_run'):
                return {
                    **observation,
                    'stdout': '', 'stderr': '',
                    'operation_id': operation_id,
                    'artifact_path': str(path),
                    'mysql_shell_completed': True,
                    'dry_run': True,
                    'driver_observation_only': True,
                    'transaction_finality_interpreted_by_common_code': False,
                }
            files, total = self._artifact_manifest(path)
            if not files or not (path / '@.json').is_file() or not (
                    path / '@.done.json').is_file():
                raise RelationalClientError(
                    'MySQL Shell operation has no verifiable dump metadata'
                )
            return {
                **observation,
                'stdout': '', 'stderr': '',
                'operation_id': operation_id,
                'artifact_path': str(path),
                'artifact_file_count': len(files),
                'artifact_total_bytes': total,
                'artifact_manifest': files,
                'mysql_shell_completed': True,
                'driver_observation_only': True,
                'transaction_finality_interpreted_by_common_code': False,
            }

        enable_local_infile = (
            operation_id == 'restore_logical' and
            effective_options.get('enable_local_infile') is True
        )
        if not enable_local_infile:
            result = self._tool_secrets(route, invoke)
        else:
            owns_connection = connection is None
            settings_connection = connection or self._connect({
                'route': route
            })
            settings_cursor = None
            changed = False
            try:
                settings_cursor = settings_connection.cursor()
                settings_cursor.execute('SELECT @@GLOBAL.local_infile')
                prior = bool(settings_cursor.fetchone()[0])
                if not prior:
                    settings_cursor.execute('SET GLOBAL local_infile = ON')
                    changed = True
                result = self._tool_secrets(route, invoke)
                result['local_infile_temporarily_enabled'] = changed
                result['local_infile_prior_state'] = prior
            finally:
                try:
                    if changed:
                        if settings_cursor is None:
                            settings_cursor = settings_connection.cursor()
                        settings_cursor.execute(
                            'SET GLOBAL local_infile = OFF'
                        )
                finally:
                    if settings_cursor is not None:
                        settings_cursor.close()
                    if owns_connection:
                        self._forget_and_close(settings_connection)
        if operation_id == 'restore_logical' and not effective_options.get(
                'dry_run'):
            result['restore_postcondition'] = self._verify_restore(
                route, path, effective_options
            )
        return result


class MySQLPilotProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, MYSQL_PROFILE)


class MariaDBPilotProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, MARIADB_PROFILE)


def _route_arguments(route, profile):
    if profile is MYSQL_PROFILE:
        return mysql_route(route)
    allowed = {
        'host', 'port', 'user', 'database', 'unix_socket',
        'connection_timeout', 'connect_timeout', 'read_timeout',
        'write_timeout', 'local_infile', 'compress', 'init_command',
        'default_file', 'default_group', 'plugin_dir', 'reconnect',
        'ssl_key', 'ssl_cert', 'ssl_ca', 'ssl_capath', 'ssl_cipher',
        'ssl_crlpath', 'ssl_verify_cert', 'ssl', 'tls_version',
        'autocommit', 'pool_name', 'pool_size', 'pool_reset_connection',
        'pool_validation_interval',
    }
    result = {key: value for key, value in route.items() if key in allowed}
    if profile is MARIADB_PROFILE and 'connection_timeout' in result:
        result['connect_timeout'] = result.pop('connection_timeout')
    if result.get('pool_size') and not result.get('pool_name'):
        route_id = str(route.get('route_id') or 'unscoped')
        result['pool_name'] = 'cde_' + hashlib.sha256(
            route_id.encode('utf-8')
        ).hexdigest()[:24]
    return result


def _initialize_connection(connection, route):
    isolation = route.get('transaction_isolation')
    if isolation is None:
        return
    allowed = {
        'READ UNCOMMITTED', 'READ COMMITTED', 'REPEATABLE READ',
        'SERIALIZABLE',
    }
    if isolation not in allowed:
        raise RelationalClientError(
            'MySQL-family transaction isolation is invalid'
        )
    cursor = connection.cursor()
    try:
        cursor.execute(
            f'SET SESSION TRANSACTION ISOLATION LEVEL {isolation}'
        )
    finally:
        cursor.close()


def _version(row):
    value = str(row[0]).strip() if row else ''
    match = re.match(r'^(\d+\.\d+\.\d+)', value)
    if match is None:
        raise RelationalClientError(
            'MySQL-family profile version is unavailable'
        )
    return match.group(1)


def _resources(connection, request, profile=MYSQL_PROFILE):
    cursor = connection.cursor()
    try:
        generation = str(request.get('capability_generation') or 'current')
        route = request.get('route') if isinstance(request, dict) else None
        selected_database = route.get('database') if isinstance(
            route, dict
        ) else None
        selected_database = (
            selected_database.strip()
            if isinstance(selected_database, str) and
            selected_database.strip() else None
        )
        resources = {}

        def add(kind, path, name, native=None):
            path = [str(item) for item in path]
            name = str(name)
            resource_id = ':'.join([kind, *path, name])
            resource = resources.setdefault(resource_id, {
                'resource_id': resource_id,
                'resource_kind': kind,
                'display_name': name,
                'display_path': [*path, name],
                'authority_path': [*path, kind, name],
                'generation': generation,
            })
            if native:
                resource.setdefault('native', {}).update(native)

        def optional(source):
            try:
                cursor.execute(source)
                return cursor.fetchall()
            except Exception:
                return []

        def normalized(value):
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            if isinstance(value, (bytes, bytearray, memoryview)):
                return bytes(value).hex()
            return str(value)

        def optional_records(source):
            try:
                cursor.execute(source)
                names = tuple(str(item[0]).lower() for item in (
                    cursor.description or ()
                ))
                return [
                    {
                        name: normalized(row[index])
                        for index, name in enumerate(names)
                    }
                    for row in cursor.fetchall()
                ]
            except Exception:
                return []

        if profile is MYSQL_PROFILE:
            server_observation = optional(
                'SELECT VERSION(), @@hostname, @@port, @@server_uuid, '
                '@@version_comment, @@version_compile_machine, '
                '@@version_compile_os, @@lower_case_table_names, '
                '@@default_storage_engine, @@transaction_isolation, '
                'CURRENT_USER(), USER()'
            )
            server_names = (
                'version', 'hostname', 'port', 'server_uuid',
                'version_comment', 'version_compile_machine',
                'version_compile_os', 'lower_case_table_names',
                'default_storage_engine', 'transaction_isolation',
                'current_user', 'session_user',
            )
        else:
            server_observation = optional(
                'SELECT VERSION(), @@hostname, @@port, @@version_comment, '
                '@@version_compile_machine, @@version_compile_os, '
                '@@lower_case_table_names, @@default_storage_engine, '
                'CURRENT_USER(), USER()'
            )
            server_names = (
                'version', 'hostname', 'port', 'version_comment',
                'version_compile_machine', 'version_compile_os',
                'lower_case_table_names', 'default_storage_engine',
                'current_user', 'session_user',
            )
        add('server', [], profile.engine_name, (
            dict(zip(server_names, server_observation[0]))
            if server_observation else {
                'observation_error': 'server_properties_unavailable',
            }
        ))
        schema_source = (
            'SELECT SCHEMA_NAME, DEFAULT_CHARACTER_SET_NAME, '
            'DEFAULT_COLLATION_NAME, DEFAULT_ENCRYPTION '
            'FROM information_schema.SCHEMATA ORDER BY SCHEMA_NAME'
            if profile is MYSQL_PROFILE else
            'SELECT SCHEMA_NAME, DEFAULT_CHARACTER_SET_NAME, '
            'DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA '
            'ORDER BY SCHEMA_NAME'
        )
        for row in optional(schema_source):
            native = {
                'default_character_set': row[1],
                'default_collation': row[2],
            }
            if profile is MYSQL_PROFILE:
                native['default_encryption'] = row[3]
            add('database', [], row[0], native)
        cursor.execute(
            'SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE '
            'FROM information_schema.TABLES '
            "WHERE TABLE_SCHEMA NOT IN ('information_schema', 'mysql', "
            "'performance_schema', 'sys') "
            'ORDER BY TABLE_SCHEMA, TABLE_NAME'
        )
        for schema_name, object_name, object_type in cursor.fetchall():
            schema_name = str(schema_name)
            add('database', [], schema_name)
            object_type = str(object_type).upper()
            if object_type == 'SEQUENCE' and profile is MARIADB_PROFILE:
                kind = 'sequence'
            elif object_type == 'VIEW' and profile is MYSQL_PROFILE:
                escaped_schema = schema_name.replace('`', '``')
                escaped_name = str(object_name).replace('`', '``')
                definition = optional(
                    f'SHOW CREATE VIEW `{escaped_schema}`.`{escaped_name}`'
                )
                create_source = (
                    str(definition[0][1]) if definition else ''
                )
                kind = (
                    'materialized-view'
                    if re.search(r'\bMATERIALIZED\b', create_source, re.I)
                    else 'view'
                )
            else:
                kind = 'view' if 'VIEW' in object_type else 'table'
            add(kind, [schema_name], object_name, {
                'table_type': object_type,
            })
        queries = (
            ('column', 'SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, '
             'COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT '
             "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA NOT IN "
             "('information_schema', 'mysql', 'performance_schema', 'sys') "
             'ORDER BY 1, 2, '
             'ORDINAL_POSITION'),
            ('index', 'SELECT DISTINCT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, '
             'NON_UNIQUE, INDEX_TYPE FROM information_schema.STATISTICS '
             "WHERE TABLE_SCHEMA NOT IN ('information_schema', 'mysql', "
             "'performance_schema', 'sys') "
             'ORDER BY 1, 2, 3'),
            ('constraint', 'SELECT TABLE_SCHEMA, TABLE_NAME, '
             'CONSTRAINT_NAME, CONSTRAINT_TYPE FROM information_schema.'
             "TABLE_CONSTRAINTS WHERE TABLE_SCHEMA NOT IN "
             "('information_schema', 'mysql', 'performance_schema', 'sys') "
             'ORDER BY 1, 2, 3'),
            ('trigger', 'SELECT TRIGGER_SCHEMA, EVENT_OBJECT_TABLE, '
             'TRIGGER_NAME, ACTION_TIMING, EVENT_MANIPULATION '
             "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA NOT IN "
             "('information_schema', 'mysql', 'performance_schema', 'sys') "
             'ORDER BY 1, 2, 3'),
            ('partition', 'SELECT TABLE_SCHEMA, TABLE_NAME, PARTITION_NAME, '
             'PARTITION_METHOD, PARTITION_EXPRESSION FROM information_schema.'
             "PARTITIONS WHERE PARTITION_NAME IS NOT NULL AND TABLE_SCHEMA "
             "NOT IN ('information_schema', 'mysql', 'performance_schema', "
             "'sys') ORDER BY 1, 2, 3"),
        )
        for kind, source in queries:
            for row in optional(source):
                schema_name, parent, name, *detail = row
                add(kind, [schema_name, parent], name, {
                    'details': [
                        None if item is None else str(item)
                        for item in detail
                    ],
                })
        for schema_name, name, routine_type, data_type in optional(
            'SELECT ROUTINE_SCHEMA, ROUTINE_NAME, ROUTINE_TYPE, DATA_TYPE '
            "FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA NOT IN "
            "('information_schema', 'mysql', 'performance_schema', 'sys') "
            'ORDER BY 1, 2'
        ):
            kind = str(routine_type).lower()
            if kind in {'package', 'package body'} and (
                profile is MARIADB_PROFILE
            ):
                add('package', [schema_name], name, {
                    'routine_type': str(routine_type).upper(),
                    'data_type': data_type,
                })
                continue
            if kind not in {'procedure', 'function'}:
                continue
            add(kind, [schema_name], name, {'data_type': data_type})
        for schema_name, name, status, event_type in optional(
            'SELECT EVENT_SCHEMA, EVENT_NAME, STATUS, EVENT_TYPE '
            "FROM information_schema.EVENTS WHERE EVENT_SCHEMA NOT IN "
            "('information_schema', 'mysql', 'performance_schema', 'sys') "
            'ORDER BY 1, 2'
        ):
            add('event', [schema_name], name, {
                'status': status, 'event_type': event_type,
            })
        roles = set()
        if profile is MYSQL_PROFILE:
            # MySQL stores role membership with the role on the FROM side.
            # The TO side is the user (or role) receiving that role.
            for user, host in optional(
                'SELECT DISTINCT FROM_USER, FROM_HOST FROM mysql.role_edges '
                'ORDER BY 1, 2'
            ):
                roles.add((str(user), str(host)))
                add('role', [], f'{user}@{host}')
            accounts = optional(
                'SELECT User, Host, account_locked FROM mysql.user '
                'ORDER BY 1, 2'
            )
        else:
            accounts = optional(
                'SELECT User, Host, is_role, plugin, password_expired, '
                'default_role, max_questions, max_updates, max_connections, '
                'max_user_connections, max_statement_time, ssl_type, '
                'ssl_cipher, x509_issuer, x509_subject FROM mysql.user '
                'ORDER BY 1, 2'
            )
            locked_accounts = {}
            for user, host, privilege_json in optional(
                    'SELECT User, Host, Priv FROM mysql.global_priv'):
                try:
                    document = json.loads(str(privilege_json))
                except (TypeError, ValueError):
                    document = {}
                locked_accounts[(str(user), str(host))] = bool(
                    document.get('account_locked', False)
                )
            for row in accounts:
                user, host, is_role = row[:3]
                if str(is_role).upper() == 'Y':
                    roles.add((str(user), str(host)))
                    add('role', [], str(user), {
                        'host': str(host),
                        'default_role': row[5],
                    })
        for row in accounts:
            user, host, account_state = row[:3]
            if (str(user), str(host)) not in roles:
                if profile is MYSQL_PROFILE:
                    native = {'account_locked': str(account_state)}
                else:
                    native = {
                        'is_role': str(account_state),
                        'authentication_plugin': row[3],
                        'password_expired': row[4],
                        'default_role': row[5],
                        'max_queries_per_hour': int(row[6] or 0),
                        'max_updates_per_hour': int(row[7] or 0),
                        'max_connections_per_hour': int(row[8] or 0),
                        'max_user_connections': int(row[9] or 0),
                        'max_statement_time': str(row[10] or 0),
                        'tls_requirement': row[11],
                        'tls_cipher': row[12],
                        'x509_issuer': row[13],
                        'x509_subject': row[14],
                        'account_locked': locked_accounts.get(
                            (str(user), str(host)), False
                        ),
                        'authentication_secrets_returned': False,
                    }
                add('user', [], f'{user}@{host}', native)
        for grantee, privilege in optional(
            'SELECT GRANTEE, PRIVILEGE_TYPE FROM information_schema.'
            'USER_PRIVILEGES ORDER BY 1, 2'
        ):
            add('privilege', [grantee], privilege)
        tablespace_source = (
            'SELECT DISTINCT TABLESPACE_NAME, ENGINE FROM '
            'information_schema.FILES WHERE TABLESPACE_NAME IS NOT NULL '
            'ORDER BY 1'
            if profile is MYSQL_PROFILE else
            "SELECT NAME, 'InnoDB' FROM information_schema."
            'INNODB_SYS_TABLESPACES ORDER BY NAME'
        )
        for name, engine in optional(tablespace_source):
            add('tablespace', [], name, {'engine': engine})
        for name, status, plugin_type, library, license_name in optional(
            'SHOW PLUGINS'
        ):
            add('plugin', [], name, {
                'status': status,
                'plugin_type': plugin_type,
                'library': library,
                'license': license_name,
            })
        replication_source = (
            'SELECT CHANNEL_NAME, HOST, PORT FROM performance_schema.'
            'replication_connection_configuration ORDER BY CHANNEL_NAME'
            if profile is MYSQL_PROFILE else
            'SELECT Connection_name, Master_host, Master_user, Master_port, '
            'Connect_retry, Master_log_file, Read_master_log_pos, '
            'Relay_log_file, Relay_log_pos, Slave_IO_running, '
            'Slave_SQL_running, Last_errno, Last_error, '
            'Seconds_behind_master, Master_SSL_allowed, '
            'Master_SSL_verify_server_cert, Using_Gtid, Gtid_IO_Pos, '
            'SQL_Delay, SQL_Remaining_Delay, Slave_heartbeat_period '
            'FROM information_schema.SLAVE_STATUS ORDER BY Connection_name'
        )
        for row in optional(replication_source):
            if profile is MYSQL_PROFILE:
                name, host, port = row
                native = {'host': host, 'port': port}
            else:
                name, host, user, port, *detail = row
                native = {
                    'host': host, 'user': user, 'port': port,
                    **dict(zip((
                        'connect_retry', 'master_log_file',
                        'read_master_log_position', 'relay_log_file',
                        'relay_log_position', 'io_running', 'sql_running',
                        'last_error_number', 'last_error',
                        'seconds_behind_master', 'ssl_allowed',
                        'verify_server_certificate', 'using_gtid',
                        'gtid_io_position', 'sql_delay',
                        'sql_remaining_delay', 'heartbeat_period',
                    ), detail)),
                    'replication_password_returned': False,
                }
                native = {
                    key: (str(value) if value is not None and not isinstance(
                        value, (str, int, float, bool)) else value)
                    for key, value in native.items()
                }
            add('replication-channel', [], name, native)
        if profile is MYSQL_PROFILE:
            for name, resource_type, enabled in optional(
                'SELECT RESOURCE_GROUP_NAME, RESOURCE_GROUP_TYPE, '
                'RESOURCE_GROUP_ENABLED '
                'FROM information_schema.RESOURCE_GROUPS '
                'ORDER BY RESOURCE_GROUP_NAME'
            ):
                add('resource-group', [], name, {
                    'resource_type': resource_type, 'enabled': enabled,
                })
        else:
            for name, host, database in optional(
                'SELECT Server_name, Host, Db FROM mysql.servers '
                'ORDER BY Server_name'
            ):
                add('server-link', [], name, {
                    'host': host, 'database': database,
                })
            for record in optional_records(
                'SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE, INFO, '
                'TIME_MS, STAGE, MAX_STAGE, PROGRESS, MEMORY_USED, '
                'MAX_MEMORY_USED, EXAMINED_ROWS, SENT_ROWS, QUERY_ID, TID, '
                'TMP_SPACE_USED FROM information_schema.PROCESSLIST '
                'ORDER BY ID'
            ):
                add('session', ['Sessions'], record['id'], record)
            for record in optional_records(
                'SELECT VARIABLE_NAME, SESSION_VALUE, GLOBAL_VALUE, '
                'GLOBAL_VALUE_ORIGIN, DEFAULT_VALUE, VARIABLE_SCOPE, '
                'VARIABLE_TYPE, VARIABLE_COMMENT, NUMERIC_MIN_VALUE, '
                'NUMERIC_MAX_VALUE, NUMERIC_BLOCK_SIZE, ENUM_VALUE_LIST, '
                'READ_ONLY, COMMAND_LINE_ARGUMENT, GLOBAL_VALUE_PATH '
                'FROM information_schema.SYSTEM_VARIABLES '
                'ORDER BY VARIABLE_NAME'
            ):
                add(
                    'system-variable', ['Configuration'],
                    record['variable_name'], record,
                )
            for record in optional_records(
                'SELECT lock_id, lock_trx_id, lock_mode, lock_type, '
                'lock_table, lock_index, lock_space, lock_page, lock_rec, '
                'lock_data FROM information_schema.INNODB_LOCKS '
                'ORDER BY lock_id'
            ):
                add('lock', ['Locks'], record['lock_id'], record)
            for record in optional_records(
                'SELECT requesting_trx_id, requested_lock_id, '
                'blocking_trx_id, blocking_lock_id FROM '
                'information_schema.INNODB_LOCK_WAITS '
                'ORDER BY requesting_trx_id, blocking_trx_id'
            ):
                name = (
                    f"{record['requesting_trx_id']} -> "
                    f"{record['blocking_trx_id']}"
                )
                add('lock-wait', ['Lock waits'], name, record)
            for record in optional_records(
                'SELECT TABLE_SCHEMA, TABLE_NAME, ENGINE, TABLE_ROWS, '
                'DATA_LENGTH, INDEX_LENGTH, DATA_FREE, AUTO_INCREMENT, '
                'CREATE_TIME, UPDATE_TIME, CHECK_TIME, TABLE_COLLATION, '
                'CHECKSUM, CREATE_OPTIONS, TABLE_COMMENT, MAX_INDEX_LENGTH, '
                'TEMPORARY FROM information_schema.TABLES WHERE '
                "TABLE_SCHEMA NOT IN ('information_schema', 'mysql', "
                "'performance_schema', 'sys') ORDER BY TABLE_SCHEMA, "
                'TABLE_NAME'
            ):
                add(
                    'table-storage', [record['table_schema']],
                    record['table_name'], record,
                )
            binary_logs = optional_records('SHOW BINARY LOGS')
            for record in binary_logs:
                name = record.get('log_name') or record.get('file')
                if name:
                    add('binary-log', [], name, record)
                    escaped_name = str(name).replace("'", "''")
                    for event in optional_records(
                        "SHOW BINLOG EVENTS IN '" + escaped_name +
                        "' LIMIT 200"
                    ):
                        event_name = (
                            f"{event.get('pos')} / "
                            f"{event.get('event_type')}"
                        )
                        add(
                            'binary-log-event',
                            [name], event_name, event,
                        )
            for record in optional_records('SHOW BINLOG STATUS'):
                name = record.get('file') or 'Current binary log position'
                add('binary-log-status', [], name, record)
            log_values = {
                record['variable_name']: record['global_value']
                for record in optional_records(
                    'SELECT VARIABLE_NAME, GLOBAL_VALUE FROM '
                    'information_schema.SYSTEM_VARIABLES WHERE '
                    "VARIABLE_NAME IN ('GENERAL_LOG', 'GENERAL_LOG_FILE', "
                    "'SLOW_QUERY_LOG', 'SLOW_QUERY_LOG_FILE', "
                    "'LOG_OUTPUT') ORDER BY VARIABLE_NAME"
                )
            }
            add(
                'log-configuration', ['Logs'], 'Server log configuration',
                log_values,
            )
            for record in optional_records(
                'SELECT event_time, user_host, thread_id, server_id, '
                'command_type, argument FROM mysql.general_log '
                'ORDER BY event_time DESC LIMIT 200'
            ):
                name = f"{record['event_time']} / {record['thread_id']}"
                add('general-log-entry', ['Logs', 'General log'], name, record)
            for record in optional_records(
                'SELECT start_time, user_host, query_time, lock_time, '
                'rows_sent, rows_examined, db, last_insert_id, insert_id, '
                'server_id, sql_text, thread_id, rows_affected FROM '
                'mysql.slow_log ORDER BY start_time DESC LIMIT 200'
            ):
                name = f"{record['start_time']} / {record['thread_id']}"
                add('slow-query', ['Logs', 'Slow query log'], name, record)
            tls_values = {
                record['variable_name']: record['global_value']
                for record in optional_records(
                    'SELECT VARIABLE_NAME, GLOBAL_VALUE FROM '
                    'information_schema.SYSTEM_VARIABLES WHERE '
                    "VARIABLE_NAME IN ('HAVE_SSL', 'HAVE_OPENSSL', "
                    "'SSL_CA', 'SSL_CERT', 'SSL_KEY', 'TLS_VERSION') "
                    'ORDER BY VARIABLE_NAME'
                )
            }
            add(
                'tls-configuration', ['Security'],
                'TLS configuration', tls_values,
            )
        status_source = (
            'SELECT VARIABLE_NAME, VARIABLE_VALUE FROM '
            'performance_schema.global_status'
            if profile is MYSQL_PROFILE else
            'SELECT VARIABLE_NAME, VARIABLE_VALUE FROM '
            'information_schema.GLOBAL_STATUS'
        )
        values = {
            str(name).upper(): value
            for name, value in optional(status_source)
        }
        for metric in _metric_records(profile):
            native_name = metric['native_name']
            add('metric', ['Metrics'], native_name, {
                **metric,
                'value': (
                    None if native_name.upper() not in values else
                    str(values[native_name.upper()])
                ),
                'observation_error': (
                    None if native_name.upper() in values else
                    'metric_absent_from_exact_runtime'
                ),
            })
        if selected_database is None:
            return list(resources.values())
        database_scoped = {
            'database', 'table', 'view', 'materialized-view', 'column',
            'index', 'constraint', 'trigger', 'partition', 'procedure',
            'function', 'event', 'sequence', 'package',
            'table-storage',
        }
        return [
            resource for resource in resources.values()
            if resource['resource_kind'] not in database_scoped or (
                resource['display_name'] == selected_database
                if resource['resource_kind'] == 'database' else
                bool(resource['display_path']) and
                resource['display_path'][0] == selected_database
            )
        ]
    finally:
        cursor.close()


def _security(connection, request):
    cursor = connection.cursor()
    try:
        cursor.execute('SELECT CURRENT_USER(), USER()')
        current_user, session_user = cursor.fetchone()
        generation = str(request.get('capability_generation') or 'current')
        return {
            'resource_id': f'authorization:{current_user}',
            'display_name': str(current_user),
            'authority_path': ['authorization', str(current_user)],
            'generation': generation,
            'native': {
                'current_user': str(current_user),
                'session_user': str(session_user),
            },
        }
    finally:
        cursor.close()


def _create_client(profile, permissions, context):
    module_name = (
        'mysql.connector' if profile is MYSQL_PROFILE else 'mariadb'
    )
    config = RelationalClientConfig(
        profile=profile,
        module_name=module_name,
        version_query='SELECT VERSION()',
        version_parser=_version,
        connect_arguments=lambda route: _route_arguments(route, profile),
        metadata_reader=lambda connection, request: _resources(
            connection, request, profile
        ),
        security_reader=_security,
        credential_arguments=(
            {
                'database_password': 'password',
                'database_password_2': 'password2',
                'database_password_3': 'password3',
            }
            if profile is MYSQL_PROFILE else
            {'database_password': 'password'}
        ),
        secret_acquirer=permissions.acquire_secret,
        connection_initializer=_initialize_connection,
        administration=(
            MYSQL_ADMINISTRATION if profile is MYSQL_PROFILE
            else MARIADB_ADMINISTRATION
        ),
    )
    if profile is MARIADB_PROFILE:
        return MariaDBDBAPIClient(
            config, context.pool_namespace
        )
    return MySQLDBAPIClient(config)


def create_provider(context, permissions, client=None):
    providers = {
        MYSQL_PROFILE.profile_id: MySQLPilotProvider,
        MARIADB_PROFILE.profile_id: MariaDBPilotProvider,
    }
    provider_type = providers[context.profile_id]
    return provider_type(
        context, permissions, client or _create_client(
            MYSQL_PROFILE if provider_type is MySQLPilotProvider
            else MARIADB_PROFILE,
            permissions,
            context,
        )
    )
