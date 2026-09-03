##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""XTDB 2.1 bitemporal document/relational semantic provider."""

from pgadmin.cdeadmin.sdk import ActualEnginePilotProvider, PilotProfile

from .client import XTDBClient


PROFILE = PilotProfile(
    provider_id='org.cdeadmin.xtdb',
    profile_id='xtdb-native',
    engine_id='xtdb',
    engine_name='XTDB',
    exact_version='2.1.0',
    protocol_id='postgresql_wire',
    model_family='bitemporal-document-relational',
    language_profile='xtdb-sql-2.1',
    language_name='XTDB SQL 2.1',
    transaction_model='xtdb-bitemporal-native-outcome',
    result_kind='document',
    resource_kinds=(
        'cluster', 'node', 'database', 'schema', 'table', 'column',
        'document', 'entity', 'valid-time', 'system-time',
        'transaction', 'transaction-log', 'index', 'module', 'user',
        'role', 'privilege', 'metric', 'health', 'snapshot', 'compactor',
    ),
    admin_tools=('psql', 'healthz', 'finish-block'),
    required_permissions=('network', 'secret_read'),
    language_mime_type='text/x-sql',
    result_renderer_kind='bitemporal-document',
    result_renderer_id='cdeadmin.result.bitemporal-document.inspector',
    result_component_reference='cdeadmin/results/BitemporalDocumentView',
    result_records_field='rows',
    result_export_formats=('json', 'jsonl', 'csv'),
    result_worker_required=True,
    semantic_sql_dialect={
        'language_profile': 'xtdb-sql-2.1', 'quote_open': '"',
        'supports_rollup': False,
    },
)


class XTDBPilotProvider(ActualEnginePilotProvider):
    """Bind the XTDB adapter to the common actual-engine facade."""

    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    """Create the XTDB provider with an injected or qualified client."""
    return XTDBPilotProvider(
        context,
        permissions,
        client or XTDBClient(
            permissions.acquire_secret,
            pool_namespace=context.pool_namespace,
        ),
    )
