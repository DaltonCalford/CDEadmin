##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Restart-safe operation bus stores with atomic JSON persistence."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from pathlib import Path

from .models import OperationBusError


STATE_VERSION = 1


def empty_state():
    return {
        'state_version': STATE_VERSION,
        'operations': {},
        'events': {},
        'evidence': {},
        'idempotency': {},
        'provider_audit': {},
    }


def _validate_state(value):
    if not isinstance(value, dict):
        raise OperationBusError('operation store state must be an object')
    if value.get('state_version') != STATE_VERSION:
        raise OperationBusError('unsupported operation store state version')
    for name in (
        'operations', 'events', 'evidence', 'idempotency', 'provider_audit',
    ):
        if not isinstance(value.get(name), dict):
            raise OperationBusError(
                f'operation store field {name!r} must be an object'
            )


class MemoryOperationStore:
    """Thread-safe copy-on-write store reusable across service restarts."""

    def __init__(self, state=None):
        self._lock = threading.RLock()
        initial = state if state is not None else empty_state()
        self._state = copy.deepcopy(initial)
        self._state.setdefault('provider_audit', {})
        _validate_state(self._state)

    def read(self):
        with self._lock:
            return copy.deepcopy(self._state)

    def change(self, callback):
        """Atomically apply a callback to a defensive candidate state."""
        with self._lock:
            candidate = copy.deepcopy(self._state)
            result = callback(candidate)
            _validate_state(candidate)
            self._commit(candidate)
            return copy.deepcopy(result)

    def _commit(self, candidate):
        self._state = candidate

    def export_state(self):
        return self.read()


class JsonOperationStore(MemoryOperationStore):
    """Atomic local JSON store for restart/replay state.

    The location is an application configuration choice. Stored operation
    payloads have already passed through bus redaction; evidence content is
    never persisted here, only its authorized descriptor.
    """

    def __init__(self, path):
        self.path = Path(path)
        state = None
        if self.path.exists():
            try:
                state = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise OperationBusError(
                    f'operation store cannot be loaded: {exc}'
                ) from exc
        super().__init__(state)

    def _commit(self, candidate):
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=parent,
                prefix=f'.{self.path.name}.', delete=False,
            ) as stream:
                temporary = Path(stream.name)
                os.chmod(temporary, 0o600)
                json.dump(
                    candidate, stream, sort_keys=True,
                    separators=(',', ':'),
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass
            raise OperationBusError(
                f'operation store cannot be persisted: {exc}'
            ) from exc
        self._state = candidate
