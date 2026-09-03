##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Runtime identity, destructive-action, and isolation-key policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from .models import (
    CapabilitySnapshot,
    IsolationPolicyError,
    MUTATION_CLASSES,
    RuntimeIdentityClaim,
    RuntimeIdentityError,
    SecurityPolicyError,
    required_string,
)
from .redaction import redact, redact_text
from .secrets import EndpointSecretService


PURPOSE_NAMESPACES = {
    'pool': 'pool_namespace',
    'session': 'session_namespace',
    'cache': 'cache_namespace',
    'diagnostic': 'diagnostic_namespace',
}


class RuntimeIdentityPolicy:
    """Prevent declared endpoints from silently changing runtime class."""

    def apply(self, context, claim):
        if not isinstance(claim, RuntimeIdentityClaim):
            raise RuntimeIdentityError('runtime identity claim is required')
        if claim.endpoint_id != context.endpoint_id:
            raise RuntimeIdentityError('runtime claim endpoint mismatch')
        if claim.endpoint_mode != context.mode:
            raise RuntimeIdentityError('runtime claim mode mismatch')
        if context.declared_runtime_family is not None and (
            claim.declared_runtime_family.casefold() !=
            context.declared_runtime_family.casefold()
        ):
            raise RuntimeIdentityError(
                'runtime claim changed the declared engine family'
            )
        if claim.verification_state == 'verified':
            self._verify_family(context, claim)
            if claim.evidence_reference is None:
                raise RuntimeIdentityError(
                    'verified runtime requires evidence'
                )
        elif claim.verified_runtime_family is not None:
            raise RuntimeIdentityError(
                'unverified runtime cannot publish verified identity'
            )
        return replace(
            context,
            declared_runtime_family=claim.declared_runtime_family,
            verified_runtime_family=claim.verified_runtime_family,
            verified_runtime_version=claim.verified_runtime_version,
            runtime_verification_state=claim.verification_state,
            runtime_evidence_reference=claim.evidence_reference,
            runtime_identity_generation=claim.generation,
        )

    @staticmethod
    def _verify_family(context, claim):
        if claim.verified_runtime_family is None:
            raise RuntimeIdentityError(
                'verified runtime family is missing'
            )
        family = claim.verified_runtime_family.casefold()
        if context.mode == 'legacy_native':
            if family != (
                claim.declared_runtime_family.casefold()
            ):
                raise RuntimeIdentityError(
                    'legacy-native target runtime does not match declaration'
                )
        elif context.mode == 'scratchbird_native':
            if family != 'scratchbird':
                raise RuntimeIdentityError(
                    'ScratchBird-native endpoint identity mismatch'
                )

    def authorize(self, context, mutation_class, authority_scope=None):
        if mutation_class not in MUTATION_CLASSES:
            raise SecurityPolicyError('mutation class is invalid')
        if mutation_class == 'destructive':
            self._require_verified(context)
        if authority_scope == 'scratchbird_native_control':
            if context.mode != 'scratchbird_native':
                raise RuntimeIdentityError(
                    'compatibility authentication grants no native authority'
                )
            self._require_verified(context)
        elif authority_scope is not None:
            raise SecurityPolicyError('authority scope is invalid')
        return True

    def _require_verified(self, context):
        if context.runtime_verification_state != 'verified':
            raise RuntimeIdentityError(
                'destructive authority requires verified runtime identity'
            )
        if not context.runtime_evidence_reference:
            raise RuntimeIdentityError(
                'verified runtime identity lacks evidence'
            )
        claim = RuntimeIdentityClaim(
            endpoint_id=context.endpoint_id,
            endpoint_mode=context.mode,
            declared_runtime_family=(
                context.declared_runtime_family or context.experience_family
            ),
            verification_state=context.runtime_verification_state,
            verified_runtime_family=context.verified_runtime_family,
            verified_runtime_version=context.verified_runtime_version,
            evidence_reference=context.runtime_evidence_reference,
            generation=context.runtime_identity_generation,
        )
        self._verify_family(context, claim)


class IsolationKeyPolicy:
    """Derive keys bound to endpoint, mode, principal, and credential."""

    @staticmethod
    def key(
        context,
        purpose,
        principal_id,
        credential_reference_id=None,
        generation=None,
    ):
        purpose = required_string(purpose, 'purpose')
        principal_id = required_string(principal_id, 'principal_id')
        namespace_field = PURPOSE_NAMESPACES.get(purpose)
        if namespace_field is None:
            raise IsolationPolicyError('isolation purpose is invalid')
        if purpose in {'pool', 'session'} and not credential_reference_id:
            raise IsolationPolicyError(
                'pool/session isolation requires credential reference'
            )
        payload = {
            'purpose': purpose,
            'namespace': getattr(context, namespace_field),
            'endpoint_id': context.endpoint_id,
            'endpoint_mode': context.mode,
            'provider_id': context.provider_id,
            'provider_version': context.provider_version,
            'profile_id': context.profile_id,
            'profile_version': context.profile_version,
            'target_adapter_id': context.target_adapter_id,
            'target_adapter_version': context.target_adapter_version,
            'runtime_state': context.runtime_verification_state,
            'verified_runtime_family': context.verified_runtime_family,
            'runtime_generation': context.runtime_identity_generation,
            'principal_id': principal_id,
            'credential_reference_id': credential_reference_id,
            'generation': generation,
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(',', ':')
        ).encode('utf-8')
        return f'cdeadmin:{purpose}:{hashlib.sha256(encoded).hexdigest()}'


class SecurityService:
    """Application-scoped facade for common CDEadmin security controls."""

    def __init__(self, secret_service=None):
        self.secrets = secret_service or EndpointSecretService()
        self.runtime_identity = RuntimeIdentityPolicy()
        self.isolation = IsolationKeyPolicy()

    def admit_runtime(self, context, claim):
        return self.runtime_identity.apply(context, claim)

    def authorize(self, context, mutation_class, authority_scope=None):
        return self.runtime_identity.authorize(
            context, mutation_class, authority_scope
        )

    def isolation_key(
        self, context, purpose, principal_id,
        credential_reference_id=None, generation=None,
    ):
        return self.isolation.key(
            context, purpose, principal_id,
            credential_reference_id, generation,
        )

    def capability_snapshot(
        self, context, generation, capability_ids, permissions
    ):
        runtime = {
            'endpoint_id': context.endpoint_id,
            'mode': context.mode,
            'state': context.runtime_verification_state,
            'family': context.verified_runtime_family,
            'version': context.verified_runtime_version,
            'evidence': context.runtime_evidence_reference,
            'generation': context.runtime_identity_generation,
        }
        digest = hashlib.sha256(json.dumps(
            runtime, sort_keys=True, separators=(',', ':')
        ).encode('utf-8')).hexdigest()
        return CapabilitySnapshot(
            context.endpoint_id,
            context.mode,
            generation,
            digest,
            frozenset(capability_ids),
            frozenset(permissions),
            {'runtime_verification_state': (
                context.runtime_verification_state
            )},
        )

    @staticmethod
    def safe_dto(value, extra_keys=()):
        return redact(value, extra_keys)

    @staticmethod
    def safe_diagnostic(value, secret_values=()):
        if isinstance(value, str):
            return redact_text(value, secret_values)
        return redact(value, secret_values=secret_values)

    @staticmethod
    def safe_telemetry(value, secret_values=()):
        return redact(value, secret_values=secret_values)

    @staticmethod
    def safe_evidence_export(value, secret_values=()):
        return redact(value, secret_values=secret_values)
