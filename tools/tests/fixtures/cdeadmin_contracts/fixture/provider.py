"""Network-incapable provider used only by CDEadmin contract tests."""

from __future__ import annotations

import copy


NETWORK_CAPABLE = False
PRODUCTION_REGISTRATION = False


class NonOperationalFixtureProvider:
    """Return defensive DTO copies without opening any external resource."""

    __slots__ = ()

    @staticmethod
    def validate_endpoint(request):
        return copy.deepcopy(request)

    @staticmethod
    def discover_endpoint(request):
        return copy.deepcopy(request)


def create_provider():
    return NonOperationalFixtureProvider()
