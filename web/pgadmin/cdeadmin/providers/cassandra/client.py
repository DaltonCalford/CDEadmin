##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Apache Cassandra binding to the shared CQL-native client foundation."""

from ..cql_native import (
    CassandraClient,
    CassandraClientError,
    CassandraDependencyError,
)


__all__ = (
    'CassandraClient',
    'CassandraClientError',
    'CassandraDependencyError',
)
