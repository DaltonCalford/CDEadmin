##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Load and validate the CDEadmin hard-fork product identity contract."""

from __future__ import annotations

import json
from pathlib import Path


DEFAULT_IDENTITY_PATH = Path(__file__).with_name('product_identity.json')
IDENTITY_VERSION = '1.1.0'
REQUIRED_DELIVERY_MODES = frozenset({
    'source', 'python', 'linux-deb', 'linux-rpm', 'linux-desktop',
    'macos', 'windows', 'container', 'helm', 'web-service',
})
REQUIRED_NAMESPACE_SCOPES = frozenset({
    'configuration', 'data', 'cache', 'logs', 'runtime', 'sessions',
    'cookie', 'database', 'environment', 'desktop_store', 'keyring',
})


class ProductIdentityError(ValueError):
    """Raised when a product identity cannot safely coexist with pgAdmin."""


def load_identity(path=DEFAULT_IDENTITY_PATH):
    """Load and validate a product identity JSON document."""
    identity_path = Path(path)
    try:
        identity = json.loads(identity_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductIdentityError(
            f'cannot load product identity {identity_path}: {exc}'
        ) from exc
    validate_identity(identity)
    return identity


def _require_string(mapping, key, location):
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProductIdentityError(
            f'{location}.{key} must be a non-empty string'
        )
    return value


def namespace_values(identity, product='cdeadmin'):
    """Return normalized namespace values for collision checks."""
    namespaces = identity['namespaces'][product]
    values = {}
    for platform, scopes in namespaces.items():
        if not isinstance(scopes, dict):
            raise ProductIdentityError(
                f'namespaces.{product}.{platform} must be an object'
            )
        for scope, value in scopes.items():
            if not isinstance(value, str) or not value.strip():
                raise ProductIdentityError(
                    f'namespaces.{product}.{platform}.{scope} must be '
                    'a non-empty string'
                )
            values[f'{platform}.{scope}'] = value.strip()
    return values


def namespace_collisions(identity):
    """Report exact, case-insensitive CDEadmin/pgAdmin namespace collisions."""
    cdeadmin = namespace_values(identity, 'cdeadmin')
    upstream = namespace_values(identity, 'pgadmin')
    upstream_index = {}
    for location, value in upstream.items():
        upstream_index.setdefault(value.casefold(), []).append(location)
    collisions = []
    for location, value in cdeadmin.items():
        matches = upstream_index.get(value.casefold(), ())
        for upstream_location in matches:
            collisions.append({
                'cdeadmin': location,
                'pgadmin': upstream_location,
                'value': value,
            })
    return collisions


def validate_identity(identity):
    """Validate product, package, update, signing and namespace separation."""
    if not isinstance(identity, dict):
        raise ProductIdentityError('product identity must be an object')
    if identity.get('identity_version') != IDENTITY_VERSION:
        raise ProductIdentityError(
            f'identity_version must be {IDENTITY_VERSION}'
        )
    product = identity.get('product')
    if not isinstance(product, dict):
        raise ProductIdentityError('product must be an object')
    if _require_string(product, 'display_name', 'product') != 'CDEadmin':
        raise ProductIdentityError('product.display_name must be CDEadmin')
    if _require_string(product, 'short_name', 'product') != 'cdeadmin':
        raise ProductIdentityError('product.short_name must be cdeadmin')
    if product.get('identity_status') != 'hard-fork':
        raise ProductIdentityError(
            'product.identity_status must identify an independent hard fork'
        )
    _require_string(product, 'version', 'product')
    forked_from = product.get('forked_from')
    if not isinstance(forked_from, dict) or \
            _require_string(forked_from, 'product', 'product.forked_from') \
            != 'pgAdmin 4' or \
            _require_string(forked_from, 'version', 'product.forked_from') \
            != '9.17' or \
            _require_string(forked_from, 'relationship',
                            'product.forked_from') != 'independent hard fork':
        raise ProductIdentityError(
            'product.forked_from must identify pgAdmin 4 9.17 as the '
            'independent hard-fork base'
        )
    if product.get('release_ready') is not False:
        raise ProductIdentityError(
            'CDEadmin cannot be release-ready before release approvals'
        )

    namespaces = identity.get('namespaces')
    if not isinstance(namespaces, dict) or set(namespaces) != {
            'cdeadmin', 'pgadmin'}:
        raise ProductIdentityError(
            'namespaces must contain only cdeadmin and pgadmin maps'
        )
    cdeadmin_values = namespace_values(identity, 'cdeadmin')
    namespace_values(identity, 'pgadmin')
    common_scopes = {
        location.split('.', 1)[1] for location in cdeadmin_values
        if location.startswith('common.')
    }
    missing_scopes = sorted(REQUIRED_NAMESPACE_SCOPES - common_scopes)
    if missing_scopes:
        raise ProductIdentityError(
            f'missing common namespace scopes: {missing_scopes!r}'
        )
    collisions = namespace_collisions(identity)
    if collisions:
        raise ProductIdentityError(
            f'CDEadmin namespaces collide with pgAdmin: {collisions!r}'
        )

    packaging = identity.get('packaging')
    if not isinstance(packaging, dict):
        raise ProductIdentityError('packaging must be an object')
    modes = set(packaging.get('selected_delivery_modes', ()))
    missing_modes = sorted(REQUIRED_DELIVERY_MODES - modes)
    if missing_modes:
        raise ProductIdentityError(
            f'missing selected delivery modes: {missing_modes!r}'
        )
    identifiers = packaging.get('identifiers')
    if not isinstance(identifiers, dict):
        raise ProductIdentityError('packaging.identifiers must be an object')
    for key, value in identifiers.items():
        _require_string(identifiers, key, 'packaging.identifiers')
        if value.casefold() in {'pgadmin', 'pgadmin4', 'pgadmin 4'}:
            raise ProductIdentityError(
                f'packaging identifier {key!r} reuses pgAdmin identity'
            )

    update = identity.get('update_channel')
    if not isinstance(update, dict):
        raise ProductIdentityError('update_channel must be an object')
    if update.get('enabled') is not False:
        raise ProductIdentityError(
            'CDEadmin update channel must be disabled until release approval'
        )
    if _require_string(update, 'channel_id', 'update_channel').casefold() \
            == 'pgadmin4':
        raise ProductIdentityError('update channel reuses pgAdmin channel')
    if _require_string(update, 'artifact_prefix', 'update_channel') \
            .casefold().startswith('pgadmin4'):
        raise ProductIdentityError('update artifacts reuse pgAdmin prefix')
    if update.get('feed_url') not in (None, ''):
        raise ProductIdentityError(
            'update feed URL must remain unassigned until product approval'
        )

    signing = identity.get('signing')
    if not isinstance(signing, dict) or not signing:
        raise ProductIdentityError('signing must define delivery families')
    for family, record in signing.items():
        if not isinstance(record, dict):
            raise ProductIdentityError(f'signing.{family} must be an object')
        if record.get('status') != 'unassigned':
            raise ProductIdentityError(
                f'signing.{family} must remain unassigned'
            )
        if record.get('reuse_upstream_key') is not False:
            raise ProductIdentityError(
                f'signing.{family} must prohibit upstream-key reuse'
            )
    return identity
