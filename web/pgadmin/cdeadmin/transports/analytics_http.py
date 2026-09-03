##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Bounded HTTP mechanics shared by HTTP-based analytic providers.

This module deliberately knows nothing about query, catalog, consistency, or
retry semantics.  Those remain owned by the provider using the transport.
"""

from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import json
import re
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from pgadmin.cdeadmin.sdk import PilotProviderError

import urllib3


MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 2 * 1024 * 1024
_HOST = re.compile(
    r'^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?'
    r'|\[[0-9A-Fa-f:.]+\])$'
)


class AnalyticHTTPError(PilotProviderError):
    """A bounded analytic HTTP request failed safely."""

    def __init__(self, message, status=None, native_payload=None):
        super().__init__(message)
        self.status = status
        self.native_payload = native_payload


class AnalyticHTTPUnknownOutcomeError(AnalyticHTTPError):
    """A mutating request may have reached the target engine."""

    outcome = 'unknown_requires_observation'
    retryable = False


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    body: bytes
    headers: Mapping[str, Any]

    def json(self):
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AnalyticHTTPError(
                'target returned an invalid JSON response'
            ) from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def required_text(value, label, maximum=4096):
    if not isinstance(value, str) or not value.strip():
        raise AnalyticHTTPError(f'{label} must not be empty')
    value = value.strip()
    if len(value.encode('utf-8')) > maximum or any(
        ord(character) < 32 for character in value
    ):
        raise AnalyticHTTPError(f'{label} is invalid')
    return value


def bounded_integer(value, label, default, minimum, maximum):
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, int):
        raise AnalyticHTTPError(f'{label} must be an integer')
    if not minimum <= value <= maximum:
        raise AnalyticHTTPError(f'{label} is outside the admitted range')
    return value


def normalize_http_route(request, *, default_port, default_auth='none',
                         extra_fields=()):
    if not isinstance(request, Mapping):
        raise AnalyticHTTPError('request must be an object')
    supplied = request.get('route')
    if not isinstance(supplied, Mapping):
        raise AnalyticHTTPError('route must be an object')
    route = copy.deepcopy(dict(supplied))
    forbidden = {'password', 'secret', 'token', 'api_key', 'credential'}
    if forbidden.intersection(key.casefold() for key in route):
        raise AnalyticHTTPError('inline credentials are forbidden')
    admitted = {
        'route_id', 'host', 'port', 'tls_mode', 'tls_ca_file',
        'tls_certificate_file', 'tls_key_file', 'connect_timeout',
        'statement_timeout', 'credential_reference_id',
        'credential_references', 'credential_kinds',
        'principal_reference', 'user', 'username', 'auth_kind',
        'credential_kind', 'aws_access_key_id', 'aws_region',
        'aws_service', 'api_key_header', 'http_compression',
        'pool_max_size', 'pool_block', 'read_only', *extra_fields,
    }
    unknown = sorted(set(route).difference(admitted))
    if unknown:
        raise AnalyticHTTPError(
            f'route contains unknown fields: {unknown}'
        )
    result = {
        'route_id': required_text(route.get('route_id', 'direct'), 'route ID'),
        'host': required_text(route.get('host'), 'host', 255),
        'port': bounded_integer(
            route.get('port'), 'port', default_port, 1, 65535
        ),
        'tls_mode': route.get('tls_mode', 'disable'),
        'connect_timeout': bounded_integer(
            route.get('connect_timeout'), 'connect timeout', 10, 1, 120
        ),
        'statement_timeout': bounded_integer(
            route.get('statement_timeout'), 'statement timeout', 30, 1, 3600
        ),
        'auth_kind': route.get('auth_kind', default_auth),
        'credential_kind': route.get('credential_kind'),
        'username': route.get('username') or route.get('user'),
        'read_only': route.get('read_only', False),
        'http_compression': route.get('http_compression', 'none'),
        'pool_max_size': bounded_integer(
            route.get('pool_max_size'), 'HTTP pool maximum size', 10, 1, 1000
        ),
        'pool_block': route.get('pool_block', True),
    }
    if not _HOST.fullmatch(result['host']):
        raise AnalyticHTTPError('host is invalid')
    if result['tls_mode'] not in {
        'disable', 'require', 'verify-ca', 'verify-full'
    }:
        raise AnalyticHTTPError('TLS mode is invalid')
    if result['auth_kind'] not in {
        'none', 'bearer', 'basic', 'clickhouse-basic', 'api-key',
        'aws-sigv4'
    }:
        raise AnalyticHTTPError('authentication kind is invalid')
    expected_credential_kind = {
        'none': None,
        'bearer': 'api_token',
        'basic': 'database_password',
        'clickhouse-basic': 'database_password',
        'api-key': 'api_key',
        'aws-sigv4': 'cloud_secret_access_key',
    }[result['auth_kind']]
    if result['credential_kind'] is None:
        result['credential_kind'] = expected_credential_kind
    if result['credential_kind'] != expected_credential_kind:
        raise AnalyticHTTPError(
            'credential kind does not match authentication kind'
        )
    if not isinstance(result['read_only'], bool):
        raise AnalyticHTTPError('read_only must be true or false')
    if not isinstance(result['pool_block'], bool):
        raise AnalyticHTTPError('HTTP pool blocking policy must be boolean')
    if result['http_compression'] not in {'none', 'gzip'}:
        raise AnalyticHTTPError('HTTP compression mode is invalid')
    for field in (
        'tls_ca_file', 'tls_certificate_file', 'tls_key_file',
        'credential_reference_id', 'principal_reference', 'username',
        'aws_access_key_id', 'aws_region', 'aws_service', 'api_key_header',
    ):
        if field in route and route[field] is not None:
            result[field] = required_text(route[field], field)
    for field in extra_fields:
        if field in route and route[field] is not None:
            result[field] = copy.deepcopy(route[field])
    if result['tls_mode'] in {'verify-ca', 'verify-full'} and not result.get(
        'tls_ca_file'
    ):
        raise AnalyticHTTPError('verified TLS requires a CA file')
    if bool(result.get('tls_certificate_file')) != bool(
        result.get('tls_key_file')
    ):
        raise AnalyticHTTPError(
            'client certificate and key must be configured together'
        )
    if result.get('username') is not None:
        result['username'] = required_text(
            result['username'], 'username', 255
        )
    else:
        result.pop('username')
    if result['auth_kind'] in {'basic', 'clickhouse-basic'} and not result.get(
        'username'
    ):
        raise AnalyticHTTPError('basic authentication requires a username')
    references = route.get('credential_references') or {}
    if not isinstance(references, Mapping) or not all(
        isinstance(kind, str) and kind.strip() and
        isinstance(reference, str) and reference.strip()
        for kind, reference in references.items()
    ):
        raise AnalyticHTTPError('credential references must be a text map')
    references = dict(references)
    if result.get('credential_reference_id'):
        references.setdefault(
            result.get('credential_kind'), result['credential_reference_id']
        )
    allowed_kinds = {
        kind for kind in (
            expected_credential_kind, 'tls_private_key_password',
            'cloud_session_token' if result['auth_kind'] == 'aws-sigv4'
            else None,
        ) if kind is not None
    }
    unknown_kinds = sorted(set(references) - allowed_kinds)
    if unknown_kinds:
        raise AnalyticHTTPError(
            'credential kinds are not valid for the selected HTTP '
            'authentication mode: ' + ', '.join(unknown_kinds)
        )
    if result['auth_kind'] != 'none':
        if expected_credential_kind not in references:
            raise AnalyticHTTPError(
                'authentication requires a credential reference'
            )
    if references and not result.get('principal_reference'):
        raise AnalyticHTTPError(
            'credential references require a principal reference'
        )
    if result['auth_kind'] == 'aws-sigv4':
        for field in ('aws_access_key_id', 'aws_region'):
            if not result.get(field):
                raise AnalyticHTTPError(
                    f'AWS SigV4 authentication requires {field}'
                )
        result.setdefault('aws_service', 'es')
    if result['auth_kind'] == 'api-key':
        result.setdefault('api_key_header', 'Authorization')
    result['credential_references'] = references
    return result


class BoundedJSONHTTPTransport:
    """No-redirect, size-limited HTTP/JSON transport with leased secrets."""

    def __init__(self, secret_acquirer=None, urlopen=None,
                 max_response_bytes=MAX_RESPONSE_BYTES):
        self.secret_acquirer = secret_acquirer
        self._urlopen = urlopen
        self.max_response_bytes = max_response_bytes
        self._pools = {}
        self._pool_lock = threading.RLock()

    @staticmethod
    def _ssl_context(route, secrets=None):
        if route['tls_mode'] == 'disable':
            return None
        if route['tls_mode'] == 'require':
            context = ssl._create_unverified_context()
        else:
            context = ssl.create_default_context(cafile=route['tls_ca_file'])
            context.check_hostname = route['tls_mode'] == 'verify-full'
        if route.get('tls_certificate_file'):
            context.load_cert_chain(
                route['tls_certificate_file'], route['tls_key_file'],
                password=(secrets or {}).get('tls_private_key_password'),
            )
        return context

    def _open(self, request, timeout, context):
        handlers = [_NoRedirect()]
        if context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=context))
        return urllib.request.build_opener(*handlers).open(
            request, timeout=timeout
        )

    def _pool(self, route, secrets):
        identity = {
            key: route.get(key) for key in (
                'host', 'port', 'tls_mode', 'tls_ca_file',
                'tls_certificate_file', 'tls_key_file', 'pool_max_size',
                'pool_block',
            )
        }
        key = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(',', ':'), default=str
        ).encode('utf-8')).hexdigest()
        with self._pool_lock:
            pool = self._pools.get(key)
            if pool is None:
                pool = urllib3.PoolManager(
                    num_pools=8,
                    maxsize=route['pool_max_size'],
                    block=route['pool_block'],
                    ssl_context=self._ssl_context(route, secrets),
                )
                self._pools[key] = pool
            return pool

    def close(self):
        with self._pool_lock:
            pools, self._pools = tuple(self._pools.values()), {}
        for pool in pools:
            pool.clear()

    def _pooled_request(self, route, method, url, body, headers, secrets):
        timeout = urllib3.Timeout(
            connect=route['connect_timeout'],
            read=route['statement_timeout'],
        )
        response = self._pool(route, secrets).request(
            method, url, body=body, headers=headers, timeout=timeout,
            retries=False, redirect=False, preload_content=False,
        )
        try:
            payload = response.read(self.max_response_bytes + 1)
            response_headers = response.headers
            status = int(response.status)
        finally:
            response.release_conn()
        if len(payload) > self.max_response_bytes:
            raise AnalyticHTTPError('response exceeds the admitted size')
        if status >= 400:
            raise AnalyticHTTPError(
                payload.decode('utf-8', 'replace')[:2048] or
                f'target returned HTTP {status}',
                status=status, native_payload=payload,
            )
        return payload, status, response_headers

    def _with_secrets(self, route, callback):
        references = dict(route.get('credential_references') or {})
        if not references:
            return callback({})
        if not callable(self.secret_acquirer):
            raise AnalyticHTTPError('secret acquisition is unavailable')
        bindings = sorted(references.items())

        def acquire(index, values):
            if index == len(bindings):
                return callback(values)
            kind, reference = bindings[index]
            lease = self.secret_acquirer(
                reference, route['principal_reference'], 'connect', kind
            )
            with lease:
                return lease.use(lambda view: acquire(index + 1, {
                    **values, kind: bytes(view).decode('utf-8'),
                }))

        return acquire(0, {})

    @staticmethod
    def _authorize(route, method, url, body, headers, secrets):
        auth_kind = route['auth_kind']
        if auth_kind == 'bearer':
            headers['Authorization'] = 'Bearer ' + secrets['api_token']
        elif auth_kind == 'basic':
            encoded = base64.b64encode(
                f'{route["username"]}:{secrets["database_password"]}'.encode(
                    'utf-8'
                )
            ).decode('ascii')
            headers['Authorization'] = f'Basic {encoded}'
        elif auth_kind == 'clickhouse-basic':
            headers['X-ClickHouse-User'] = route['username']
            headers['X-ClickHouse-Key'] = secrets['database_password']
        elif auth_kind == 'api-key':
            header = route.get('api_key_header', 'Authorization')
            if any(character in header for character in '\r\n\x00'):
                raise AnalyticHTTPError('API key header name is invalid')
            value = secrets['api_key']
            headers[header] = (
                f'ApiKey {value}' if header.casefold() == 'authorization'
                else value
            )
        elif auth_kind == 'aws-sigv4':
            try:
                from botocore.auth import SigV4Auth
                from botocore.awsrequest import AWSRequest
                from botocore.credentials import Credentials
            except (ImportError, ModuleNotFoundError) as exc:
                raise AnalyticHTTPError(
                    'AWS SigV4 authentication dependency is unavailable'
                ) from exc
            credentials = Credentials(
                route['aws_access_key_id'],
                secrets['cloud_secret_access_key'],
                secrets.get('cloud_session_token'),
            )
            aws_request = AWSRequest(
                method=method, url=url, data=body, headers=headers
            )
            SigV4Auth(
                credentials, route.get('aws_service', 'es'),
                route['aws_region'],
            ).add_auth(aws_request)
            headers.clear()
            headers.update(dict(aws_request.headers.items()))

    def request(self, route, path, *, method='GET', query=None,
                json_body=None, body=None, headers=None, mutating=False):
        if not isinstance(path, str) or not path.startswith('/') or any(
            character in path for character in ('\r', '\n', '\x00')
        ) or '://' in path:
            raise AnalyticHTTPError('HTTP path is invalid')
        method = required_text(method, 'HTTP method', 12).upper()
        if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'}:
            raise AnalyticHTTPError('HTTP method is not admitted')
        if json_body is not None and body is not None:
            raise AnalyticHTTPError('request body encoding is ambiguous')
        if json_body is not None:
            try:
                body = json.dumps(
                    json_body, ensure_ascii=False, separators=(',', ':')
                ).encode('utf-8')
            except (TypeError, ValueError) as exc:
                raise AnalyticHTTPError('request JSON is invalid') from exc
        if isinstance(body, str):
            body = body.encode('utf-8')
        if body is not None and not isinstance(body, bytes):
            raise AnalyticHTTPError('request body must be bytes')
        if body is not None and len(body) > MAX_REQUEST_BYTES:
            raise AnalyticHTTPError('request exceeds the admitted size')
        if query is not None and not isinstance(query, Mapping):
            raise AnalyticHTTPError('query parameters must be an object')
        encoded_query = urllib.parse.urlencode(
            [(str(key), str(value)) for key, value in (query or {}).items()
             if value is not None], doseq=True
        )
        scheme = 'http' if route['tls_mode'] == 'disable' else 'https'
        url = f'{scheme}://{route["host"]}:{route["port"]}{path}'
        if encoded_query:
            url += '?' + encoded_query
        request_headers = {'Accept': 'application/json'}
        request_headers.update(dict(headers or {}))
        if json_body is not None:
            request_headers.setdefault('Content-Type', 'application/json')

        if route.get('http_compression') == 'gzip':
            request_headers['Accept-Encoding'] = 'gzip'
            if body:
                body = gzip.compress(body)
                request_headers['Content-Encoding'] = 'gzip'

        def perform(secrets):
            self._authorize(
                route, method, url, body, request_headers, secrets
            )
            request = urllib.request.Request(
                url, data=body, headers=request_headers, method=method
            )
            opener = self._urlopen or self._open
            timeout = route['connect_timeout'] + route['statement_timeout']
            try:
                if self._urlopen is None:
                    payload, status, response_headers = self._pooled_request(
                        route, method, url, body, request_headers, secrets
                    )
                else:
                    response = opener(
                        request, timeout, self._ssl_context(route, secrets)
                    )
                    with response:
                        payload = response.read(self.max_response_bytes + 1)
                        status = int(getattr(response, 'status', 200))
                        response_headers = getattr(response, 'headers', {})
                header = getattr(response_headers, 'get', lambda *_: None)
                if str(header('Content-Encoding') or '').casefold() == 'gzip':
                    try:
                        payload = gzip.decompress(payload)
                    except (OSError, EOFError) as exc:
                        raise AnalyticHTTPError(
                            'target returned invalid gzip content'
                        ) from exc
                if len(payload) > self.max_response_bytes:
                    raise AnalyticHTTPError(
                        'response exceeds the admitted size'
                    )
                return HTTPResponse(status, payload, response_headers)
            except urllib.error.HTTPError as exc:
                payload = exc.read(min(self.max_response_bytes, 65536))
                message = payload.decode('utf-8', 'replace')[:2048]
                raise AnalyticHTTPError(
                    message or f'target returned HTTP {exc.code}',
                    status=exc.code, native_payload=payload,
                ) from None
            except AnalyticHTTPError:
                raise
            except Exception as exc:
                if mutating:
                    raise AnalyticHTTPUnknownOutcomeError(
                        'mutation outcome is unknown; observe target state '
                        f'before retry ({type(exc).__name__})'
                    ) from None
                raise AnalyticHTTPError(
                    f'HTTP request failed ({type(exc).__name__})'
                ) from None

        return self._with_secrets(route, perform)
