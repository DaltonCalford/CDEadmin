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
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone

from pgadmin.cdeadmin.core import EndpointContext
from pgadmin.cdeadmin.security import SecretReference
from pgadmin.cdeadmin.security import (
    credential_from_protected_value,
    encode_credential_bundle,
)

from .profiles import (
    EndpointRegistrationError,
    active_secret_fields,
    registration_profile,
    provider_route_options,
)
from .routing import RouteHealthRegistry, RouteSelectionError


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
        source_kind, source_id, column, credential_kind = (
            self._parse_locator(locator)
        )
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
                value = bytes(transient)
                return self._select_credential(
                    value, column, credential_kind
                )
        ciphertext = getattr(source, column, None)
        key_present, key = get_crypt_key()
        if ciphertext is None or not key_present:
            raise EndpointRegistrationError(
                'protected endpoint credential is unavailable'
            )
        return self._select_credential(
            decrypt(ciphertext, key), column, credential_kind
        )

    @staticmethod
    def _select_credential(value, column, credential_kind):
        if credential_kind is None:
            return value
        legacy_kind = (
            'database_password' if column == 'password'
            else 'tunnel_password'
        )
        return credential_from_protected_value(
            value, credential_kind, legacy_kind=legacy_kind
        )

    @staticmethod
    def _parse_locator(locator):
        try:
            parts = locator.split(':')
            if len(parts) not in {3, 4}:
                raise ValueError
            source_kind, raw_id, column = parts[:3]
            credential_kind = parts[3] if len(parts) == 4 else None
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
        if credential_kind is not None and not credential_kind.strip():
            raise EndpointRegistrationError(
                'protected endpoint credential locator is invalid'
            )
        return source_kind, source_id, column, credential_kind


class EndpointService:
    """Manage provider endpoint verification without legacy driver routing."""

    def __init__(self, provider_registry, security_service, route_health=None):
        self.provider_registry = provider_registry
        self.security_service = security_service
        self.route_health = route_health or RouteHealthRegistry()
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
        discovered = None
        selected_route = None
        try:
            candidates = self.route_health.candidates(
                endpoint.id, endpoint.routes
            )
        except RouteSelectionError as exc:
            raise EndpointRegistrationError(str(exc)) from exc
        for route_model in candidates:
            route, reference = self._route_and_reference(
                server, endpoint, profile, route_model=route_model
            )
            transient = self._transient_credentials(
                endpoint, route, reference, password
            )
            try:
                with transient:
                    discovered = self.provider_registry.resolve(
                        context
                    ).instance.discover_endpoint({'route': route})
            except Exception:
                self.route_health.record_failure(endpoint.id, route_model.id)
                continue
            self.route_health.record_success(endpoint.id, route_model.id)
            selected_route = route_model
            break
        if discovered is None:
            self._record_verification(endpoint, 'failed')
            raise EndpointRegistrationError(
                'endpoint verification failed'
            )
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
            'selected_route_id': selected_route.id,
        }

    @contextmanager
    def _transient_credentials(self, endpoint, route, primary, values):
        if primary is None or not values:
            yield
            return
        payload = (
            encode_credential_bundle(values)
            if isinstance(values, dict) else values
        )
        reference_ids = set(
            route.get('credential_references', {}).values()
        )
        reference_ids.add(primary.reference_id)
        models = {
            item.id: item for item in endpoint.secret_references
        }
        with ExitStack() as stack:
            for reference_id in sorted(reference_ids):
                model = models.get(reference_id)
                if model is not None:
                    stack.enter_context(self.resolver.transient(
                        model.secret_reference, payload
                    ))
            yield

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
        candidates = self.route_health.candidates(
            endpoint.id, endpoint.routes
        )
        route, _reference = self._route_and_reference(
            server, endpoint, profile, route_model=candidates[0]
        )
        route_candidates = [
            self._route_and_reference(
                server, endpoint, profile, route_model=item
            )[0]
            for item in candidates
        ]
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
            'route_candidates': route_candidates,
            'route_health': self.route_health.snapshot(endpoint.id),
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

    def route_catalog(self, server):
        """Return owner-safe, credential-free persistent route definitions."""
        endpoint, profile = self._managed_endpoint(server)
        routes = []
        for model in sorted(
            endpoint.routes, key=lambda item: (item.priority, item.id)
        ):
            configuration = self._route_configuration(model)
            configuration.pop('credential_references', None)
            configuration.pop('credential_reference_id', None)
            configuration.pop('principal_reference', None)
            routes.append({
                'route_id': model.id,
                'route_kind': model.route_kind,
                'priority': model.priority,
                'configuration': configuration,
                'health': self.route_health.snapshot(endpoint.id).get(
                    model.id, {}
                ),
            })
        return {
            'endpoint_id': endpoint.id,
            'profile_id': profile['profile_id'],
            'supports_multiple_routes': profile['route_kind'] == 'network',
            'default_port': profile.get('default_port'),
            'connection_fields': profile.get('connection_fields', []),
            'database_targeting': profile.get('database_targeting', {}),
            'routes': routes,
        }

    def database_catalog(self, server):
        """Return database targets without confusing them with routes."""
        endpoint, profile = self._managed_endpoint(server)
        targeting = profile.get('database_targeting', {})
        targets = [self._database_target_value(item) for item in sorted(
            endpoint.database_targets,
            key=lambda item: (not item.active, item.display_name, item.id),
        )]
        legacy_database = None
        if not targets:
            route = min(
                endpoint.routes,
                key=lambda item: (item.priority, item.id),
                default=None,
            )
            if route is not None:
                legacy_database = self._route_configuration(route).get(
                    'database'
                )
        return {
            'endpoint_id': endpoint.id,
            'mode': targeting.get('mode', 'required'),
            'multiple': targeting.get('multiple') is True,
            'server_verification': (
                targeting.get('server_verification') is True
            ),
            'create_and_activate': (
                targeting.get('create_and_activate') is True
            ),
            'active_target_id': next((
                item['target_id'] for item in targets if item['active']
            ), None),
            'legacy_route_database': legacy_database,
            'targets': targets,
        }

    def attach_database(self, server, data):
        """Verify, retain and activate one database on an existing server."""
        from pgadmin.model import EndpointDatabaseTarget, db

        endpoint, profile = self._managed_endpoint(server)
        self._require_multiple_database_targeting(profile)
        database, display_name = self._database_target_input(data)
        existing = next((
            item for item in endpoint.database_targets
            if item.database == database
        ), None)
        self._verify_database_target(server, endpoint, profile, database)
        for item in endpoint.database_targets:
            item.active = False
        if existing is None:
            existing = EndpointDatabaseTarget(
                id=str(uuid.uuid4()), endpoint_id=endpoint.id,
                display_name=display_name, database=database,
                configuration='{}', active=True,
            )
            db.session.add(existing)
        else:
            existing.display_name = display_name
            existing.active = True
        self._remove_route_databases(endpoint)
        endpoint.profile_generation = str(uuid.uuid4())
        db.session.commit()
        return self.database_catalog(server)

    def activate_database(self, server, target_id):
        """Verify and select an already retained database target."""
        from pgadmin.model import db

        endpoint, profile = self._managed_endpoint(server)
        self._require_multiple_database_targeting(profile)
        target = self._owned_database_target(endpoint, target_id)
        self._verify_database_target(
            server, endpoint, profile, target.database
        )
        for item in endpoint.database_targets:
            item.active = item.id == target.id
        endpoint.profile_generation = str(uuid.uuid4())
        db.session.commit()
        return self.database_catalog(server)

    def disconnect_database(self, server):
        """Return an endpoint to server scope after verifying that scope."""
        from pgadmin.model import db

        endpoint, profile = self._managed_endpoint(server)
        targeting = self._require_multiple_database_targeting(profile)
        if not targeting.get('server_verification'):
            raise EndpointRegistrationError(
                'the endpoint cannot operate without a database'
            )
        self._verify_database_target(
            server, endpoint, profile, None
        )
        for item in endpoint.database_targets:
            item.active = False
        self._remove_route_databases(endpoint)
        endpoint.profile_generation = str(uuid.uuid4())
        db.session.commit()
        return self.database_catalog(server)

    def delete_database_target(self, server, target_id):
        """Forget an attachment; this never drops the provider database."""
        from pgadmin.model import db

        endpoint, profile = self._managed_endpoint(server)
        self._require_multiple_database_targeting(profile)
        target = self._owned_database_target(endpoint, target_id)
        was_active = target.active
        db.session.delete(target)
        if was_active:
            endpoint.profile_generation = str(uuid.uuid4())
        db.session.commit()
        return self.database_catalog(server)

    def retain_created_database(self, server, target):
        """Activate a provider-created database after its driver succeeds."""
        from pgadmin.model import EndpointDatabaseTarget, db

        endpoint, profile = self._managed_endpoint(server)
        targeting = profile.get('database_targeting', {})
        if not targeting.get('create_and_activate'):
            return None
        database, display_name = self._database_target_input(target)
        for item in endpoint.database_targets:
            item.active = False
        model = next((
            item for item in endpoint.database_targets
            if item.database == database
        ), None)
        if model is None:
            model = EndpointDatabaseTarget(
                id=str(uuid.uuid4()), endpoint_id=endpoint.id,
                display_name=display_name, database=database,
                configuration='{}', active=True,
            )
            db.session.add(model)
        else:
            model.display_name = display_name
            model.active = True
        self._remove_route_databases(endpoint)
        endpoint.profile_generation = str(uuid.uuid4())
        db.session.commit()
        return self.database_catalog(server)

    def create_route(self, server, data):
        """Create a validated alternate route for one network endpoint."""
        from pgadmin.model import EndpointRoute, db

        endpoint, profile = self._managed_endpoint(server)
        if profile['route_kind'] != 'network':
            raise EndpointRegistrationError(
                'embedded endpoints do not support alternate routes'
            )
        configuration = self._validated_route(profile, data)
        route_id = str(uuid.uuid4())
        priorities = {item.priority for item in endpoint.routes}
        priority = data.get(
            'priority', max(priorities, default=-1) + 1
        )
        self._validate_route_priority(priority, priorities)
        model = EndpointRoute(
            id=route_id,
            endpoint_id=endpoint.id,
            route_kind='network',
            route_reference=f'cde-route:{route_id}',
            priority=priority,
            configuration=self._encoded_route(configuration),
        )
        db.session.add(model)
        self._stale(endpoint)
        db.session.commit()
        return self.route_catalog(server)

    def update_route(self, server, route_id, data):
        """Replace admitted values on a persistent route."""
        from pgadmin.model import db

        endpoint, profile = self._managed_endpoint(server)
        model = self._owned_route(endpoint, route_id)
        existing = self._route_configuration(model)
        configuration = self._validated_route(profile, data, existing)
        priorities = {
            item.priority for item in endpoint.routes if item.id != model.id
        }
        priority = data.get('priority', model.priority)
        self._validate_route_priority(priority, priorities)
        model.priority = priority
        model.configuration = self._encoded_route(configuration)
        self.route_health.clear(endpoint.id, model.id)
        self._stale(endpoint)
        db.session.commit()
        return self.route_catalog(server)

    def delete_route(self, server, route_id):
        """Delete one alternate route while retaining a usable endpoint."""
        from pgadmin.model import db

        endpoint, _profile = self._managed_endpoint(server)
        if len(endpoint.routes) <= 1:
            raise EndpointRegistrationError(
                'the final endpoint route cannot be deleted'
            )
        model = self._owned_route(endpoint, route_id)
        db.session.delete(model)
        self.route_health.clear(endpoint.id, model.id)
        self._stale(endpoint)
        db.session.commit()
        return self.route_catalog(server)

    @staticmethod
    def _managed_endpoint(server):
        endpoint = getattr(server, 'endpoint_profile', None)
        if endpoint is None or endpoint.provider_version is None:
            raise EndpointRegistrationError(
                'server is not a provider-managed endpoint'
            )
        return endpoint, registration_profile(endpoint.profile_id)

    @staticmethod
    def _require_multiple_database_targeting(profile):
        targeting = profile.get('database_targeting', {})
        if not targeting.get('multiple'):
            raise EndpointRegistrationError(
                'the endpoint does not support database target management'
            )
        return targeting

    @staticmethod
    def _database_target_input(data):
        if not isinstance(data, dict):
            raise EndpointRegistrationError(
                'database target input must be an object'
            )
        database = data.get('database')
        if not isinstance(database, str) or not database.strip():
            raise EndpointRegistrationError(
                'database target must not be empty'
            )
        database = database.strip()
        if len(database) > 4096 or any(
            character in database for character in ('\x00', '\r', '\n')
        ):
            raise EndpointRegistrationError(
                'database target is invalid'
            )
        display_name = data.get('display_name')
        if display_name is None:
            display_name = database.rsplit('/', 1)[-1]
        if not isinstance(display_name, str) or not display_name.strip() or (
            len(display_name.strip()) > 256
        ):
            raise EndpointRegistrationError(
                'database target display name is invalid'
            )
        return database, display_name.strip()

    @staticmethod
    def _database_target_value(target):
        try:
            configuration = json.loads(target.configuration)
        except (TypeError, ValueError):
            configuration = {}
        return {
            'target_id': target.id,
            'display_name': target.display_name,
            'database': target.database,
            'configuration': configuration,
            'active': bool(target.active),
        }

    @classmethod
    def _remove_route_databases(cls, endpoint):
        for route_model in endpoint.routes:
            configuration = cls._route_configuration(route_model)
            if 'database' in configuration:
                configuration.pop('database')
                route_model.configuration = cls._encoded_route(configuration)

    @staticmethod
    def _owned_database_target(endpoint, target_id):
        target_id = str(target_id or '')
        target = next((
            item for item in endpoint.database_targets
            if item.id == target_id
        ), None)
        if target is None:
            raise EndpointRegistrationError(
                'database target is unavailable'
            )
        return target

    def _verify_database_target(self, server, endpoint, profile, database):
        context = self._context(endpoint, VERIFY_PERMISSIONS)
        candidates = self.route_health.candidates(
            endpoint.id, endpoint.routes
        )
        for route_model in candidates:
            route, _reference = self._route_and_reference(
                server, endpoint, profile, route_model=route_model,
                database_override=database,
            )
            try:
                self.provider_registry.resolve(
                    context
                ).instance.discover_endpoint({'route': route})
            except Exception:
                self.route_health.record_failure(endpoint.id, route_model.id)
                continue
            self.route_health.record_success(endpoint.id, route_model.id)
            return
        raise EndpointRegistrationError(
            'database target verification failed'
        )

    @staticmethod
    def _owned_route(endpoint, route_id):
        route_id = str(route_id or '')
        model = next(
            (item for item in endpoint.routes if item.id == route_id), None
        )
        if model is None:
            raise EndpointRegistrationError(
                'endpoint route is unavailable'
            )
        return model

    @staticmethod
    def _route_configuration(model):
        try:
            value = json.loads(model.configuration)
        except (TypeError, ValueError) as exc:
            raise EndpointRegistrationError(
                'endpoint route configuration is invalid'
            ) from exc
        if not isinstance(value, dict):
            raise EndpointRegistrationError(
                'endpoint route configuration is invalid'
            )
        return value

    @staticmethod
    def _encoded_route(configuration):
        return json.dumps(
            configuration, sort_keys=True, separators=(',', ':')
        )

    @staticmethod
    def _validate_route_priority(priority, occupied):
        if isinstance(priority, bool) or not isinstance(priority, int) or (
            not 0 <= priority <= 100000
        ):
            raise EndpointRegistrationError(
                'endpoint route priority is invalid'
            )
        if priority in occupied:
            raise EndpointRegistrationError(
                'endpoint route priority is already in use'
            )

    @staticmethod
    def _validated_route(profile, data, existing=None):
        if not isinstance(data, dict):
            raise EndpointRegistrationError(
                'endpoint route input must be an object'
            )
        forbidden = {
            'credential_reference_id', 'credential_references',
            'principal_reference', 'password', 'secret',
        }
        if forbidden.intersection(data):
            raise EndpointRegistrationError(
                'endpoint route input cannot contain credentials'
            )
        result = provider_route_options(profile, data, existing)
        if profile.get('route_kind') == 'embedded_file':
            if not result.get('database') or not result.get(
                'filesystem_root'
            ):
                raise EndpointRegistrationError(
                    'embedded endpoint route is incomplete'
                )
            return result
        if profile.get('database_targeting', {}).get('multiple'):
            if data.get('database') not in {None, ''}:
                raise EndpointRegistrationError(
                    'databases must be managed as endpoint database targets'
                )
            result.pop('database', None)
        for field in ('host', 'user', 'database'):
            if field in data:
                value = data[field]
                if value is not None and not isinstance(value, str):
                    raise EndpointRegistrationError(
                        f'endpoint route {field} must be text'
                    )
                if value in {None, ''}:
                    result.pop(field, None)
                else:
                    result[field] = value.strip()
        if 'port' in data:
            port = data['port']
            if isinstance(port, bool) or not isinstance(port, int) or (
                not 1 <= port <= 65535
            ):
                raise EndpointRegistrationError(
                    'endpoint route port is invalid'
                )
            result['port'] = port
        if not result.get('host') or not result.get('port'):
            raise EndpointRegistrationError(
                'endpoint route host and port are required'
            )
        return result

    @staticmethod
    def _stale(endpoint):
        runtime = endpoint.runtime_identity
        endpoint.profile_generation = str(uuid.uuid4())
        runtime.verification_state = 'stale'
        runtime.verified_runtime_family = None
        runtime.verified_runtime_version = None
        runtime.verification_evidence_reference = None
        runtime.verified_at = None

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
        self, server, endpoint, profile=True, requires_secret=None,
        route_model=None, database_override=Ellipsis,
    ):
        if requires_secret is not None:
            profile = requires_secret
        route_model = route_model or min(
            endpoint.routes, key=lambda item: (item.priority, item.id),
            default=None,
        )
        if route_model is None:
            raise EndpointRegistrationError(
                'endpoint route or credential reference is unavailable'
            )
        route = json.loads(route_model.configuration)
        if database_override is not Ellipsis:
            if database_override is None:
                route.pop('database', None)
            else:
                route['database'] = database_override
        elif isinstance(profile, dict) and profile.get(
            'database_targeting', {}
        ).get('multiple'):
            active = next((
                item for item in endpoint.database_targets if item.active
            ), None)
            if active is not None:
                route['database'] = active.database
                route['database_target_id'] = active.id
            else:
                route.pop('database', None)
        route['route_id'] = route_model.id
        if isinstance(profile, dict) and profile.get('secret_fields'):
            fields = active_secret_fields(profile, route)
            models = {
                item.secret_kind: item
                for item in endpoint.secret_references
            }
            present = route.get('credential_kinds')
            if present is not None:
                if not isinstance(present, list) or not all(
                    isinstance(item, str) for item in present
                ):
                    raise EndpointRegistrationError(
                        'endpoint credential-kind presence is invalid'
                    )
                models = {
                    kind: model for kind, model in models.items()
                    if kind in present
                }
            missing = [
                field['secret_kind'] for field in fields
                if field['required'] and field['secret_kind'] not in models
            ]
            if missing:
                raise EndpointRegistrationError(
                    'endpoint credential references are unavailable: ' +
                    ', '.join(missing)
                )
            references = {}
            for field in fields:
                model = models.get(field['secret_kind'])
                if model is None:
                    continue
                reference = self._bind_reference(server, endpoint, model)
                references[field['secret_kind']] = reference
            route['credential_references'] = {
                kind: reference.reference_id
                for kind, reference in references.items()
            }
            primary_fields = [field for field in fields if field['primary']]
            if len(primary_fields) > 1:
                raise EndpointRegistrationError(
                    'authentication mechanism selected multiple primary '
                    'credentials'
                )
            primary_field = next(
                iter(primary_fields),
                next((field for field in fields if field['required']), None),
            )
            if primary_field is None:
                primary_field = next(iter(fields), None)
            if primary_field is None:
                route.pop('credential_references', None)
                return route, None
            primary = references.get(primary_field['secret_kind'])
            if primary is None:
                if references:
                    route['principal_reference'] = f'user:{server.user_id}'
                return route, None
            route.update({
                'credential_reference_id': primary.reference_id,
                'credential_kind': primary.secret_kind,
                'principal_reference': f'user:{server.user_id}',
            })
            return route, primary
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
        reference = self._bind_reference(
            server, endpoint, reference_model
        )
        route.update({
            'route_id': route_model.id,
            'credential_reference_id': reference.reference_id,
            'principal_reference': f'user:{server.user_id}',
        })
        if reference.secret_kind != 'database_password':
            route['credential_kind'] = reference.secret_kind
        return route, reference

    def _bind_reference(self, server, endpoint, reference_model):
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
        return reference

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
