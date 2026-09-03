##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Compatibility facade from existing integer IDs to endpoint identity."""

from __future__ import annotations

from pgadmin.model import EndpointProfile, SharedServer


def endpoint_profile_for_legacy(
    server_id: int,
    shared_server_id: int | None = None,
    user_id: int | None = None,
) -> EndpointProfile | None:
    """Resolve additive endpoint metadata without changing existing routes."""
    query = EndpointProfile.query
    if user_id is not None:
        query = query.filter(EndpointProfile.user_id == user_id)
    if shared_server_id is not None:
        return query.join(
            SharedServer,
            EndpointProfile.legacy_shared_server_id == SharedServer.id,
        ).filter(
            EndpointProfile.legacy_shared_server_id == shared_server_id,
            SharedServer.osid == server_id,
        ).first()
    return query.filter(EndpointProfile.legacy_server_id == server_id).first()


def endpoint_identity_for_legacy(
    server_id: int,
    shared_server_id: int | None = None,
    user_id: int | None = None,
) -> dict | None:
    """Return the endpoint fields needed by future provider resolution."""
    endpoint = endpoint_profile_for_legacy(
        server_id, shared_server_id, user_id
    )
    if endpoint is None:
        return None
    runtime = endpoint.runtime_identity
    return {
        'endpoint_id': endpoint.id,
        'endpoint_mode': endpoint.endpoint_mode,
        'experience_family': endpoint.experience_family,
        'provider_id': endpoint.provider_id,
        'provider_version': endpoint.provider_version,
        'profile_id': endpoint.profile_id,
        'profile_version': endpoint.profile_version,
        'target_adapter_id': endpoint.target_adapter_id,
        'target_adapter_version': endpoint.target_adapter_version,
        'pool_namespace': endpoint.pool_namespace,
        'session_namespace': endpoint.session_namespace,
        'cache_namespace': endpoint.cache_namespace,
        'diagnostic_namespace': endpoint.diagnostic_namespace,
        'declared_runtime_family': (
            runtime.declared_runtime_family if runtime else None
        ),
        'verified_runtime_family': (
            runtime.verified_runtime_family if runtime else None
        ),
        'verified_runtime_version': (
            runtime.verified_runtime_version if runtime else None
        ),
        'runtime_verification_state': (
            runtime.verification_state if runtime else 'unverified'
        ),
        'runtime_evidence_reference': (
            runtime.verification_evidence_reference if runtime else None
        ),
    }
