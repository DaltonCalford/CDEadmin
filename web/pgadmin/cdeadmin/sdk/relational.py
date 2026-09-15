##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-local DB-API boundary for relational engine profiles.

The adapter observes only the profile and protocol advertised by the
endpoint.  It has no concept of which server implementation is behind that
protocol.  Transaction values are exposed as opaque driver observations; the
adapter never decides commit, rollback, retry, or recovery outcomes.
"""

from __future__ import annotations

import copy
import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .actual_engine import PilotProfile, PilotProviderError


class RelationalClientError(PilotProviderError):
    """A relational client dependency or DB-API operation failed safely."""


class RelationalDependencyError(RelationalClientError):
    """The selected optional DB-API dependency is unavailable."""


class RelationalCredentialError(RelationalClientError):
    """A provider credential could not be acquired for a connection."""

    credential_required_before_dispatch = True


def load_optional_module(module_name: str):
    """Import an approved optional dependency without leaking import detail."""
    try:
        return importlib.import_module(module_name)
    except (ImportError, ModuleNotFoundError) as exc:
        raise RelationalDependencyError(
            f'relational client dependency {module_name!r} is unavailable'
        ) from exc


def first_value(row: object) -> str:
    """Return a version query's first scalar value as text."""
    if isinstance(row, Mapping):
        values = tuple(row.values())
        value = values[0] if values else None
    elif isinstance(row, Sequence) and not isinstance(
        row, (str, bytes, bytearray)
    ):
        value = row[0] if row else None
    else:
        value = row
    if value is None or not str(value).strip():
        raise RelationalClientError('profile version query returned no value')
    return str(value).strip()


@dataclass(frozen=True)
class RelationalClientConfig:
    """Semantic and connection hooks owned by one relational provider."""

    profile: PilotProfile
    module_name: str
    version_query: str
    connect_arguments: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    metadata_reader: Callable[[object, Mapping[str, Any]], list[dict]]
    security_reader: Callable[
        [object, Mapping[str, Any]], Mapping[str, Any]
    ] | None = None
    version_parser: Callable[[object], str] = first_value
    connector_name: str = 'connect'
    connect_positional: Callable[
        [Mapping[str, Any]], Sequence[object]
    ] = lambda _route: ()
    result_kind: str | None = None
    execute_on_connection: bool = False
    extensions: Mapping[str, Any] = field(default_factory=dict)
    credential_argument: str | None = None
    credential_arguments: Mapping[str, str] = field(default_factory=dict)
    tool_credential_kinds: frozenset[str] = field(default_factory=frozenset)
    secret_acquirer: Callable[..., object] | None = field(
        default=None, repr=False, compare=False
    )
    administration: object | None = field(
        default=None, repr=False, compare=False
    )
    connection_initializer: Callable[
        [object, Mapping[str, Any]], None
    ] | None = field(default=None, repr=False, compare=False)
    session_initializer: Callable[
        [object, Mapping[str, Any]], None
    ] | None = field(default=None, repr=False, compare=False)
    transaction_controller: Callable[
        [object, str], None
    ] | None = field(default=None, repr=False, compare=False)
    database_initializer: Callable[
        [object, Mapping[str, Any]], Mapping[str, Any]
    ] | None = field(default=None, repr=False, compare=False)
    database_create_arguments: Callable[
        [Mapping[str, Any], str, Mapping[str, Any]], Mapping[str, Any]
    ] | None = field(default=None, repr=False, compare=False)
    database_dropper: Callable[
        [Mapping[str, Any], str], Mapping[str, Any]
    ] | None = field(default=None, repr=False, compare=False)
    database_operation_runner: Callable[
        [object, Mapping[str, Any], str, Mapping[str, Any]],
        Mapping[str, Any]
    ] | None = field(default=None, repr=False, compare=False)
    server_route: Callable[[Mapping[str, Any]], bool] | None = field(
        default=None, repr=False, compare=False
    )
    server_connector_name: str | None = None
    server_connect_arguments: Callable[
        [Mapping[str, Any]], Mapping[str, Any]
    ] | None = field(default=None, repr=False, compare=False)
    server_connect_positional: Callable[
        [Mapping[str, Any]], Sequence[object]
    ] | None = field(default=None, repr=False, compare=False)
    server_identity_reader: Callable[
        [object, Mapping[str, Any]], Mapping[str, Any]
    ] | None = field(default=None, repr=False, compare=False)
    server_metadata_reader: Callable[
        [object, Mapping[str, Any]], list[dict]
    ] | None = field(default=None, repr=False, compare=False)
    server_operation_runner: Callable[
        [object, str, str, Mapping[str, Any]], Mapping[str, Any]
    ] | None = field(default=None, repr=False, compare=False)
    query_parameter_normalizer: Callable[[object], object] | None = field(
        default=None, repr=False, compare=False)
    query_value_normalizer: Callable[[object], object] | None = field(
        default=None, repr=False, compare=False)
    query_columns_reader: Callable[[object], list[dict]] | None = field(
        default=None, repr=False, compare=False)
    session_rollback_needed: Callable[[object], bool] | None = field(
        default=None, repr=False, compare=False)
    transaction_observer: Callable[[object], Mapping[str, Any]] | None = field(
        default=None, repr=False, compare=False)
    session_releaser: Callable[[object], None] | None = field(
        default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.query_columns_reader is not None and not callable(
                self.query_columns_reader):
            raise RelationalClientError(
                'query_columns_reader must be callable')
        if self.query_value_normalizer is not None and not callable(
                self.query_value_normalizer):
            raise RelationalClientError(
                'query_value_normalizer must be callable')
        if self.session_releaser is not None and not callable(
                self.session_releaser):
            raise RelationalClientError('session_releaser must be callable')
        if self.transaction_observer is not None and not callable(
                self.transaction_observer):
            raise RelationalClientError(
                'transaction_observer must be callable')
        if self.session_rollback_needed is not None and not callable(
                self.session_rollback_needed):
            raise RelationalClientError(
                'session_rollback_needed must be callable')
        if self.query_parameter_normalizer is not None and not callable(
                self.query_parameter_normalizer):
            raise RelationalClientError(
                'query_parameter_normalizer must be callable')
        if not isinstance(self.profile, PilotProfile):
            raise RelationalClientError('relational profile is required')
        for name in ('module_name', 'version_query', 'connector_name'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise RelationalClientError(f'{name} must not be empty')
        for name in (
            'connect_arguments', 'connect_positional', 'metadata_reader',
            'version_parser',
        ):
            if not callable(getattr(self, name)):
                raise RelationalClientError(f'{name} must be callable')
        if self.security_reader is not None and not callable(
            self.security_reader
        ):
            raise RelationalClientError('security_reader must be callable')
        if self.result_kind is not None and (
            self.result_kind != self.profile.result_kind
        ):
            raise RelationalClientError(
                'client result kind differs from provider profile'
            )
        if not isinstance(self.execute_on_connection, bool):
            raise RelationalClientError(
                'execute_on_connection must be true or false'
            )
        if self.credential_argument is not None and (
            not isinstance(self.credential_argument, str) or
            not self.credential_argument.strip()
        ):
            raise RelationalClientError(
                'credential_argument must be a non-empty string'
            )
        if not isinstance(self.credential_arguments, Mapping) or not all(
            isinstance(kind, str) and kind.strip() and
            isinstance(argument, str) and argument.strip()
            for kind, argument in self.credential_arguments.items()
        ):
            raise RelationalClientError(
                'credential_arguments must map kinds to connector arguments'
            )
        if not isinstance(self.tool_credential_kinds, frozenset) or any(
                not isinstance(kind, str) or not kind.strip()
                for kind in self.tool_credential_kinds):
            raise RelationalClientError(
                'tool_credential_kinds must be a set of secret kinds'
            )
        if set(self.credential_arguments).intersection(
                self.tool_credential_kinds):
            raise RelationalClientError(
                'connector and provider-tool credentials must be distinct'
            )
        if self.secret_acquirer is not None and not callable(
            self.secret_acquirer
        ):
            raise RelationalClientError('secret_acquirer must be callable')
        if self.administration is not None:
            for name in (
                'supports', 'validate', 'plan', 'apply', 'read_rows',
            ):
                if not callable(getattr(self.administration, name, None)):
                    raise RelationalClientError(
                        f'administration adapter requires {name}'
                    )
        if self.connection_initializer is not None and not callable(
            self.connection_initializer
        ):
            raise RelationalClientError(
                'connection_initializer must be callable'
            )
        if self.session_initializer is not None and not callable(
            self.session_initializer
        ):
            raise RelationalClientError(
                'session_initializer must be callable'
            )
        if self.transaction_controller is not None and not callable(
            self.transaction_controller
        ):
            raise RelationalClientError(
                'transaction_controller must be callable'
            )
        if self.database_initializer is not None and not callable(
            self.database_initializer
        ):
            raise RelationalClientError(
                'database_initializer must be callable'
            )
        if self.database_create_arguments is not None and not callable(
            self.database_create_arguments
        ):
            raise RelationalClientError(
                'database_create_arguments must be callable'
            )
        if self.database_dropper is not None and not callable(
            self.database_dropper
        ):
            raise RelationalClientError('database_dropper must be callable')
        if self.database_operation_runner is not None and not callable(
                self.database_operation_runner):
            raise RelationalClientError(
                'database_operation_runner must be callable'
            )
        server_values = (
            self.server_route, self.server_connector_name,
            self.server_connect_arguments, self.server_identity_reader,
            self.server_metadata_reader,
        )
        if any(value is not None for value in server_values):
            if not all(value is not None for value in server_values):
                raise RelationalClientError(
                    'server-level relational hooks must be complete'
                )
            if not isinstance(self.server_connector_name, str) or not (
                self.server_connector_name.strip()
            ):
                raise RelationalClientError(
                    'server_connector_name must not be empty'
                )
            for name in (
                'server_route', 'server_connect_arguments',
                'server_identity_reader', 'server_metadata_reader',
            ):
                if not callable(getattr(self, name)):
                    raise RelationalClientError(
                        f'{name} must be callable'
                    )
        if self.server_connect_positional is not None and not callable(
            self.server_connect_positional
        ):
            raise RelationalClientError(
                'server_connect_positional must be callable'
            )
        if self.server_operation_runner is not None and not callable(
            self.server_operation_runner
        ):
            raise RelationalClientError(
                'server_operation_runner must be callable'
            )


@dataclass
class _ResultToken:
    cursor: object
    connection: object
    columns: tuple[dict[str, Any], ...]
    rows: list[object]
    rowcount: int | None
    closed: bool = False
    cancelled: bool = False


class RelationalDBAPIClient:
    """Synchronous DB-API client port for ``ActualEnginePilotProvider``."""

    transaction_actions = ('commit', 'rollback')

    def __init__(self, config: RelationalClientConfig, module=None):
        self.config = config
        self.module = module or load_optional_module(config.module_name)
        connector = getattr(self.module, config.connector_name, None)
        if not callable(connector):
            raise RelationalDependencyError(
                'relational client dependency has no approved connector'
            )
        self._connector = connector
        self._server_connector = None
        if config.server_connector_name is not None:
            self._server_connector = getattr(
                self.module, config.server_connector_name, None
            )
            if not callable(self._server_connector):
                raise RelationalDependencyError(
                    'relational client dependency has no approved '
                    'server connector'
                )
        self._connections: list[object] = []
        self._connection_databases: dict[int, object] = {}
        self._tokens: list[_ResultToken] = []

    @staticmethod
    def _route(request: Mapping[str, Any]) -> dict[str, Any]:
        route = request.get('route')
        if not isinstance(route, Mapping) or not route:
            raise RelationalClientError(
                'relational request requires a non-empty route'
            )
        return copy.deepcopy(dict(route))

    def _connect(self, request):
        # Keep the provider route for post-connect session initialization.
        # ``_invoke_connector`` intentionally creates and filters its own
        # copy before handing options to the driver, so it cannot return the
        # session-only values (for example transaction isolation) needed by
        # the initializer.
        route = self._route(request)
        connection = self._invoke_connector(request, self._connector)
        self._connections.append(connection)
        self._connection_databases[id(connection)] = route.get('database')
        if self.config.connection_initializer is not None:
            try:
                self.config.connection_initializer(connection, route)
            except RelationalClientError:
                self._forget_and_close(connection)
                raise
            except Exception as exc:
                self._forget_and_close(connection)
                raise RelationalClientError(
                    f'{self.config.profile.engine_name} session '
                    f'initialization failed ({type(exc).__name__})'
                ) from None
        return connection

    def _uses_server_scope(self, request):
        if self.config.server_route is None:
            return False
        return bool(self.config.server_route(self._route(request)))

    def _connect_server(self, request):
        if self._server_connector is None:
            raise RelationalClientError(
                'server-level connection is unavailable'
            )
        connection = self._invoke_connector(
            request, self._server_connector,
            connect_arguments=self.config.server_connect_arguments,
            connect_positional=(
                self.config.server_connect_positional or (lambda _route: ())
            ),
        )
        self._connections.append(connection)
        self._connection_databases[id(connection)] = None
        return connection

    def _invoke_connector(
        self, request, connector, overrides=None, *,
        connect_arguments=None, connect_positional=None,
    ):
        route = self._route(request)
        reference_id = route.pop('credential_reference_id', None)
        principal = route.pop('principal_reference', None)
        primary_kind = route.pop('credential_kind', 'database_password')
        references = route.pop('credential_references', {})
        route.pop('credential_kinds', None)
        if not isinstance(references, Mapping):
            raise RelationalClientError(
                'credential references must be an object'
            )
        references = dict(references)
        if reference_id is not None:
            references.setdefault(primary_kind, reference_id)
        positional = connect_positional or self.config.connect_positional
        arguments = connect_arguments or self.config.connect_arguments
        args = tuple(positional(route))
        kwargs = dict(arguments(route))
        kwargs.update(dict(overrides or {}))
        try:
            if not references:
                connection = connector(*args, **kwargs)
            else:
                if not all(
                    isinstance(value, str) and value.strip()
                    for value in references.values()
                ):
                    raise RelationalClientError(
                        'credential reference must be a non-empty string'
                    )
                if not isinstance(principal, str) or not principal.strip():
                    raise RelationalClientError(
                        'credential reference requires a principal reference'
                    )
                arguments = dict(self.config.credential_arguments)
                if self.config.credential_argument is not None:
                    arguments.setdefault(
                        'database_password', self.config.credential_argument
                    )
                connector_references = {
                    kind: reference
                    for kind, reference in references.items()
                    if kind in arguments
                }
                unknown = sorted(
                    set(references) - set(arguments) -
                    set(self.config.tool_credential_kinds)
                )
                if unknown or (
                    connector_references and
                    not callable(self.config.secret_acquirer)
                ):
                    raise RelationalClientError(
                        'relational credential binding is unavailable'
                    )
                bindings = [
                    (kind, connector_references[kind], arguments[kind])
                    for kind in sorted(connector_references)
                ]

                def connect(index, options):
                    if index == len(bindings):
                        return connector(*args, **options)
                    kind, reference, argument = bindings[index]
                    try:
                        lease = self.config.secret_acquirer(
                            reference.strip(), principal.strip(), 'connect',
                            kind
                        )
                    except Exception:
                        raise RelationalCredentialError(
                            f'{self.config.profile.engine_name} endpoint '
                            'credentials are unavailable'
                        ) from None
                    with lease:
                        return lease.use(lambda view: connect(index + 1, {
                            **options,
                            argument: bytes(view).decode('utf-8'),
                        }))

                connection = connect(0, kwargs)
        except RelationalClientError:
            raise
        except Exception as exc:
            message = (
                f'{self.config.profile.engine_name} connection failed '
                f'({type(exc).__name__})'
            )
            codes = ()
            if self.config.profile.engine_id == 'firebird':
                from ..providers.firebird.error_diagnostics import status_codes
                codes = status_codes(exc)
                if codes:
                    message += '; Firebird status codes: ' + ', '.join(
                        str(code) for code in codes)
            error = RelationalClientError(message)
            error.gds_codes = codes
            raise error from None
        return connection

    def create_database(self, request, database, driver_operation):
        connector_arguments = {'database': database}
        if driver_operation == 'firebird-create-database':
            connector = getattr(self.module, 'create_database', None)
            if not callable(connector):
                raise RelationalDependencyError(
                    'Firebird driver has no create_database operation'
                )
            options = request.get('create_options') or {}
            if not isinstance(options, Mapping):
                raise RelationalClientError(
                    'Firebird database creation options are invalid'
                )
            if self.config.database_create_arguments is None:
                raise RelationalDependencyError(
                    'Firebird provider database creation configuration '
                    'is unavailable'
                )
            route = self._route(request)
            connector_arguments = dict(self.config.database_create_arguments(
                route, database, copy.deepcopy(dict(options))))
        elif driver_operation == 'embedded-create-database':
            connector = self._connector
        else:
            raise RelationalClientError(
                'database creation driver operation is unavailable'
            )
        options = request.get('create_options') or {}
        if not isinstance(options, Mapping):
            raise RelationalClientError(
                'database creation options are invalid'
            )
        if (
            driver_operation == 'embedded-create-database' and options and
            self.config.database_initializer is None and
            self.config.database_create_arguments is None
        ):
            raise RelationalClientError(
                'embedded database creation options are unavailable'
            )
        if (
            driver_operation == 'embedded-create-database' and
            self.config.database_create_arguments is not None
        ):
            route = self._route(request)
            connector_arguments = dict(
                self.config.database_create_arguments(
                    route, database, copy.deepcopy(dict(options))
                )
            )
            if connector_arguments.get('database') != database:
                raise RelationalClientError(
                    'embedded database creation arguments changed the '
                    'trusted database target'
                )
        connection = self._invoke_connector(
            request, connector, connector_arguments,
            connect_arguments=((lambda _route: {}) if
                               driver_operation == 'firebird-create-database'
                               else None),
        )
        initialization = {}
        try:
            if (
                driver_operation == 'embedded-create-database' and
                self.config.database_initializer is not None
            ):
                initialization = dict(self.config.database_initializer(
                    connection, copy.deepcopy(dict(options))
                ))
        finally:
            self._safe_close(connection)
        return {
            'driver_operation': driver_operation,
            'driver_returned': True,
            'initialization': initialization,
            'endpoint_database_target': {
                'database': database,
                'display_name': str(database).rsplit('/', 1)[-1],
            },
            'transaction_finality_interpreted_by_common_code': False,
        }

    def drop_database(self, request, database, driver_operation):
        """Drop a database through the exact provider-owned mechanism."""
        if driver_operation not in {
            'firebird-drop-database', 'embedded-drop-database',
        }:
            raise RelationalClientError(
                'database drop driver operation is unavailable'
            )
        route = self._route(request)
        if route.get('database') != database:
            raise RelationalClientError(
                'database drop target does not match the trusted route'
            )
        if driver_operation == 'embedded-drop-database':
            if self.config.database_dropper is None:
                raise RelationalDependencyError(
                    'embedded provider has no database file deleter'
                )
            if any(
                self._connection_databases.get(id(connection)) == database
                for connection in self._connections
            ):
                raise RelationalClientError(
                    'close all retained sessions for this database before '
                    'deleting the database file'
                )
            return dict(self.config.database_dropper(route, database))
        connection = self._connect(request)
        dropped = False
        try:
            operation = getattr(connection, 'drop_database', None)
            if not callable(operation):
                raise RelationalDependencyError(
                    'Firebird driver has no drop_database operation'
                )
            operation()
            dropped = True
            # Firebird's successful native drop releases its attachment.
            # Retaining this dead handle would block profile synchronization.
            self._forget_connection(connection)
        finally:
            if not dropped:
                self._safe_close(connection)
        return {
            'driver_operation': driver_operation,
            'driver_returned': True,
            'database': database,
            'transaction_finality_interpreted_by_common_code': False,
        }

    def _server_operation_database(self, database):
        """Provider hook for the native service target's spelling."""
        return database.strip()

    def run_server_operation(self, request, operation_id, database, options):
        """Run one provider-owned server service against an exact database.

        The common DB-API adapter owns credential leasing and handle cleanup
        only.  It neither translates operation names nor interprets service
        completion; those decisions belong to the provider callback.
        """
        runner = self.config.server_operation_runner
        if runner is None or self._server_connector is None:
            raise RelationalClientError(
                'provider server operation runner is unavailable'
            )
        if not isinstance(operation_id, str) or not operation_id:
            raise RelationalClientError('server operation ID is invalid')
        if not isinstance(database, str) or not database.strip():
            raise RelationalClientError(
                'server operation requires an exact database identifier'
            )
        if not isinstance(options, Mapping):
            raise RelationalClientError(
                'server operation options must be an object'
            )
        server = self._connect_server(request)
        result = None
        failure = None
        try:
            observation = runner(
                server, operation_id,
                self._server_operation_database(database),
                copy.deepcopy(dict(options)),
            )
            if not isinstance(observation, Mapping):
                raise RelationalClientError(
                    'provider server operation returned an invalid result'
                )
            result = copy.deepcopy(dict(observation))
            return result
        except RelationalClientError as exc:
            failure = exc
            raise
        except Exception as exc:
            failure = self._server_operation_error(exc)
            raise failure from None
        finally:
            self._finish_server_operation(server, result, failure)

    def _finish_server_operation(self, server, result, failure):
        """Provider hook separating the returned observation from cleanup."""
        self._forget_and_close(server)

    def _server_operation_error(self, error):
        """Allow provider-owned diagnostics without leaking native text."""
        return RelationalClientError(
            'provider server operation failed '
            f'({type(error).__name__})')

    def run_database_operation(
            self, connection, route, operation_id, options):
        """Run one provider-owned operation on an attached database."""
        runner = self.config.database_operation_runner
        if runner is None:
            raise RelationalClientError(
                'provider database operation runner is unavailable'
            )
        try:
            result = runner(
                connection, copy.deepcopy(dict(route)), operation_id,
                copy.deepcopy(dict(options)),
            )
            if not isinstance(result, Mapping):
                raise RelationalClientError(
                    'provider database operation returned an invalid result'
                )
            return copy.deepcopy(dict(result))
        except RelationalClientError:
            raise
        except Exception as exc:
            raise RelationalClientError(
                'provider database operation failed '
                f'({type(exc).__name__})'
            ) from None

    def runtime_identity(self, request, handle=None):
        if handle is None and self._uses_server_scope(request):
            connection = self._connect_server(request)
            try:
                value = self.config.server_identity_reader(
                    connection, request
                )
                if not isinstance(value, Mapping):
                    raise RelationalClientError(
                        'server identity reader must return a mapping'
                    )
                return copy.deepcopy(dict(value))
            except RelationalClientError:
                raise
            except Exception as exc:
                raise RelationalClientError(
                    'relational server verification failed '
                    f'({type(exc).__name__})'
                ) from None
            finally:
                self._forget_and_close(connection)
        temporary = handle is None
        connection = handle or self._connect(request)
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(self.config.version_query)
            row = cursor.fetchone()
            version = self.config.version_parser(row)
            return {
                'engine_id': self.config.profile.engine_id,
                'version': version,
                'build_id': f'{self.config.profile.engine_id}:{version}',
                'protocol_id': self.config.profile.protocol_id,
            }
        except RelationalClientError:
            raise
        except Exception as exc:
            raise RelationalClientError(
                'relational profile verification failed '
                f'({type(exc).__name__})'
            ) from None
        finally:
            if cursor is not None:
                self._safe_close(cursor)
            if temporary:
                self._forget_and_close(connection)

    def open_session(self, request):
        if self._uses_server_scope(request):
            raise RelationalClientError(
                'select or create a database before opening a query session'
            )
        connection = self._connect(request)
        if self.config.session_initializer is not None:
            try:
                self.config.session_initializer(
                    connection, self._route(request)
                )
            except RelationalClientError:
                self._forget_and_close(connection)
                raise
            except Exception as exc:
                self._forget_and_close(connection)
                raise RelationalClientError(
                    'relational retained-session initialization failed '
                    f'({type(exc).__name__})'
                ) from None
        return connection

    def describe_transaction(self, handle):
        if self.config.transaction_observer is not None:
            return dict(self.config.transaction_observer(handle))
        observations = {}
        for name in ('autocommit', 'in_transaction', 'isolation_level'):
            try:
                value = getattr(handle, name)
            except Exception:
                continue
            if callable(value):
                continue
            if isinstance(value, (bool, int, float, str)) or value is None:
                observations[name] = value
        observations['driver_observation_only'] = True
        observations['finality_interpreted_by_common_code'] = False
        return observations

    def control_transaction(self, handle, action):
        """Delegate transaction control to the retained DB-API connection."""
        if action not in self.transaction_actions:
            raise RelationalClientError(
                'relational transaction action is unavailable'
            )
        callback = getattr(handle, action, None)
        controller = self.config.transaction_controller
        if controller is None and not callable(callback):
            raise RelationalClientError(
                'relational driver has no transaction controller'
            )
        try:
            if controller is not None:
                controller(handle, action)
            else:
                callback()
        except Exception as exc:
            raise RelationalClientError(
                'relational transaction action outcome is provider-owned '
                f'({type(exc).__name__})'
            ) from None

    def close_session(self, handle):
        """Roll back and release one retained DB-API connection."""
        rollback = getattr(handle, 'rollback', None)
        rollback_requested = False
        needed = self.config.session_rollback_needed
        if callable(rollback) and (needed is None or needed(handle)):
            rollback()
            rollback_requested = True
        if self.config.session_releaser is not None:
            self.config.session_releaser(handle)
            self._forget_connection(handle)
        else:
            self._forget_and_close(handle)
        return {
            'rollback_requested': rollback_requested,
            'connection_released': True,
            'driver_observation_only': True,
            'finality_interpreted_by_common_code': False,
        }

    def list_resources(self, request):
        if self._uses_server_scope(request):
            connection = self._connect_server(request)
            try:
                rows = self.config.server_metadata_reader(
                    connection, request
                )
                if not isinstance(rows, list):
                    raise RelationalClientError(
                        'server metadata reader must return a list'
                    )
                return copy.deepcopy(rows)
            finally:
                self._forget_and_close(connection)
        connection = request.get('_provider_session_handle')
        owns_connection = connection is None
        if owns_connection:
            connection = self._connect(request)
        try:
            rows = self.config.metadata_reader(connection, request)
            if not isinstance(rows, list):
                raise RelationalClientError(
                    'relational metadata reader must return a list'
                )
            return copy.deepcopy(rows)
        finally:
            if owns_connection:
                self._forget_and_close(connection)

    def inspect_resource(self, request):
        resource_id = request.get('resource_id')
        for resource in self.list_resources(request):
            if resource.get('resource_id') == resource_id:
                return resource
        raise RelationalClientError('relational resource is unavailable')

    def supports_admin_operation(self, resource_kind, operation_id):
        adapter = self.config.administration
        return bool(
            adapter is not None and
            adapter.supports(resource_kind, operation_id)
        )

    def admin_operation_requires_dialect(self, resource_kind, operation_id):
        """Return whether the operation emits or executes native SQL."""
        adapter = self.config.administration
        return bool(
            adapter is not None and
            adapter.requires_dialect(resource_kind, operation_id)
        )

    def admin_dialect_task_ids(self):
        """Expose exact generated-task obligations to the contract gate."""
        adapter = self.config.administration
        return () if adapter is None else adapter.dialect_task_ids()

    def visual_admin_catalog(self, catalog):
        adapter = self._administration()
        return adapter.catalog(catalog)

    def validate_admin_operation(self, request):
        adapter = self._administration()
        return adapter.validate(request)

    def plan_admin_operation(self, request):
        adapter = self._administration()
        return adapter.plan(request)

    def apply_admin_operation(self, request):
        adapter = self._administration()
        return adapter.apply(
            self, request,
            connection=request.get('_provider_session_handle'),
        )

    def read_admin_rows(self, request):
        adapter = self._administration()
        return adapter.read_rows(
            self, request,
            connection=request.get('_provider_session_handle'),
        )

    def cancel_admin_cursor(self, request):
        adapter = self._administration()
        return adapter.cancel_rows(request)

    def inspect_admin_operation(self, request):
        adapter = self._administration()
        callback = getattr(adapter, 'inspect_operation', None)
        if not callable(callback):
            raise RelationalClientError(
                'provider operation observation is unavailable'
            )
        return callback(self, request)

    def cancel_admin_operation(self, request):
        adapter = self._administration()
        callback = getattr(adapter, 'cancel_operation', None)
        if not callable(callback):
            raise RelationalClientError(
                'provider operation cancellation is unavailable'
            )
        return callback(self, request)

    def validate_admin_post_state(self, request):
        adapter = self._administration()
        callback = getattr(adapter, 'validate_operation_post_state', None)
        if not callable(callback):
            return {
                'confirmed': False,
                'reason': 'provider_post_state_validator_unavailable',
            }
        return callback(self, request)

    def _administration(self):
        adapter = self.config.administration
        if adapter is None:
            raise RelationalClientError(
                'relational administration adapter is unavailable'
            )
        return adapter

    def describe_security(self, request):
        if self.config.security_reader is None:
            raise RelationalClientError(
                'relational security reader is unavailable'
            )
        connection = self._connect(request)
        try:
            value = self.config.security_reader(connection, request)
            if not isinstance(value, Mapping):
                raise RelationalClientError(
                    'relational security reader must return a mapping'
                )
            return copy.deepcopy(dict(value))
        finally:
            self._forget_and_close(connection)

    def _fetch_query_rows(self, cursor, request):
        """Provider override point; ordinary DB-API behavior is unchanged."""
        return list(cursor.fetchall())

    def _close_failed_query_cursor(self, handle, cursor, error):
        """Keep default cleanup policy overridable by native session owners."""
        self._safe_close(cursor)

    def execute(self, handle, request):
        source = request.get('source')
        if not isinstance(source, str) or not source.strip():
            raise RelationalClientError('relational query source is required')
        parameters = request.get('parameters', ())
        if self.config.query_parameter_normalizer is not None:
            parameters = self.config.query_parameter_normalizer(parameters)
        if not isinstance(parameters, (Mapping, list, tuple)):
            raise RelationalClientError(
                'relational query parameters must be a mapping or sequence'
            )
        cursor = None
        try:
            cursor = (
                handle if self.config.execute_on_connection
                else handle.cursor()
            )
            if parameters:
                cursor.execute(source, parameters)
            else:
                cursor.execute(source)
            description = getattr(cursor, 'description', None) or ()
            columns = tuple(
                {
                    'name': str(column[0]),
                    'native_type': (
                        None if len(column) < 2 else str(column[1])
                    ),
                }
                for column in description
            )
            if self.config.query_columns_reader is not None:
                columns = tuple(self.config.query_columns_reader(cursor))
            rows = (self._fetch_query_rows(cursor, request)
                    if description else [])
            if self.config.query_value_normalizer is not None:
                rows = [tuple(self.config.query_value_normalizer(value)
                              for value in row) for row in rows]
            try:
                rowcount = getattr(cursor, 'rowcount', None)
            except Exception:
                # DB-API row counts are optional. In particular, Firebird's
                # driver can reject the records-info request after valid DDL.
                rowcount = None
            token = _ResultToken(
                cursor, handle, columns, rows,
                rowcount if isinstance(rowcount, int) else None,
            )
            self._tokens.append(token)
            return token
        except RelationalClientError as exc:
            if cursor is not None and cursor is not handle:
                self._close_failed_query_cursor(handle, cursor, exc)
            raise
        except Exception as exc:
            if cursor is not None and cursor is not handle:
                self._close_failed_query_cursor(handle, cursor, exc)
            native_identity = []
            for attribute in ('errno', 'sqlstate'):
                value = getattr(exc, attribute, None)
                if isinstance(value, (int, str)) and str(value).strip():
                    native_identity.append(f'{attribute}={value}')
            detail = (
                '; ' + ', '.join(native_identity)
                if native_identity else ''
            )
            codes = ()
            if self.config.profile.engine_id == 'firebird':
                from ..providers.firebird.error_diagnostics import status_codes
                codes = status_codes(exc)
                if codes:
                    detail += '; Firebird status codes: ' + ', '.join(
                        str(code) for code in codes)
            error = RelationalClientError(
                f'relational execution failed ({type(exc).__name__}'
                f'{detail})'
            )
            error.gds_codes = codes
            raise error from None

    def describe_result(self, token):
        if not isinstance(token, _ResultToken) or token not in self._tokens:
            raise RelationalClientError('relational result token is invalid')
        if not token.closed and token.cursor is not token.connection:
            self._safe_close(token.cursor)
        token.closed = True
        return {
            'result_kind': (
                self.config.result_kind or self.config.profile.result_kind
            ),
            'schema': {'columns': list(token.columns)},
            'payload': {
                'rows': copy.deepcopy(token.rows),
                'rowcount': token.rowcount,
                'cancelled': token.cancelled,
            },
            'stream_reference': None,
            'complete': True,
        }

    def cancel(self, token):
        if not isinstance(token, _ResultToken) or token not in self._tokens:
            raise RelationalClientError('relational result token is invalid')
        cancel = getattr(token.connection, 'cancel', None)
        if not callable(cancel):
            cancel = getattr(token.connection, 'interrupt', None)
        if callable(cancel):
            cancel()
            token.cancelled = True
            return True
        return False

    def close(self):
        for token in tuple(self._tokens):
            if not token.closed and token.cursor is not token.connection:
                self._safe_close(token.cursor)
            token.closed = True
        self._tokens.clear()
        for connection in tuple(self._connections):
            self._forget_and_close(connection)

    def complete(self, _request):
        return []

    @staticmethod
    def _safe_close(value):
        close = getattr(value, 'close', None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def _forget_connection(self, connection):
        self._connection_databases.pop(id(connection), None)
        try:
            self._connections.remove(connection)
        except ValueError:
            pass

    def _forget_and_close(self, connection):
        self._forget_connection(connection)
        self._safe_close(connection)
