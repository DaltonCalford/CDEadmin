##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Endpoint registration verification and protected-column resolution.

Database-session commits in this module persist pgAdmin configuration only.
They do not represent, infer, or publish target-engine transaction finality.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from pgadmin.cdeadmin.core import EndpointContext
from pgadmin.cdeadmin.security import SecretReference

from .profiles import EndpointRegistrationError, registration_profile


APP_EXTENSION_KEY = 'cdeadmin_endpoint_service'
RESOLVER_ID = 'cdeadmin.protected-column'
VERIFY_PERMISSIONS = frozenset({'network', 'secret_read'})
WORKSPACE_PERMISSIONS = frozenset({
    'network', 'secret_read', 'data_read', 'data_write', 'administer',
    'execute', 'filesystem', 'topology_admin', 'security_admin',
    'backup_admin', 'restore_admin', 'replication_admin',
    'maintenance_admin', 'upgrade_admin',
})
EMBEDDED_VERIFY_PERMISSIONS = frozenset({
    'embedded_runtime', 'filesystem',
})
EMBEDDED_WORKSPACE_PERMISSIONS = frozenset({
    'embedded_runtime', 'filesystem', 'data_read', 'data_write',
    'administer', 'execute', 'topology_admin', 'security_admin',
    'backup_admin', 'restore_admin', 'replication_admin',
    'maintenance_admin', 'upgrade_admin',
})


class ProtectedColumnResolver:
    """Resolve a protected pgAdmin column for its owning endpoint user."""

    def __init__(self):
        self._transient = {}
        self._lock = threading.RLock()

    @contextmanager
    def transient(self, locator, value):
        if isinstance(value, str):
            value = value.encode('utf-8')
        if not isinstance(value, (bytes, bytearray)) or not value:
            raise EndpointRegistrationError(
                'endpoint verification password is unavailable'
            )
        buffer = bytearray(value)
        with self._lock:
            if locator in self._transient:
                raise EndpointRegistrationError(
                    'endpoint credential is already in use'
                )
            self._transient[locator] = buffer
        try:
            yield
        finally:
            with self._lock:
                self._transient.pop(locator, None)
            for index in range(len(buffer)):
                buffer[index] = 0

    def __call__(self, locator, context, _purpose, principal):
        source_kind, source_id, column = self._parse_locator(locator)
        from flask_login import current_user
        from pgadmin.model import Server, SharedServer
        from pgadmin.utils.crypto import decrypt
        from pgadmin.utils.master_password import get_crypt_key

        source_type = Server if source_kind == 'server' else SharedServer
        source = source_type.query.filter_by(id=source_id).first()
        endpoint = getattr(source, 'endpoint_profile', None)
        expected_principal = (
            f'user:{source.user_id}' if source is not None else None
        )
        if source is None or endpoint is None or (
            endpoint.id != context.endpoint_id or
            principal != expected_principal or
            not current_user.is_authenticated or
            current_user.id != source.user_id
        ):
            raise EndpointRegistrationError(
                'protected endpoint credential is unavailable'
            )
        with self._lock:
            transient = self._transient.get(locator)
            if transient is not None:
                return bytes(transient)
        ciphertext = getattr(source, column, None)
        key_present, key = get_crypt_key()
        if ciphertext is None or not key_present:
            raise EndpointRegistrationError(
                'protected endpoint credential is unavailable'
            )
        return decrypt(ciphertext, key)

    @staticmethod
    def _parse_locator(locator):
        try:
            source_kind, raw_id, column = locator.split(':', 2)
            source_id = int(raw_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise EndpointRegistrationError(
                'protected endpoint credential locator is invalid'
            ) from exc
        if source_kind not in {'server', 'sharedserver'} or column not in {
            'password', 'tunnel_password'
        }:
            raise EndpointRegistrationError(
                'protected endpoint credential locator is invalid'
            )
        return source_kind, source_id, column


class EndpointService:
    """Manage provider endpoint verification without legacy driver routing."""

    def __init__(self, provider_registry, security_service):
        self.provider_registry = provider_registry
        self.security_service = security_service
        self.resolver = ProtectedColumnResolver()
        self.security_service.secrets.register_resolver(
            RESOLVER_ID, self.resolver
        )

    def verify_server(self, server, password=None):
        endpoint = getattr(server, 'endpoint_profile', None)
        if endpoint is None or endpoint.provider_version is None:
            raise EndpointRegistrationError(
                'server is not a provider-managed endpoint'
            )
        profile = registration_profile(endpoint.profile_id)
        embedded = profile['route_kind'] == 'embedded_file'
        context = self._context(
            endpoint,
            EMBEDDED_VERIFY_PERMISSIONS if embedded else VERIFY_PERMISSIONS,
        )
        route, reference = self._route_and_reference(
            server, endpoint, profile
        )
        transient = (
            self.resolver.transient(reference.locator, password)
            if reference is not None and password else _empty_context()
        )
        try:
            with transient:
                discovered = self.provider_registry.resolve(
                    context
                ).instance.discover_endpoint({'route': route})
        except Exception:
            self._record_verification(endpoint, 'failed')
            raise EndpointRegistrationError(
                'endpoint verification failed'
            ) from None
        verified = discovered['verified_runtime']
        self._record_verification(
            endpoint,
            'verified',
            verified.get('engine_id'),
            verified.get('version'),
            verified.get('evidence_reference'),
        )
        return {
            'endpoint_id': endpoint.id,
            'profile_id': endpoint.profile_id,
            'profile_version': endpoint.profile_version,
            'verification_state': 'verified',
            'verified_runtime_family': verified.get('engine_id'),
            'verified_runtime_version': verified.get('version'),
            'evidence_reference': verified.get('evidence_reference'),
        }

    def workspace(self, server):
        """Build a verified endpoint DTO without exposing credentials."""
        endpoint = getattr(server, 'endpoint_profile', None)
        if endpoint is not None and endpoint.provider_version is None and (
            endpoint.provider_id == 'org.pgadmin.postgresql'
        ):
            return self._postgresql_workspace(server, endpoint)
        if endpoint is None or endpoint.provider_version is None:
            raise EndpointRegistrationError(
                'server is not a provider-managed endpoint'
            )
        runtime = endpoint.runtime_identity
        if runtime is None or runtime.verification_state != 'verified':
            raise EndpointRegistrationError(
                'endpoint must be verified before opening a workspace'
            )
        profile = registration_profile(endpoint.profile_id)
        embedded = profile['route_kind'] == 'embedded_file'
        context = self._context(
            endpoint,
            EMBEDDED_WORKSPACE_PERMISSIONS
            if embedded else WORKSPACE_PERMISSIONS,
        )
        route, _reference = self._route_and_reference(
            server, endpoint, profile
        )
        binding = self.provider_registry.resolve(context)
        identity = dict(binding.manifest['identity'])
        endpoint_payload = {
            'identity': identity,
            'endpoint_id': endpoint.id,
            'mode': endpoint.endpoint_mode,
            'declared_runtime': {
                'engine_id': runtime.declared_runtime_family,
                'version': runtime.declared_runtime_version,
            },
            'verified_runtime': {
                'engine_id': runtime.verified_runtime_family,
                'version': runtime.verified_runtime_version,
                'verification_state': runtime.verification_state,
                'evidence_reference': (
                    runtime.verification_evidence_reference
                ),
            },
            'route': route,
            'capability_generation': endpoint.cache_namespace,
            'extensions': {},
        }
        root_resource = {
            'identity': identity,
            'endpoint_id': endpoint.id,
            'resource_id': f'endpoint:{endpoint.id}',
            'identity_kind': 'cdeadmin-endpoint-id',
            'resource_kind': 'server',
            'model_family': endpoint.experience_family,
            'display_name': server.name,
            'parent_resource_id': None,
            'display_path': [server.name],
            'authority_path': [
                runtime.declared_runtime_family, 'endpoint', endpoint.id,
            ],
            'is_virtual': True,
            'generation': endpoint.cache_namespace,
            'capability_ids': [],
            'extensions': {'cdeadmin': {'workspace_root': True}},
        }
        return context, endpoint_payload, root_resource

    def _postgresql_workspace(self, server, endpoint):
        """Bridge a connected preserved PostgreSQL server to shared studios."""
        from pgadmin.cdeadmin.providers.postgresql.provider import (
            PROFILE_ID, PROFILE_VERSION, PROVIDER_ID, PROVIDER_VERSION,
        )
        from pgadmin.utils.driver.registry import DriverRegistry

        manager = DriverRegistry.get('psycopg3').connection_manager(server.id)
        connection = manager.connection()
        if not connection.connected():
            raise EndpointRegistrationError(
                'PostgreSQL must be connected before opening shared studios'
            )
        observed = str(manager.ver or '')
        if observed != PROFILE_VERSION:
            raise EndpointRegistrationError(
                'connected PostgreSQL runtime does not match profile 18.3'
            )
        identity = {
            'endpoint_id': endpoint.id,
            'endpoint_mode': 'legacy_native',
            'experience_family': 'postgresql',
            'provider_id': PROVIDER_ID,
            'provider_version': PROVIDER_VERSION,
            'profile_id': PROFILE_ID,
            'profile_version': PROFILE_VERSION,
            'target_adapter_id': 'legacy-pgadmin-server',
            'target_adapter_version': PROVIDER_VERSION,
            'pool_namespace': endpoint.pool_namespace,
            'session_namespace': endpoint.session_namespace,
            'cache_namespace': endpoint.cache_namespace,
            'diagnostic_namespace': endpoint.diagnostic_namespace,
            'declared_runtime_family': 'postgresql',
            'verified_runtime_family': 'postgresql',
            'verified_runtime_version': observed,
            'runtime_verification_state': 'verified',
            'runtime_evidence_reference': 'preserved-postgresql-connection',
        }
        context = EndpointContext.from_identity(
            identity, effective_permissions=WORKSPACE_PERMISSIONS
        )
        binding = self.provider_registry.resolve(context)
        provider_identity = dict(binding.manifest['identity'])
        route = {
            'route_id': f'postgresql-server:{server.id}',
            'server_id': server.id,
            'principal_reference': f'user:{server.user_id}',
        }
        endpoint_payload = {
            'identity': provider_identity, 'endpoint_id': endpoint.id,
            'mode': 'legacy_native',
            'declared_runtime': {
                'engine_id': 'postgresql', 'version': observed,
            },
            'verified_runtime': {
                'engine_id': 'postgresql', 'version': observed,
                'verification_state': 'verified',
                'evidence_reference': 'preserved-postgresql-connection',
            },
            'route': route,
            'capability_generation': endpoint.cache_namespace,
            'extensions': {},
        }
        root_resource = {
            'identity': provider_identity, 'endpoint_id': endpoint.id,
            'resource_id': f'endpoint:{endpoint.id}',
            'identity_kind': 'cdeadmin-endpoint-id',
            'resource_kind': 'server', 'model_family': 'relational',
            'display_name': server.name, 'parent_resource_id': None,
            'display_path': [server.name],
            'authority_path': ['postgresql', 'endpoint', endpoint.id],
            'is_virtual': True, 'generation': endpoint.cache_namespace,
            'capability_ids': [],
            'extensions': {'postgresql': {
                'child_kind': 'database', 'server_id': server.id,
            }},
        }
        return context, endpoint_payload, root_resource

    def _route_and_reference(
        self, server, endpoint, profile=True, requires_secret=None
    ):
        if requires_secret is not None:
            profile = requires_secret
        route_model = min(
            endpoint.routes, key=lambda item: item.priority, default=None
        )
        if route_model is None:
            raise EndpointRegistrationError(
                'endpoint route or credential reference is unavailable'
            )
        route = json.loads(route_model.configuration)
        route['route_id'] = route_model.id
        if isinstance(profile, dict):
            auth_kind = route.get('auth_kind', 'none')
            requires_secret = profile.get('requires_secret', True) or (
                auth_kind in {'basic', 'bearer'}
            ) or (
                profile.get('supports_secret', False) and
                bool(route.get('username'))
            )
        else:
            requires_secret = bool(profile)
            auth_kind = route.get('auth_kind', 'none')
        secret_kind = (
            'api_token' if auth_kind == 'bearer' else 'database_password'
        )
        reference_model = next(
            (
                item for item in endpoint.secret_references
                if item.secret_kind == secret_kind
            ),
            None,
        )
        if requires_secret and reference_model is None:
            raise EndpointRegistrationError(
                'endpoint route or credential reference is unavailable'
            )
        if not requires_secret:
            return route, None
        reference = SecretReference(
            reference_id=reference_model.id,
            endpoint_id=endpoint.id,
            endpoint_mode=endpoint.endpoint_mode,
            secret_kind=reference_model.secret_kind,
            storage_kind=reference_model.storage_kind,
            resolver_id=RESOLVER_ID,
            locator=reference_model.secret_reference,
            allowed_purposes=frozenset({
                'connect', 'administer', 'provider_tool',
            }),
            authority_scope='legacy_engine_auth',
        )
        self.security_service.secrets.register_reference(reference)
        route.update({
            'route_id': route_model.id,
            'credential_reference_id': reference.reference_id,
            'principal_reference': f'user:{server.user_id}',
        })
        if reference.secret_kind != 'database_password':
            route['credential_kind'] = reference.secret_kind
        return route, reference

    @staticmethod
    def _context(endpoint, permissions):
        runtime = endpoint.runtime_identity
        identity = {
            'endpoint_id': endpoint.id,
            'endpoint_mode': endpoint.endpoint_mode,
            'experience_family': endpoint.experience_family,
            'provider_id': endpoint.provider_id,
            'provider_version': endpoint.provider_version,
            'profile_id': endpoint.profile_id,
            'profile_version': endpoint.profile_version,
            'target_adapter_id': endpoint.target_adapter_id,
            'target_adapter_version': endpoint.target_adapter_version,
            'pool_namespace': endpoint.pool_namespace,
            'session_namespace': endpoint.session_namespace,
            'cache_namespace': endpoint.cache_namespace,
            'diagnostic_namespace': endpoint.diagnostic_namespace,
            'declared_runtime_family': runtime.declared_runtime_family,
            'verified_runtime_family': getattr(
                runtime, 'verified_runtime_family', None
            ),
            'verified_runtime_version': getattr(
                runtime, 'verified_runtime_version', None
            ),
            'runtime_verification_state': runtime.verification_state,
            'runtime_evidence_reference': (
                getattr(runtime, 'verification_evidence_reference', None)
            ),
            'runtime_identity_generation': getattr(
                endpoint, 'profile_generation', None
            ),
        }
        return EndpointContext.from_identity(
            identity, effective_permissions=permissions
        )

    @staticmethod
    def _record_verification(
        endpoint, state, family=None, version=None, evidence=None
    ):
        from pgadmin.model import db

        runtime = endpoint.runtime_identity
        runtime.verification_state = state
        runtime.verified_runtime_family = family
        runtime.verified_runtime_version = version
        runtime.verification_evidence_reference = evidence
        runtime.verified_at = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            if state == 'verified' else None
        )
        db.session.commit()


@contextmanager
def _empty_context():
    yield


def init_app(app, provider_registry, security_service):
    existing = app.extensions.get(APP_EXTENSION_KEY)
    if existing is not None:
        return existing
    service = EndpointService(provider_registry, security_service)
    app.extensions[APP_EXTENSION_KEY] = service
    return service


def service_for_app(app):
    try:
        return app.extensions[APP_EXTENSION_KEY]
    except (AttributeError, KeyError) as exc:
        raise EndpointRegistrationError(
            'CDEadmin endpoint service is not initialized'
        ) from exc
