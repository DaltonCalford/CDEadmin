##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Endpoint route health and bounded pre-session failover selection."""

from __future__ import annotations

import threading
import time


class RouteSelectionError(ValueError):
    """An endpoint has no usable route."""


class RouteHealthRegistry:
    """Keep process-local observations without changing profile authority.

    Route health is advisory and deliberately short lived. Native drivers own
    established-session routing and reconnection. CDEadmin uses this registry
    only while choosing a route before a provider session exists.
    """

    def __init__(self, clock=None, base_backoff=1.0, max_backoff=30.0):
        if base_backoff <= 0 or max_backoff < base_backoff:
            raise RouteSelectionError('route backoff configuration is invalid')
        self._clock = clock or time.monotonic
        self._base_backoff = float(base_backoff)
        self._max_backoff = float(max_backoff)
        self._health = {}
        self._lock = threading.RLock()

    def candidates(self, endpoint_id, routes):
        """Return stable priority order, deferring routes in backoff."""
        ordered = sorted(routes, key=lambda item: (item.priority, item.id))
        if not ordered:
            raise RouteSelectionError('endpoint has no configured routes')
        now = self._clock()
        with self._lock:
            ready = []
            deferred = []
            for route in ordered:
                state = self._health.get((endpoint_id, route.id), {})
                target = ready if state.get('retry_at', 0) <= now else deferred
                target.append(route)
        # If every route is cooling down, retaining priority order still gives
        # callers a bounded recovery attempt instead of permanent lockout.
        return tuple(ready or deferred)

    def record_failure(self, endpoint_id, route_id):
        now = self._clock()
        with self._lock:
            previous = self._health.get((endpoint_id, route_id), {})
            failures = previous.get('consecutive_failures', 0) + 1
            backoff = min(
                self._max_backoff,
                self._base_backoff * (2 ** (failures - 1)),
            )
            self._health[(endpoint_id, route_id)] = {
                'consecutive_failures': failures,
                'last_failure_at': now,
                'retry_at': now + backoff,
            }

    def record_success(self, endpoint_id, route_id):
        now = self._clock()
        with self._lock:
            self._health[(endpoint_id, route_id)] = {
                'consecutive_failures': 0,
                'last_success_at': now,
                'retry_at': 0,
            }

    def snapshot(self, endpoint_id):
        """Return secret-free route observations for diagnostics and UI."""
        with self._lock:
            return {
                route_id: dict(state)
                for (observed_endpoint, route_id), state
                in self._health.items()
                if observed_endpoint == endpoint_id
            }

    def clear(self, endpoint_id, route_id=None):
        """Forget observations after persistent route mutation/removal."""
        with self._lock:
            keys = [
                key for key in self._health
                if key[0] == endpoint_id and (
                    route_id is None or key[1] == route_id
                )
            ]
            for key in keys:
                del self._health[key]


__all__ = ('RouteHealthRegistry', 'RouteSelectionError')
