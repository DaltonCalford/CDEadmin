##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Versioned authenticated encryption for delegated worker credentials."""

from __future__ import annotations

import base64
import json
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


KEY_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')


class WorkerKeyError(RuntimeError):
    """The worker encryption key authority is absent or invalid."""


class WorkerKeyRing:
    """Encrypt with one active key and retain old keys for rotation reads."""

    def __init__(self, keys=None, active_key_id=None):
        if keys is None:
            keys = {}
        if not isinstance(keys, dict) or len(keys) > 16:
            raise WorkerKeyError('worker key ring must be an object')
        self._keys = {}
        for key_id, encoded in keys.items():
            if (
                not isinstance(key_id, str) or
                not KEY_ID.fullmatch(key_id)
            ):
                raise WorkerKeyError('worker key ID is invalid')
            if not isinstance(encoded, str):
                raise WorkerKeyError('worker key must be base64 text')
            try:
                key = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                raise WorkerKeyError('worker key is not valid base64') \
                    from None
            if len(key) != 32:
                raise WorkerKeyError('worker key must contain 32 bytes')
            self._keys[key_id] = key
        if active_key_id is not None and active_key_id not in self._keys:
            raise WorkerKeyError('active worker key is unavailable')
        if self._keys and active_key_id is None:
            raise WorkerKeyError('active worker key ID is required')
        self.active_key_id = active_key_id

    @property
    def available(self):
        return self.active_key_id is not None

    @property
    def key_ids(self):
        return tuple(sorted(self._keys))

    def encrypt(self, value, aad):
        if not self.available:
            raise WorkerKeyError('worker key authority is not configured')
        if not isinstance(value, (bytes, bytearray, memoryview)) or not value:
            raise WorkerKeyError(
                'delegated credential must be non-empty bytes'
            )
        associated = self._aad(aad)
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._keys[self.active_key_id]).encrypt(
            nonce, bytes(value), associated
        )
        return json.dumps({
            'schema': 'cdeadmin.worker-envelope.v1',
            'key_id': self.active_key_id,
            'nonce': base64.b64encode(nonce).decode('ascii'),
            'ciphertext': base64.b64encode(ciphertext).decode('ascii'),
        }, sort_keys=True, separators=(',', ':'))

    def decrypt(self, envelope, aad):
        try:
            value = json.loads(envelope)
            if set(value) != {'schema', 'key_id', 'nonce', 'ciphertext'} or (
                value['schema'] != 'cdeadmin.worker-envelope.v1'
            ):
                raise ValueError
            key = self._keys[value['key_id']]
            nonce = base64.b64decode(value['nonce'], validate=True)
            ciphertext = base64.b64decode(
                value['ciphertext'], validate=True
            )
            if len(nonce) != 12:
                raise ValueError
            return AESGCM(key).decrypt(
                nonce, ciphertext, self._aad(aad)
            )
        except (KeyError, TypeError, ValueError):
            raise WorkerKeyError(
                'delegated credential envelope is unavailable or invalid'
            ) from None
        except Exception as exc:
            raise WorkerKeyError(
                'delegated credential authentication failed'
            ) from exc

    def rotate(self, envelope, aad):
        plaintext = bytearray(self.decrypt(envelope, aad))
        try:
            return self.encrypt(plaintext, aad)
        finally:
            for index in range(len(plaintext)):
                plaintext[index] = 0

    @staticmethod
    def _aad(value):
        if not isinstance(value, dict) or not value:
            raise WorkerKeyError('worker envelope scope is invalid')
        try:
            encoded = json.dumps(
                value, sort_keys=True, separators=(',', ':'),
                ensure_ascii=True,
            ).encode('ascii')
        except (TypeError, ValueError):
            raise WorkerKeyError('worker envelope scope is invalid') \
                from None
        return encoded
