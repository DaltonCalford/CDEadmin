##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Shared framing/auth/transport foundations for provider-owned clients."""

from .embedded import (
    BubblewrapSandbox,
    EmbeddedHelperHost,
    EmbeddedRuntimeError,
    EmbeddedRuntimeGrant,
    HelperInvocation,
    HelperResult,
    SandboxCapabilities,
)
from .models import (
    ProtocolSelection,
    TransportError,
    TransportFault,
    TransportIsolationError,
    TransportRequest,
    TransportResponse,
    TransportSelectionError,
    TransportUnavailableError,
)
from .registry import (
    EndpointFaultStore,
    ProtocolBoundary,
    ProtocolBoundaryRegistry,
    load_protocol_selections,
    load_selection_document,
    safe_selection_summary,
)


__all__ = (
    'BubblewrapSandbox',
    'EmbeddedHelperHost',
    'EmbeddedRuntimeError',
    'EmbeddedRuntimeGrant',
    'EndpointFaultStore',
    'HelperInvocation',
    'HelperResult',
    'ProtocolBoundary',
    'ProtocolBoundaryRegistry',
    'ProtocolSelection',
    'SandboxCapabilities',
    'TransportError',
    'TransportFault',
    'TransportIsolationError',
    'TransportRequest',
    'TransportResponse',
    'TransportSelectionError',
    'TransportUnavailableError',
    'load_protocol_selections',
    'load_selection_document',
    'safe_selection_summary',
)
