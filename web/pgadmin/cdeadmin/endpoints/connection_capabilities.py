##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Versioned, fail-closed connection capability declarations.

The declaration describes CDEadmin's admitted connection surface. It is not
an inference from whichever keyword arguments a driver happens to accept.
"""

from __future__ import annotations

import copy


CONNECTION_CAPABILITY_CONTRACT_VERSION = '1.0'
CONNECTION_CAPABILITY_CATEGORIES = (
    'authentication',
    'tls',
    'client_certificates',
    'connection_profiles',
    'multiple_endpoints',
    'topology_discovery',
    'routing_failover',
    'session_defaults',
    'timeouts',
    'compression',
    'consistency_isolation',
    'pooling',
    'reconnection',
    'state_visibility',
)
CONNECTION_CAPABILITY_STATES = frozenset({
    'complete', 'partial', 'missing', 'not_applicable',
})


class ConnectionCapabilityError(ValueError):
    """A capability declaration is malformed or incomplete."""


def normalize_connection_capabilities(value):
    """Normalize one provider declaration without hiding omissions."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ConnectionCapabilityError(
            'connection capabilities must be an object'
        )
    version = value.get(
        'contract_version', CONNECTION_CAPABILITY_CONTRACT_VERSION
    )
    if version != CONNECTION_CAPABILITY_CONTRACT_VERSION:
        raise ConnectionCapabilityError(
            'connection capability contract version is unsupported'
        )
    declared = value.get('categories', {})
    if not isinstance(declared, dict):
        raise ConnectionCapabilityError(
            'connection capability categories must be an object'
        )
    unknown = sorted(set(declared) - set(CONNECTION_CAPABILITY_CATEGORIES))
    if unknown:
        raise ConnectionCapabilityError(
            'unknown connection capability categories: ' +
            ', '.join(unknown)
        )

    categories = {}
    for category in CONNECTION_CAPABILITY_CATEGORIES:
        item = declared.get(category)
        if item is None:
            categories[category] = {
                'state': 'missing',
                'features': [],
                'evidence': [],
                'reason': 'provider declaration is absent',
            }
            continue
        if not isinstance(item, dict):
            raise ConnectionCapabilityError(
                f'connection capability {category} must be an object'
            )
        state = item.get('state')
        if state not in CONNECTION_CAPABILITY_STATES:
            raise ConnectionCapabilityError(
                f'connection capability {category} state is invalid'
            )
        features = item.get('features', [])
        evidence = item.get('evidence', [])
        if not _string_list(features):
            raise ConnectionCapabilityError(
                f'connection capability {category} features are invalid'
            )
        if not _string_list(evidence):
            raise ConnectionCapabilityError(
                f'connection capability {category} evidence is invalid'
            )
        reason = item.get('reason')
        if reason is not None and (
            not isinstance(reason, str) or not reason.strip()
        ):
            raise ConnectionCapabilityError(
                f'connection capability {category} reason is invalid'
            )
        if state == 'complete' and (not features or not evidence):
            raise ConnectionCapabilityError(
                f'complete connection capability {category} requires '
                'features and evidence'
            )
        if state == 'not_applicable' and not reason:
            raise ConnectionCapabilityError(
                f'not-applicable connection capability {category} '
                'requires a reason'
            )
        categories[category] = {
            'state': state,
            'features': copy.deepcopy(features),
            'evidence': copy.deepcopy(evidence),
            **({'reason': reason.strip()} if reason else {}),
        }
    result = {
        'contract_version': version,
        'categories': categories,
    }
    result['complete'] = all(
        item['state'] in {'complete', 'not_applicable'}
        for item in categories.values()
    )
    return result


def assert_connection_capabilities_complete(value, profile_id='provider'):
    """Fail unless every category is implemented or justified as N/A."""
    normalized = normalize_connection_capabilities(value)
    incomplete = [
        name for name, item in normalized['categories'].items()
        if item['state'] not in {'complete', 'not_applicable'}
    ]
    if incomplete:
        raise ConnectionCapabilityError(
            f'{profile_id} has incomplete connection capabilities: ' +
            ', '.join(incomplete)
        )
    return normalized


def _string_list(value):
    return isinstance(value, list) and all(
        isinstance(item, str) and bool(item.strip()) for item in value
    )


__all__ = (
    'CONNECTION_CAPABILITY_CATEGORIES',
    'CONNECTION_CAPABILITY_CONTRACT_VERSION',
    'ConnectionCapabilityError',
    'assert_connection_capabilities_complete',
    'normalize_connection_capabilities',
)
