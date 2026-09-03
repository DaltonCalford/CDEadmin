##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Versioned provider package registry and endpoint-scoped bindings."""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable, Iterable, Mapping

from pgadmin.cdeadmin.contracts.v1.runtime import (
    ACTIVE_SUPPORT_STATES,
    ContractValidationError,
    load_contract_schema,
    validate_contract,
)

from .context import EndpointContext


APP_EXTENSION_KEY = 'cdeadmin_provider_registry'
ACTIVE = 'active'
QUARANTINED = 'quarantined'
UNLOADED = 'unloaded'


class ProviderRegistryError(RuntimeError):
    """Base provider registry error."""


class ProviderRegistrationError(ProviderRegistryError):
    """Provider package metadata or registration is invalid."""


class ProviderUnavailableError(ProviderRegistryError):
    """No active compatible provider can serve an endpoint."""


class ProviderPermissionError(ProviderRegistryError):
    """A provider requested authority it was not granted."""


def _version_key(value: str) -> tuple[int, int, int]:
    try:
        core = value.split('-', 1)[0]
        parts = core.split('.')
        if not 1 <= len(parts) <= 3:
            raise ValueError
        numbers = tuple(int(item) for item in parts)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProviderRegistrationError(
            f'invalid semantic version {value!r}'
        ) from exc
    return numbers + (0,) * (3 - len(numbers))


def _string_set(
    values: object, field_name: str, allow_empty: bool = False
) -> frozenset[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ProviderRegistrationError(f'{field_name} must be an array')
    result = frozenset(
        item.strip()
        for item in values
        if isinstance(item, str) and item.strip()
    )
    if len(result) != len(values):
        raise ProviderRegistrationError(
            f'{field_name} contains an invalid or duplicate value'
        )
    if not result and not allow_empty:
        raise ProviderRegistrationError(f'{field_name} must not be empty')
    return result


@dataclass(frozen=True)
class PermissionGrant:
    """One manifest permission grant and its admitted scopes."""

    permission_id: str
    scopes: frozenset[str]


class PermissionGuard:
    """Fail-closed provider permission view for one endpoint."""

    def __init__(
        self,
        manifest_grants: Mapping[str, PermissionGrant],
        endpoint_permissions: frozenset[str],
        violation_handler: Callable[[str], None] | None = None,
        context: EndpointContext | None = None,
        secret_service: object | None = None,
    ):
        self._manifest_grants = dict(manifest_grants)
        self._endpoint_permissions = endpoint_permissions
        self._violation_handler = violation_handler
        self._context = context
        self._secret_service = secret_service

    def _deny(self, message, diagnostic_code):
        if self._violation_handler is not None:
            self._violation_handler(diagnostic_code)
        raise ProviderPermissionError(message)

    def require(self, permission_id: str, scope: str = 'endpoint') -> None:
        grant = self._manifest_grants.get(permission_id)
        if grant is None or permission_id not in self._endpoint_permissions:
            self._deny(
                f'permission {permission_id!r} is not granted',
                'CDE_PROVIDER_PERMISSION_VIOLATION',
            )
        if scope not in grant.scopes:
            self._deny(
                f'permission {permission_id!r} does not grant scope '
                f'{scope!r}',
                'CDE_PROVIDER_SCOPE_VIOLATION',
            )

    def allows(self, permission_id: str, scope: str = 'endpoint') -> bool:
        grant = self._manifest_grants.get(permission_id)
        return bool(
            grant is not None and
            permission_id in self._endpoint_permissions and
            scope in grant.scopes
        )

    def acquire_secret(
        self, reference_id: str, principal: str, purpose: str = 'connect',
        expected_kind: str = 'database_password',
    ):
        """Acquire one endpoint-scoped lease without exposing its value."""
        self.require('secret_read')
        if self._context is None or self._secret_service is None:
            raise ProviderPermissionError(
                'endpoint secret service is unavailable'
            )
        return self._secret_service.acquire(
            reference_id,
            self._context,
            principal,
            purpose,
            expected_kind=expected_kind,
        )


@dataclass
class ProviderRegistration:
    """Loaded provider package metadata and lifecycle state."""

    manifest: dict[str, Any]
    module_name: str
    factory_name: str
    experience_families: frozenset[str]
    target_adapter_ids: frozenset[str]
    required_permissions: frozenset[str]
    permission_grants: dict[str, PermissionGrant]
    state: str = ACTIVE
    diagnostic_code: str | None = None
    error_type: str | None = None
    module: ModuleType | None = None
    factory: Callable | None = None
    bindings: dict[tuple[str, ...], 'ProviderBinding'] = field(
        default_factory=dict
    )

    @property
    def identity(self) -> Mapping[str, str]:
        return self.manifest['identity']

    @property
    def key(self) -> tuple[str, str]:
        return (
            self.identity['provider_id'],
            self.identity['provider_version'],
        )


@dataclass(frozen=True)
class ProviderBinding:
    """One provider instance bound to one complete endpoint identity."""

    context: EndpointContext
    instance: object
    permissions: PermissionGuard
    manifest: Mapping[str, Any]

    def require_permission(
        self, permission_id: str, scope: str = 'endpoint'
    ) -> None:
        self.permissions.require(permission_id, scope)

    def legacy_driver(self, driver_type: str):
        """Resolve an explicitly declared legacy driver compatibility port."""
        if self.context.legacy_driver_type != driver_type:
            raise ProviderUnavailableError(
                'endpoint does not declare the requested legacy driver type'
            )
        resolver = getattr(self.instance, 'get_legacy_driver', None)
        if not callable(resolver):
            raise ProviderUnavailableError(
                'provider does not expose the legacy driver compatibility port'
            )
        return resolver(driver_type)

    def query_tool_manager(self, server_id: int, legacy_manager):
        """Route the preserved Query Tool through a provider-owned port.

        This is an internal migration port, not an engine-neutral transaction
        abstraction.  The provider continues to own all query and transaction
        semantics exercised by the legacy Query Tool implementation.
        """
        if 'ExecutionProvider' not in self.manifest['contracts']:
            raise ProviderUnavailableError(
                'provider does not declare an execution contract'
            )
        resolver = getattr(
            self.instance, 'query_tool_connection_manager', None
        )
        if not callable(resolver):
            raise ProviderUnavailableError(
                'provider does not expose the Query Tool compatibility port'
            )
        return resolver(server_id, legacy_manager)

    def query_tool_execute_async(
        self, connection, source, server_cursor=False
    ):
        """Execute through the provider's preserved Query Tool port."""
        resolver = getattr(self.instance, 'query_tool_execute_async', None)
        if not callable(resolver):
            raise ProviderUnavailableError(
                'provider does not expose Query Tool execution'
            )
        return resolver(connection, source, server_cursor)

    def query_tool_poll(self, connection, **kwargs):
        """Poll through the provider's preserved result port."""
        resolver = getattr(self.instance, 'query_tool_poll', None)
        if not callable(resolver):
            raise ProviderUnavailableError(
                'provider does not expose Query Tool result polling'
            )
        return resolver(connection, **kwargs)

    def query_tool_fetch(self, connection, *args, **kwargs):
        """Fetch rows through the provider's preserved result port."""
        resolver = getattr(self.instance, 'query_tool_fetch', None)
        if not callable(resolver):
            raise ProviderUnavailableError(
                'provider does not expose Query Tool result fetching'
            )
        return resolver(connection, *args, **kwargs)


class ProviderRegistry:
    """Application-scoped registry with endpoint-scoped provider instances."""

    def __init__(
        self,
        allowed_module_prefixes: Iterable[str] = (
            'pgadmin.cdeadmin.providers.',
        ),
        contract_version: str = '1.1.0',
        secret_service: object | None = None,
    ):
        self._allowed_module_prefixes = tuple(allowed_module_prefixes)
        self._contract_version = contract_version
        self._secret_service = secret_service
        self._registrations: dict[
            tuple[str, str], ProviderRegistration
        ] = {}
        self._lock = threading.RLock()

    def register_package(
        self,
        manifest: Mapping[str, Any],
        module_name: str,
        factory_name: str = 'create_provider',
    ) -> ProviderRegistration:
        """Validate and import one production provider package.

        Import failures become quarantined records and never escape into app
        startup. Invalid operator metadata is rejected before import.
        """
        try:
            value = validate_contract('ProviderManifest', manifest)
        except ContractValidationError as exc:
            raise ProviderRegistrationError(str(exc)) from exc
        self._validate_manifest(value)
        self._validate_module_name(module_name, factory_name)
        identity = value['identity']
        key = (identity['provider_id'], identity['provider_version'])
        composition = value.get('composition', {})
        experiences = _string_set(
            composition.get('experience_families'),
            'composition.experience_families',
        )
        adapters = _string_set(
            composition.get('target_adapter_ids'),
            'composition.target_adapter_ids',
        )
        required_permissions = _string_set(
            value.get('required_permissions', []),
            'required_permissions',
            allow_empty=True,
        )
        permission_grants = self._permission_grants(value)
        missing = required_permissions.difference(permission_grants)
        if missing:
            labels = ', '.join(sorted(missing))
            raise ProviderPermissionError(
                f'provider requires permissions absent from its manifest: '
                f'{labels}'
            )
        registration = ProviderRegistration(
            manifest=value,
            module_name=module_name,
            factory_name=factory_name,
            experience_families=experiences,
            target_adapter_ids=adapters,
            required_permissions=required_permissions,
            permission_grants=permission_grants,
        )
        with self._lock:
            if key in self._registrations:
                raise ProviderRegistrationError(
                    'provider ID and version are already registered'
                )
            self._registrations[key] = registration
            try:
                module = importlib.import_module(module_name)
                factory = getattr(module, factory_name)
                if not callable(factory):
                    raise TypeError('provider factory is not callable')
            except Exception as exc:
                self._quarantine_registration(
                    registration,
                    'CDE_PROVIDER_IMPORT_FAILED',
                    type(exc).__name__,
                )
                return registration
            registration.module = module
            registration.factory = factory
        return registration

    def _validate_manifest(self, manifest: Mapping[str, Any]) -> None:
        if manifest['fixture']:
            raise ProviderRegistrationError(
                'fixture packages cannot enter the production registry'
            )
        if not manifest['enabled'] or not manifest['production_registration']:
            raise ProviderRegistrationError(
                'provider must be enabled for production registration'
            )
        schema = load_contract_schema()
        manifest_properties = schema['$defs']['ProviderManifest'][
            'properties'
        ]
        package_types = set(
            manifest_properties['package_type']['x-known-values']
        )
        if manifest['package_type'] not in package_types:
            raise ProviderRegistrationError(
                'provider package type is unknown to this registry'
            )
        if manifest['support_state'] not in ACTIVE_SUPPORT_STATES:
            raise ProviderRegistrationError(
                'provider support state cannot activate production behavior'
            )
        permission_ids = set(
            schema['$defs']['ProviderPermission']['properties'][
                'permission_id'
            ]['x-known-values']
        )
        unknown_permissions = {
            item['permission_id']
            for item in manifest['permissions']
        }.difference(permission_ids)
        if unknown_permissions:
            labels = ', '.join(sorted(unknown_permissions))
            raise ProviderRegistrationError(
                f'provider declares unknown permissions: {labels}'
            )
        compatibility = manifest['sdk_compatibility']
        current = _version_key(self._contract_version)
        minimum = _version_key(compatibility['minimum'])
        maximum = _version_key(compatibility['maximum_exclusive'])
        if not minimum <= current < maximum:
            raise ProviderRegistrationError(
                'provider SDK range does not admit this registry version'
            )
        interfaces = schema['x-cdeadmin-provider-interfaces']
        unknown = set(manifest['contracts']).difference(interfaces)
        if unknown:
            labels = ', '.join(sorted(unknown))
            raise ProviderRegistrationError(
                f'provider declares unknown contracts: {labels}'
            )

    def _validate_module_name(
        self, module_name: str, factory_name: str
    ) -> None:
        if not isinstance(module_name, str) or not any(
            module_name.startswith(prefix)
            for prefix in self._allowed_module_prefixes
        ):
            raise ProviderRegistrationError(
                'provider module is outside configured package roots'
            )
        valid_factory = (
            isinstance(factory_name, str) and factory_name.isidentifier()
        )
        if not valid_factory:
            raise ProviderRegistrationError('provider factory name is invalid')

    @staticmethod
    def _permission_grants(
        manifest: Mapping[str, Any]
    ) -> dict[str, PermissionGrant]:
        grants = {}
        seen = set()
        for item in manifest['permissions']:
            permission_id = item['permission_id']
            if permission_id in seen:
                raise ProviderRegistrationError(
                    f'duplicate permission {permission_id!r}'
                )
            seen.add(permission_id)
            if not item['granted']:
                continue
            grants[permission_id] = PermissionGrant(
                permission_id,
                _string_set(
                    item['scope'],
                    f'permissions.{permission_id}.scope',
                ),
            )
        return grants

    def has_registration(self, context: EndpointContext) -> bool:
        """Return whether the exact endpoint provider version is known."""
        if context.provider_version is None:
            return False
        key = (context.provider_id, context.provider_version)
        with self._lock:
            return key in self._registrations

    def resolve(self, context: EndpointContext) -> ProviderBinding:
        """Return or create a provider instance isolated to one endpoint."""
        if context.provider_version is None:
            raise ProviderUnavailableError(
                'endpoint provider version has not been verified'
            )
        key = (context.provider_id, context.provider_version)
        with self._lock:
            registration = self._registrations.get(key)
            if registration is None:
                raise ProviderUnavailableError(
                    'no exact provider ID/version registration exists'
                )
            if registration.state != ACTIVE:
                raise ProviderUnavailableError(
                    f'provider registration is {registration.state}'
                )
            self._validate_context(registration, context)
            cached = registration.bindings.get(context.isolation_key)
            if cached is not None:
                return cached
            permissions = PermissionGuard(
                registration.permission_grants,
                context.effective_permissions,
                lambda diagnostic: self._quarantine_permission_violation(
                    registration, diagnostic
                ),
                context=context,
                secret_service=self._secret_service,
            )
            instance = None
            try:
                instance = registration.factory(context, permissions)
                self._validate_instance(registration, instance)
                if registration.state != ACTIVE:
                    raise ProviderPermissionError(
                        'provider was quarantined during factory creation'
                    )
            except Exception as exc:
                if instance is not None:
                    self._close_instance(instance)
                if registration.state == ACTIVE:
                    self._quarantine_registration(
                        registration,
                        'CDE_PROVIDER_FACTORY_FAILED',
                        type(exc).__name__,
                    )
                raise ProviderUnavailableError(
                    'provider factory failed and was quarantined'
                ) from None
            binding = ProviderBinding(
                context=context,
                instance=instance,
                permissions=permissions,
                manifest=registration.manifest,
            )
            registration.bindings[context.isolation_key] = binding
            return binding

    def _quarantine_permission_violation(
        self, registration, diagnostic_code
    ):
        with self._lock:
            self._quarantine_registration(
                registration, diagnostic_code, 'ProviderPermissionError'
            )

    @staticmethod
    def _validate_instance(
        registration: ProviderRegistration, instance: object
    ) -> None:
        interfaces = load_contract_schema()[
            'x-cdeadmin-provider-interfaces'
        ]
        for contract_name in registration.manifest['contracts']:
            methods = interfaces[contract_name]
            for method_name in methods:
                method = getattr(instance, method_name, None)
                if not callable(method):
                    raise TypeError(
                        f'provider does not implement {contract_name}.'
                        f'{method_name}'
                    )

    @staticmethod
    def _validate_context(
        registration: ProviderRegistration,
        context: EndpointContext,
    ) -> None:
        if context.experience_family not in registration.experience_families:
            raise ProviderUnavailableError(
                'provider does not declare the endpoint experience'
            )
        if context.target_adapter_id not in registration.target_adapter_ids:
            raise ProviderUnavailableError(
                'provider does not declare the endpoint target adapter'
            )
        unknown = context.effective_permissions.difference(
            registration.permission_grants
        )
        if unknown:
            labels = ', '.join(sorted(unknown))
            raise ProviderPermissionError(
                f'endpoint grants permissions absent from manifest: {labels}'
            )
        missing = registration.required_permissions.difference(
            context.effective_permissions
        )
        if missing:
            labels = ', '.join(sorted(missing))
            raise ProviderPermissionError(
                f'endpoint policy withholds required permissions: {labels}'
            )

    def quarantine(
        self,
        provider_id: str,
        provider_version: str,
        diagnostic_code: str = 'CDE_PROVIDER_OPERATOR_QUARANTINE',
    ) -> None:
        """Quarantine a provider and close all endpoint instances."""
        with self._lock:
            registration = self._registration(
                provider_id, provider_version
            )
            self._quarantine_registration(
                registration, diagnostic_code, None
            )

    def unload(self, provider_id: str, provider_version: str) -> None:
        """Close instances and retain an unloaded lifecycle tombstone."""
        with self._lock:
            registration = self._registration(
                provider_id, provider_version
            )
            self._close_bindings(registration)
            registration.factory = None
            registration.module = None
            registration.state = UNLOADED
            registration.diagnostic_code = 'CDE_PROVIDER_UNLOADED'
            registration.error_type = None

    def close(self) -> None:
        """Unload every active or quarantined package."""
        with self._lock:
            for registration in self._registrations.values():
                self._close_bindings(registration)
                registration.factory = None
                registration.module = None
                registration.state = UNLOADED
                registration.diagnostic_code = 'CDE_PROVIDER_UNLOADED'
                registration.error_type = None

    def status(self) -> tuple[dict[str, object], ...]:
        """Return redacted lifecycle status without exception messages."""
        with self._lock:
            return tuple(
                {
                    'provider_id': item.identity['provider_id'],
                    'provider_version': item.identity['provider_version'],
                    'state': item.state,
                    'diagnostic_code': item.diagnostic_code,
                    'error_type': item.error_type,
                    'endpoint_binding_count': len(item.bindings),
                }
                for item in self._registrations.values()
            )

    def _registration(
        self, provider_id: str, provider_version: str
    ) -> ProviderRegistration:
        try:
            return self._registrations[(provider_id, provider_version)]
        except KeyError as exc:
            raise ProviderUnavailableError(
                'provider ID/version is not registered'
            ) from exc

    def _quarantine_registration(
        self,
        registration: ProviderRegistration,
        diagnostic_code: str,
        error_type: str | None,
    ) -> None:
        self._close_bindings(registration)
        registration.state = QUARANTINED
        registration.diagnostic_code = diagnostic_code
        registration.error_type = error_type

    @staticmethod
    def _close_bindings(registration: ProviderRegistration) -> None:
        bindings = list(registration.bindings.values())
        registration.bindings.clear()
        for binding in bindings:
            ProviderRegistry._close_instance(binding.instance)

    @staticmethod
    def _close_instance(instance: object) -> None:
        close = getattr(instance, 'close', None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def init_app(app, secret_service=None) -> ProviderRegistry:
    """Install one provider registry in a Flask-compatible application."""
    extensions = getattr(app, 'extensions', None)
    if extensions is None:
        extensions = {}
        setattr(app, 'extensions', extensions)
    existing = extensions.get(APP_EXTENSION_KEY)
    if existing is not None:
        return existing
    registry = ProviderRegistry(secret_service=secret_service)
    extensions[APP_EXTENSION_KEY] = registry
    return registry


def registry_for_app(app) -> ProviderRegistry:
    """Return the initialized provider registry for an application."""
    try:
        return app.extensions[APP_EXTENSION_KEY]
    except (AttributeError, KeyError) as exc:
        raise ProviderUnavailableError(
            'CDEadmin provider registry is not initialized'
        ) from exc


def route_query_tool_manager(app, server_id: int, legacy_manager):
    """Use an endpoint provider port when a verified context is active."""
    from .context import current_endpoint_context

    context = current_endpoint_context()
    if context is None:
        return legacy_manager
    registry = registry_for_app(app)
    if not registry.has_registration(context):
        return legacy_manager
    return registry.resolve(context).query_tool_manager(
        server_id, legacy_manager
    )


def _query_tool_binding(app):
    from .context import current_endpoint_context

    context = current_endpoint_context()
    if context is None:
        return None
    registry = registry_for_app(app)
    if not registry.has_registration(context):
        return None
    return registry.resolve(context)


def route_query_tool_execute(
    app, connection, source, server_cursor=False
):
    """Route async Query Tool execution or preserve the legacy call."""
    binding = _query_tool_binding(app)
    if binding is None:
        return connection.execute_async(
            source, server_cursor=server_cursor
        )
    return binding.query_tool_execute_async(
        connection, source, server_cursor
    )


def route_query_tool_poll(app, connection, **kwargs):
    """Route Query Tool polling or preserve the legacy call."""
    binding = _query_tool_binding(app)
    if binding is None:
        return connection.poll(**kwargs)
    return binding.query_tool_poll(connection, **kwargs)


def route_query_tool_fetch(app, connection, *args, **kwargs):
    """Route Query Tool result fetching or preserve the legacy call."""
    binding = _query_tool_binding(app)
    if binding is None:
        return connection.async_fetchmany_2darray(*args, **kwargs)
    return binding.query_tool_fetch(connection, *args, **kwargs)
