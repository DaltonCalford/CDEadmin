##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Native YugabyteDB 2025.2.2.2 YCQL provider."""

from pgadmin.cdeadmin.sdk import ActualEnginePilotProvider, PilotProfile

from .client import YugabyteDBYCQLClient


PROFILE = PilotProfile(
    provider_id='org.cdeadmin.yugabytedb.ycql',
    profile_id='yugabytedb-ycql',
    engine_id='yugabytedb',
    engine_name='YugabyteDB YCQL',
    exact_version='2025.2.2.2',
    protocol_id='cql',
    model_family='distributed-wide-column',
    language_profile='ycql',
    language_name='Yugabyte Cloud Query Language (YCQL)',
    transaction_model='yugabytedb-ycql-native-operation-outcome',
    result_kind='wide_column',
    resource_kinds=(
        'cluster', 'datacenter', 'node', 'keyspace', 'table', 'column',
        'index', 'user-defined-type', 'role', 'permission', 'query',
        'tracing-session', 'shell',
    ),
    admin_tools=('ycqlsh',),
    required_permissions=('network', 'secret_read'),
    language_mime_type='application/vnd.yugabyte.ycql',
    result_renderer_kind='wide-column',
    result_renderer_id='cdeadmin.result.wide-column.grid',
    result_component_reference='cdeadmin/results/WideColumnView',
    result_records_field='rows',
    result_export_formats=('csv', 'json', 'jsonl'),
    result_worker_required=True,
    dialect_contract_id=(
        'yugabytedb.ycql-dialect.2025.2.2.2.v1'
    ),
    dialect_evidence=(
        'yugabytedb-2025.2.2.2-ycql-source-parser-inventory',
        'yugabytedb-2025.2.2.2-ycql-native-task-live-execution',
    ),
    dialect_contract_file=(
        'yugabytedb_ycql_dialect_2025_2_2_2.json'
    ),
    metrics_contract_file=(
        'yugabytedb_ycql_metrics_2025_2_2_2.json'
    ),
)


class YugabyteDBYCQLProvider(ActualEnginePilotProvider):
    """CDEadmin facade for the native YCQL interface."""

    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    return YugabyteDBYCQLProvider(
        context,
        permissions,
        client or YugabyteDBYCQLClient(permissions.acquire_secret),
    )
