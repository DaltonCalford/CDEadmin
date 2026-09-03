"""Reusable visual-administration controls for native key/value engines."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import threading
import time
import uuid
from collections.abc import Mapping

from .native_distributed import NativeDistributedError


def _field(field_id, label, control='text', required=False, default=None,
           options=None, help_text=''):
    value = {
        'field_id': field_id, 'label': label, 'control': control,
        'required': required,
    }
    if default is not None:
        value['default'] = default
    if options is not None:
        value['options'] = [
            {'value': item, 'label': item.upper()} for item in options
        ]
    if help_text:
        value['help'] = help_text
    return value


def key_value_catalog(catalog, resource_kinds, provider_name):
    """Install binary-safe provider forms on admitted key/value objects."""
    result = copy.deepcopy(dict(catalog))
    result['key_identity'] = 'provider-issued-key-and-original-value-token'
    result['automatic_mutation_retry'] = False
    for resource in result.get('objects', []):
        if resource.get('resource_kind') not in resource_kinds:
            continue
        for operation in resource.get('operations', []):
            operation_id = operation.get('operation_id')
            if operation_id == 'insert':
                fields = [
                    _field('key', 'Key', required=True),
                    _field(
                        'key_encoding', 'Key encoding', 'select', False,
                        'utf8', ('utf8', 'base64'),
                    ),
                    _field('value', 'Value', required=True),
                    _field(
                        'value_encoding', 'Value encoding', 'select', False,
                        'utf8', ('utf8', 'base64'),
                    ),
                ]
            elif operation_id == 'update':
                fields = [
                    _field(
                        'selector', 'Provider row identity', 'json', True
                    ),
                    _field('value', 'Replacement value', required=True),
                    _field(
                        'value_encoding', 'Value encoding', 'select', False,
                        'utf8', ('utf8', 'base64'),
                    ),
                ]
            elif operation_id == 'delete':
                fields = [
                    _field(
                        'selector', 'Provider row identity', 'json', True
                    ),
                    _field('confirmation', 'Confirmation', required=True),
                ]
            else:
                continue
            operation['form'] = {
                'form_id': f'{provider_name}-{operation_id}-key-value',
                'title': f'{operation_id.title()} key/value entry',
                'fields': fields,
            }
    return result


def validate_key_value_request(request, resource_kinds):
    kind = request.get('resource_kind')
    operation = request.get('operation_id')
    draft = request.get('draft') or {}
    errors = []
    if kind not in resource_kinds or operation not in {
        'insert', 'update', 'delete',
    }:
        return {'errors': errors}
    if not isinstance(draft, Mapping):
        return {'errors': [{
            'field_id': None, 'code': 'type',
            'message': 'The key/value draft must be an object.',
        }]}
    if operation == 'insert':
        for field in ('key', 'value'):
            if not isinstance(draft.get(field), str):
                errors.append({
                    'field_id': field, 'code': 'type',
                    'message': f'{field.title()} must be text.',
                })
    elif operation == 'update' and not isinstance(
        draft.get('value'), str
    ):
        errors.append({
            'field_id': 'value', 'code': 'type',
            'message': 'Value must be text.',
        })
    if operation in {'update', 'delete'}:
        selector = draft.get('selector')
        if not isinstance(selector, Mapping) or not isinstance(
            selector.get('identity_token'), str
        ):
            errors.append({
                'field_id': 'selector',
                'code': 'provider_identity_required',
                'message': 'A provider-issued row identity is required.',
            })
    for field in ('key_encoding', 'value_encoding'):
        if field in draft and draft[field] not in {'utf8', 'base64'}:
            errors.append({
                'field_id': field, 'code': 'choice',
                'message': 'Encoding must be utf8 or base64.',
            })
    return {'errors': errors}


def decode_value(draft, field):
    value = draft.get(field)
    if not isinstance(value, str):
        raise NativeDistributedError(f'{field} must be text')
    encoding = draft.get(f'{field}_encoding', 'utf8')
    if encoding == 'utf8':
        return value.encode('utf-8')
    if encoding != 'base64':
        raise NativeDistributedError(f'{field} encoding is invalid')
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise NativeDistributedError(f'{field} is not valid base64') from exc


def display_value(value):
    raw = bytes(value)
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        text = None
    return text, base64.b64encode(raw).decode('ascii')


class KeyIdentityStore:
    """Bind one-use edit tokens to a route, target, key, and value."""

    MAX_IDENTITIES = 5000
    MAX_AGE_SECONDS = 600

    def __init__(self):
        self._identities = {}
        self._lock = threading.RLock()

    @staticmethod
    def _fingerprint(value):
        return hashlib.sha256(json.dumps(
            value, sort_keys=True, default=str, separators=(',', ':'),
        ).encode('utf-8')).hexdigest()

    @staticmethod
    def _safe_route(route):
        return {
            key: value for key, value in route.items()
            if key not in {'credential_reference_id', 'principal_reference'}
        }

    def issue(self, route, target, key, original):
        token = str(uuid.uuid4())
        identity = (
            self._fingerprint(self._safe_route(route)),
            self._fingerprint(target), bytes(key), bytes(original),
            time.monotonic(),
        )
        with self._lock:
            while len(self._identities) >= self.MAX_IDENTITIES:
                self._identities.pop(next(iter(self._identities)), None)
            self._identities[token] = identity
        return token

    def consume(self, route, target, selector):
        token = selector.get('identity_token') if isinstance(
            selector, Mapping
        ) else None
        if not isinstance(token, str) or not token:
            raise NativeDistributedError(
                'provider-issued row identity is required'
            )
        with self._lock:
            identity = self._identities.pop(token, None)
        if identity is None:
            raise NativeDistributedError('row identity is stale or invalid')
        route_id, target_id, key, original, issued = identity
        if time.monotonic() - issued > self.MAX_AGE_SECONDS:
            raise NativeDistributedError('row identity has expired')
        if route_id != self._fingerprint(self._safe_route(route)) or (
            target_id != self._fingerprint(target)
        ):
            raise NativeDistributedError(
                'row identity belongs to another route or target'
            )
        return key, original

    def clear(self):
        with self._lock:
            self._identities.clear()
