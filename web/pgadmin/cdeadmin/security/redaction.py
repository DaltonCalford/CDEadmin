##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Central redaction for CDEadmin DTOs, logs, results, and evidence."""

from __future__ import annotations

import copy
import json
import re
from typing import Mapping


REDACTED = '[REDACTED]'
SENSITIVE_KEYS = frozenset({
    'access_token', 'api_key', 'authorization', 'client_secret', 'cookie',
    'credential', 'credentials', 'passphrase', 'password', 'private_key',
    'refresh_token', 'session_token', 'secret', 'token', 'tunnel_password',
})
REFERENCE_KEYS = frozenset({
    'configuration_reference', 'evidence_reference', 'secret_reference',
})
SENSITIVE_SUFFIXES = (
    '_access_token', '_api_key', '_credential', '_credentials', '_password',
    '_private_key', '_refresh_token', '_secret', '_session_token', '_token',
)
_URI_USERINFO = re.compile(
    r'(?P<scheme>\b[a-z][a-z0-9+.-]{0,31}://)[^/@\s]+@', re.IGNORECASE
)
_ASSIGNMENT = re.compile(
    r'(?i)\b(password|passwd|passphrase|token|access_token|refresh_token|'
    r'api_key|client_secret|authorization)=([^&;\s]+)'
)
_BEARER = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+')


def _json_safe(value):
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return copy.deepcopy(value)


def is_sensitive_key(value, extra_keys=()):
    label = str(value).casefold()
    extras = {str(item).casefold() for item in extra_keys}
    if label in REFERENCE_KEYS:
        return label in extras
    return (
        label in SENSITIVE_KEYS or label in extras or
        label.endswith(SENSITIVE_SUFFIXES)
    )


def redact_text(value, secret_values=()):
    """Redact common credential forms and explicit canaries from text."""
    text = str(value)
    text = _URI_USERINFO.sub(r'\g<scheme>[REDACTED]@', text)
    text = _ASSIGNMENT.sub(lambda match: f'{match.group(1)}={REDACTED}', text)
    text = _BEARER.sub(f'Bearer {REDACTED}', text)
    for secret in secret_values:
        if isinstance(secret, bytes):
            try:
                secret = secret.decode('utf-8')
            except UnicodeDecodeError:
                continue
        if isinstance(secret, str) and secret:
            text = text.replace(secret, REDACTED)
    return text


def redact(value, extra_keys=(), secret_values=()):
    """Return a recursive, JSON-safe copy with sensitive values removed."""
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            label = str(key)
            if is_sensitive_key(label, extra_keys):
                result[label] = REDACTED
            else:
                result[label] = redact(item, extra_keys, secret_values)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item, extra_keys, secret_values) for item in value]
    if isinstance(value, str):
        return redact_text(value, secret_values)
    return _json_safe(value)
