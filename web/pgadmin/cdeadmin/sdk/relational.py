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
    extensions: Mapping[str, Any] = field(default_factory=dict)
    credential_argument: str | None = None
    secret_acquirer: Callable[..., object] | None = field(
        default=None, repr=False, compare=False
    )
    administration: object | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self):
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
        if self.credential_argument is not None and (
            not isinstance(self.credential_argument, str) or
            not self.credential_argument.strip()
        ):
            raise RelationalClientError(
                'credential_argument must be a non-empty string'
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

    def __init__(self, config: RelationalClientConfig, module=None):
        self.config = config
        self.module = module or load_optional_module(config.module_name)
        connector = getattr(self.module, config.connector_name, None)
        if not callable(connector):
            raise RelationalDependencyError(
                'relational client dependency has no approved connector'
            )
        self._connector = connector
        self._connections: list[object] = []
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
        connection = self._invoke_connector(request, self._connector)
        self._connections.append(connection)
        return connection

    def _invoke_connector(self, request, connector, overrides=None):
        route = self._route(request)
        reference_id = route.pop('credential_reference_id', None)
        principal = route.pop('principal_reference', None)
        args = tuple(self.config.connect_positional(route))
        kwargs = dict(self.config.connect_arguments(route))
        kwargs.update(dict(overrides or {}))
        try:
            if reference_id is None:
                connection = connector(*args, **kwargs)
            else:
                if not isinstance(reference_id, str) or not (
                    reference_id.strip()
                ):
                    raise RelationalClientError(
                        'credential reference must be a non-empty string'
                    )
                if not isinstance(principal, str) or not principal.strip():
                    raise RelationalClientError(
                        'credential reference requires a principal reference'
                    )
                if self.config.credential_argument is None or not callable(
                    self.config.secret_acquirer
                ):
                    raise RelationalClientError(
                        'relational credential binding is unavailable'
                    )
                lease = self.config.secret_acquirer(
                    reference_id.strip(), principal.strip(), 'connect',
                    'database_password',
                )
                with lease:
                    connection = lease.use(
                        lambda view: connector(
                            *args,
                            **{
                                **kwargs,
                                self.config.credential_argument: bytes(
                                    view
                                ).decode('utf-8'),
                            },
                        )
                    )
        except RelationalClientError:
            raise
        except Exception as exc:
            raise RelationalClientError(
                f'{self.config.profile.engine_name} connection failed '
                f'({type(exc).__name__})'
            ) from None
        return connection

    def create_database(self, request, database, driver_operation):
        if driver_operation == 'firebird-create-database':
            connector = getattr(self.module, 'create_database', None)
            if not callable(connector):
                raise RelationalDependencyError(
                    'Firebird driver has no create_database operation'
                )
        elif driver_operation == 'embedded-create-database':
            connector = self._connector
        else:
            raise RelationalClientError(
                'database creation driver operation is unavailable'
            )
        connection = self._invoke_connector(
            request, connector, {'database': database}
        )
        self._safe_close(connection)
        return {
            'driver_operation': driver_operation,
            'driver_returned': True,
            'transaction_finality_interpreted_by_common_code': False,
        }

    def runtime_identity(self, request, handle=None):
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
        return self._connect(request)

    def describe_transaction(self, handle):
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

    def list_resources(self, request):
        connection = self._connect(request)
        try:
            rows = self.config.metadata_reader(connection, request)
            if not isinstance(rows, list):
                raise RelationalClientError(
                    'relational metadata reader must return a list'
                )
            return copy.deepcopy(rows)
        finally:
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
        return adapter.apply(self, request)

    def read_admin_rows(self, request):
        adapter = self._administration()
        return adapter.read_rows(self, request)

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

    def execute(self, handle, request):
        source = request.get('source')
        if not isinstance(source, str) or not source.strip():
            raise RelationalClientError('relational query source is required')
        parameters = request.get('parameters', ())
        if not isinstance(parameters, (Mapping, list, tuple)):
            raise RelationalClientError(
                'relational query parameters must be a mapping or sequence'
            )
        cursor = None
        try:
            cursor = handle.cursor()
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
            rows = list(cursor.fetchall()) if description else []
            rowcount = getattr(cursor, 'rowcount', None)
            token = _ResultToken(
                cursor, handle, columns, rows,
                rowcount if isinstance(rowcount, int) else None,
            )
            self._tokens.append(token)
            return token
        except RelationalClientError:
            if cursor is not None:
                self._safe_close(cursor)
            raise
        except Exception as exc:
            if cursor is not None:
                self._safe_close(cursor)
            raise RelationalClientError(
                f'relational execution failed ({type(exc).__name__})'
            ) from None

    def describe_result(self, token):
        if not isinstance(token, _ResultToken) or token not in self._tokens:
            raise RelationalClientError('relational result token is invalid')
        if not token.closed:
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
            if not token.closed:
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

    def _forget_and_close(self, connection):
        try:
            self._connections.remove(connection)
        except ValueError:
            pass
        self._safe_close(connection)
