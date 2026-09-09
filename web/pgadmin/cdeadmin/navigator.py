##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-neutral Object Explorer hierarchy presentation.

Provider resource identities and authority paths remain opaque.  The
navigator uses only the provider-owned display path and resource kind to
construct visual folders and parent/child relationships.
"""

from __future__ import annotations

import base64
import ipaddress
import json
from pathlib import PurePath


class ProviderNavigatorError(ValueError):
    """A navigator token or provider presentation is invalid."""


_KIND_LABELS = {
    'character-set': 'Character sets',
    'constraint': 'Constraints',
    'database': 'Databases',
    'domain': 'Domains',
    'event': 'Jobs and events',
    'exception': 'Exceptions',
    'extension': 'Extensions',
    'external-function': 'External functions',
    'function': 'Functions',
    'index': 'Indexes',
    'package': 'Packages',
    'partition': 'Partitions',
    'plugin': 'Extensions and plugins',
    'privilege': 'Roles and grants',
    'procedure': 'Procedures',
    'publication': 'Replication objects',
    'replication-channel': 'Replication objects',
    'resource-group': 'Resource groups',
    'role': 'Roles',
    'schema': 'Schemas',
    'sequence': 'Sequences',
    'server-link': 'Server links',
    'service-operation': 'Service operations',
    'table': 'Tables',
    'tablespace': 'Tablespaces and filespaces',
    'trigger': 'Triggers',
    'type': 'Types',
    'user': 'Users',
    'view': 'Views',
}


def kind_label(kind):
    """Return a readable plural label without imposing an object model."""
    value = str(kind or '').strip()
    if not value:
        raise ProviderNavigatorError('resource kind is required')
    if value in _KIND_LABELS:
        return _KIND_LABELS[value]
    words = value.replace('_', ' ').replace('-', ' ').strip()
    return words[:1].upper() + words[1:] + (
        '' if words.endswith('s') else 's'
    )


def server_display_name(host, fallback):
    """Return the physical server label used by the Object Explorer.

    Connection-profile names remain metadata.  The tree itself represents
    ownership, so a loopback endpoint is the ``localhost`` server and its
    databases appear below that server.
    """
    value = str(host or '').strip()
    fallback_value = str(fallback or '').strip()
    if not value:
        return fallback_value
    unbracketed = value[1:-1] if (
        value.startswith('[') and value.endswith(']')
    ) else value
    if unbracketed.casefold() in {'localhost', 'localhost.localdomain'}:
        return 'localhost'
    try:
        if ipaddress.ip_address(unbracketed).is_loopback:
            return 'localhost'
    except ValueError:
        pass
    return value


def is_loopback_server(host):
    """Return whether a configured server identifies the local machine."""
    return server_display_name(host, '') == 'localhost'


def encode_navigator_state(state):
    """Encode non-secret presentation state for a child URL."""
    if not isinstance(state, dict):
        raise ProviderNavigatorError('navigator state must be an object')
    raw = json.dumps(
        state, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
    ).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def decode_navigator_state(token):
    """Decode and strictly validate a navigator child token."""
    if not isinstance(token, str) or not token or len(token) > 16384:
        raise ProviderNavigatorError('navigator token is invalid')
    try:
        padding = '=' * (-len(token) % 4)
        state = json.loads(base64.urlsafe_b64decode(
            token + padding
        ).decode('utf-8'))
    except Exception as exc:
        raise ProviderNavigatorError('navigator token is invalid') from exc
    if not isinstance(state, dict) or set(state).difference({
        'scope', 'target_id', 'database', 'display_name', 'parent_path',
        'resource_kind', 'resource_id',
    }):
        raise ProviderNavigatorError('navigator state is invalid')
    if state.get('scope') not in {'database', 'server', 'kind', 'resource'}:
        raise ProviderNavigatorError('navigator scope is invalid')
    path = state.get('parent_path', [])
    if not isinstance(path, list) or not all(
        isinstance(item, str) and item for item in path
    ):
        raise ProviderNavigatorError('navigator display path is invalid')
    for name in ('target_id', 'database', 'display_name', 'resource_kind',
                 'resource_id'):
        value = state.get(name)
        if value is not None and not isinstance(value, str):
            raise ProviderNavigatorError(
                f'navigator {name.replace("_", " ")} is invalid'
            )
    return state


def database_entries(catalog):
    """Present retained and legacy route databases as server children."""
    if not isinstance(catalog, dict):
        raise ProviderNavigatorError('database catalog is invalid')
    entries = []
    for target in catalog.get('targets', []):
        if not isinstance(target, dict):
            raise ProviderNavigatorError('database target is invalid')
        target_id = target.get('target_id')
        database = target.get('database')
        display_name = target.get('display_name')
        if not all(
            isinstance(item, str) and item.strip()
            for item in (target_id, database, display_name)
        ):
            raise ProviderNavigatorError('database target is incomplete')
        entries.append({
            'target_id': target_id,
            'database': database,
            'display_name': display_name,
            'active': target.get('active') is True,
            'legacy': False,
        })
    legacy = catalog.get('legacy_route_database')
    if (
        not entries and
        isinstance(legacy, (str, int)) and
        not isinstance(legacy, bool) and
        str(legacy).strip()
    ):
        legacy_value = str(legacy)
        name = PurePath(legacy_value).name or legacy_value
        entries.append({
            'target_id': 'legacy-route-database',
            'database': legacy_value,
            'display_name': name,
            'active': True,
            'legacy': True,
        })
    return entries


def resource_children(resources, state):
    """Return immediate group or resource presentations for one tree node."""
    if not isinstance(resources, (list, tuple)):
        raise ProviderNavigatorError('provider resources must be an array')
    state = dict(state)
    prepared = _prepared_resources(resources, state)
    scope = state['scope']
    parent_path = tuple(state.get('parent_path') or ())

    if scope in {'database', 'server', 'resource'}:
        if scope == 'resource' and not _resource_exists(
            prepared, state.get('resource_id'), parent_path
        ):
            raise ProviderNavigatorError(
                'navigator resource is unavailable'
            )
        kinds = sorted({
            item['resource_kind'] for item in prepared
            if len(item['_navigator_path']) == len(parent_path) + 1 and
            tuple(item['_navigator_path'][:len(parent_path)]) == parent_path
        }, key=lambda value: kind_label(value).casefold())
        return [{
            'node_type': 'group',
            'label': kind_label(kind),
            'resource_kind': kind,
            'parent_path': list(parent_path),
            'has_children': True,
        } for kind in kinds]

    kind = state.get('resource_kind')
    if not isinstance(kind, str) or not kind:
        raise ProviderNavigatorError('navigator resource kind is required')
    values = []
    for item in prepared:
        path = tuple(item['_navigator_path'])
        if item['resource_kind'] != kind or len(path) != len(
            parent_path
        ) + 1 or path[:len(parent_path)] != parent_path:
            continue
        has_children = any(
            len(other['_navigator_path']) > len(path) and
            tuple(other['_navigator_path'][:len(path)]) == path
            for other in prepared
        )
        values.append({
            'node_type': 'resource',
            'label': item['display_name'],
            'resource_kind': kind,
            'resource_id': item['resource_id'],
            'display_path': list(path),
            'has_children': has_children,
            'resource': item['_source'],
        })
    return sorted(
        values,
        key=lambda item: (item['label'].casefold(), item['resource_id']),
    )


def _prepared_resources(resources, state):
    values = []
    for source in resources:
        if not isinstance(source, dict):
            raise ProviderNavigatorError('provider resource is invalid')
        resource_id = source.get('resource_id')
        kind = source.get('resource_kind')
        name = source.get('display_name')
        path = source.get('display_path')
        if not all(isinstance(item, str) and item for item in (
            resource_id, kind, name
        )) or not isinstance(path, list) or not all(
            isinstance(item, str) and item for item in path
        ):
            raise ProviderNavigatorError(
                'provider resource presentation is invalid'
            )
        values.append({
            'resource_id': resource_id,
            'resource_kind': kind,
            'display_name': name,
            'display_path': list(path),
            '_source': source,
        })

    prefix = _database_prefix(values, state)
    database_prefixes = [
        (tuple(item['display_path']), item['resource_kind'])
        for item in values
        if item['resource_kind'] in {'database', 'attached-database'} and
        item['display_path'] and tuple(item['display_path']) != tuple(prefix)
    ]
    prepared = []
    for item in values:
        path = item['display_path']
        if prefix:
            if path[:len(prefix)] != prefix:
                owner = next((kind for database_path, kind in
                              database_prefixes if tuple(
                                  path[:len(database_path)]
                              ) == database_path), None)
                if owner == 'database':
                    continue
            else:
                path = path[len(prefix):]
        if not path or item['resource_kind'] in {'server', 'database'}:
            continue
        prepared.append({**item, '_navigator_path': path})
    represented_paths = {
        tuple(item['_navigator_path']) for item in prepared
    }
    for item in prepared:
        path = tuple(item['_navigator_path'])
        if len(path) < 2 or path[:-1] in represented_paths:
            continue
        ancestors = [
            path[:index] for index in range(1, len(path) - 1)
            if path[:index] in represented_paths
        ]
        item['_navigator_path'] = [
            *(max(ancestors, key=len) if ancestors else ()), path[-1]
        ]
    return prepared


def _database_prefix(resources, state):
    if state.get('scope') == 'server':
        servers = [
            item['display_path'] for item in resources
            if item['resource_kind'] == 'server'
        ]
        return min(servers, key=len) if servers else []
    candidates = {
        value for value in (
            state.get('database'), state.get('display_name')
        ) if isinstance(value, str) and value
    }
    database = state.get('database')
    if isinstance(database, str) and database:
        candidates.add(PurePath(database).name)
    matches = [
        item for item in resources
        if item['resource_kind'] == 'database' and (
            item['display_name'] in candidates or
            (
                item['display_path'] and
                item['display_path'][-1] in candidates
            ) or
            _native_database_identity(item).intersection(candidates)
        )
    ]
    if not matches:
        return []
    prefix = min(
        (item['display_path'] for item in matches), key=len
    )
    # Some providers return endpoint-wide paths prefixed by the database;
    # others (including Firebird) return paths already relative to the
    # selected database.  A matching database resource alone is not proof
    # that its path is a prefix.  Strip it only when it actually owns at
    # least one longer provider path.
    if not any(
        item['resource_kind'] not in {'server', 'database'} and
        len(item['display_path']) > len(prefix) and
        item['display_path'][:len(prefix)] == prefix
        for item in resources
    ):
        return []
    return prefix


def _native_database_identity(item):
    """Return provider-declared database names used only for path ownership.

    Embedded providers may expose a native schema name such as ``main`` while
    the retained database target is a filesystem path.  The provider's native
    metadata is authoritative for joining those identities; the common
    navigator does not infer this relationship from an engine ID.
    """
    source = item.get('_source') or {}
    payloads = []
    native = source.get('native')
    if isinstance(native, dict):
        payloads.append(native)
    extensions = source.get('extensions')
    if isinstance(extensions, dict):
        for extension in extensions.values():
            if not isinstance(extension, dict):
                continue
            native = extension.get('native')
            if isinstance(native, dict):
                payloads.append(native)
    values = set()
    for payload in payloads:
        path = payload.get('path')
        if isinstance(path, str) and path:
            values.add(path)
            values.add(PurePath(path).name)
    return values


def _resource_exists(resources, resource_id, path):
    return any(
        item['resource_id'] == resource_id and
        tuple(item['_navigator_path']) == path
        for item in resources
    )


__all__ = (
    'ProviderNavigatorError', 'database_entries',
    'decode_navigator_state', 'encode_navigator_state', 'kind_label',
    'is_loopback_server', 'resource_children', 'server_display_name',
)
