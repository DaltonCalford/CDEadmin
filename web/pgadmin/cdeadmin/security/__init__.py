##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""CDEadmin secret, runtime identity, and mode-isolation controls."""

from .models import (
    CapabilitySnapshot,
    IsolationPolicyError,
    RuntimeIdentityClaim,
    RuntimeIdentityError,
    SecretAccessError,
    SecretReference,
    SecurityPolicyError,
)
from .redaction import REDACTED, SENSITIVE_KEYS, redact, redact_text
from .secrets import EndpointSecretService, SecretLease
from .service import IsolationKeyPolicy, RuntimeIdentityPolicy, SecurityService


APP_EXTENSION_KEY = 'cdeadmin_security_service'


def init_app(app) -> SecurityService:
    existing = app.extensions.get(APP_EXTENSION_KEY)
    if existing is not None:
        return existing
    service = SecurityService()
    app.extensions[APP_EXTENSION_KEY] = service
    return service


def service_for_app(app) -> SecurityService:
    try:
        return app.extensions[APP_EXTENSION_KEY]
    except (AttributeError, KeyError) as exc:
        raise SecurityPolicyError(
            'CDEadmin security service is not initialized'
        ) from exc


__all__ = (
    'CapabilitySnapshot',
    'EndpointSecretService',
    'IsolationKeyPolicy',
    'IsolationPolicyError',
    'REDACTED',
    'RuntimeIdentityClaim',
    'RuntimeIdentityError',
    'RuntimeIdentityPolicy',
    'SENSITIVE_KEYS',
    'SecretAccessError',
    'SecretLease',
    'SecretReference',
    'SecurityPolicyError',
    'SecurityService',
    'init_app',
    'redact',
    'redact_text',
    'service_for_app',
)
