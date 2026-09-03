##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Encoding inside an already encrypted protected credential column.

The serialized value is never a route property. The whole bundle is encrypted
by pgAdmin's existing protected-column encryption before persistence.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from .models import SecretAccessError


CREDENTIAL_BUNDLE_PREFIX = 'CDEADMIN-CREDENTIAL-BUNDLE-V1:'


def encode_credential_bundle(values):
    """Serialize typed non-empty strings for protected-column encryption."""
    if not isinstance(values, Mapping) or not values:
        raise SecretAccessError('credential bundle must not be empty')
    normalized = {}
    for kind, value in values.items():
        if not isinstance(kind, str) or not kind.strip():
            raise SecretAccessError('credential kind must not be empty')
        if not isinstance(value, str) or not value:
            raise SecretAccessError(
                f'credential bundle value for {kind!r} must not be empty'
            )
        normalized[kind.strip()] = value
    return CREDENTIAL_BUNDLE_PREFIX + json.dumps(
        normalized, sort_keys=True, separators=(',', ':'),
    )


def decode_credential_bundle(value):
    """Decode a typed bundle; never guess a type for a legacy value."""
    text = _text(value)
    if not text.startswith(CREDENTIAL_BUNDLE_PREFIX):
        raise SecretAccessError('protected credential is not a typed bundle')
    try:
        result = json.loads(text[len(CREDENTIAL_BUNDLE_PREFIX):])
    except (TypeError, ValueError):
        raise SecretAccessError('protected credential bundle is invalid') \
            from None
    if not isinstance(result, dict) or not result or not all(
        isinstance(kind, str) and kind and
        isinstance(secret, str) and bool(secret)
        for kind, secret in result.items()
    ):
        raise SecretAccessError('protected credential bundle is invalid')
    return result


def credential_from_protected_value(value, kind, legacy_kind=None):
    """Select one credential while preserving explicit legacy compatibility."""
    if not isinstance(kind, str) or not kind.strip():
        raise SecretAccessError('credential kind must not be empty')
    text = _text(value)
    if text.startswith(CREDENTIAL_BUNDLE_PREFIX):
        values = decode_credential_bundle(text)
        try:
            return values[kind]
        except KeyError:
            raise SecretAccessError(
                'protected credential kind is unavailable'
            ) from None
    if legacy_kind is not None and kind == legacy_kind:
        return text
    raise SecretAccessError('legacy credential type does not match request')


def _text(value):
    if isinstance(value, bytes):
        try:
            value = value.decode('utf-8')
        except UnicodeDecodeError:
            raise SecretAccessError(
                'protected credential is not valid UTF-8'
            ) from None
    if not isinstance(value, str) or not value:
        raise SecretAccessError('protected credential is unavailable')
    return value


__all__ = (
    'CREDENTIAL_BUNDLE_PREFIX',
    'credential_from_protected_value',
    'decode_credential_bundle',
    'encode_credential_bundle',
)
