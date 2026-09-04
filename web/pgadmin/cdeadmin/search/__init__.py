##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Shared provider-neutral search administration clients."""

from .opensearch import (
    OpenSearchClient, OpenSearchClientError, OpenSearchUnknownOutcomeError,
)


__all__ = (
    'OpenSearchClient', 'OpenSearchClientError',
    'OpenSearchUnknownOutcomeError',
)
