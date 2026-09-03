##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Preserved PostgreSQL provider for CDEadmin.

Keep package import side-effect free so surface metadata can be audited
without importing Flask or initializing the legacy pgAdmin runtime.
"""


def __getattr__(name):
    if name in {'PostgreSQLProvider', 'create_provider'}:
        from .provider import PostgreSQLProvider, create_provider
        return {
            'PostgreSQLProvider': PostgreSQLProvider,
            'create_provider': create_provider,
        }[name]
    raise AttributeError(name)


__all__ = ('PostgreSQLProvider', 'create_provider')
