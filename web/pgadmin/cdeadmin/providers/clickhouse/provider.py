"""ClickHouse 25.12 analytical HTTP/JSON semantic provider."""

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
)

from .client import ClickHouseClient


PROFILE = PilotProfile(
    provider_id='org.cdeadmin.clickhouse',
    profile_id='clickhouse-native',
    engine_id='clickhouse',
    engine_name='ClickHouse',
    exact_version='25.12.10.7-stable',
    protocol_id='http_json',
    model_family='columnar-analytic',
    language_profile='clickhouse-sql',
    language_name='ClickHouse SQL',
    transaction_model='clickhouse-statement-native-outcome',
    result_kind='columnar',
    resource_kinds=(
        'server', 'cluster', 'replica', 'database', 'table', 'column',
        'view', 'materialized-view', 'dictionary', 'function', 'projection',
        'data-skipping-index', 'partition', 'user', 'role', 'quota',
        'settings-profile', 'row-policy',
    ),
    admin_tools=('system-operations',),
    required_permissions=('network', 'secret_read'),
    language_mime_type='application/vnd.clickhouse.sql',
    result_renderer_kind='columnar',
    result_renderer_id='cdeadmin.result.columnar.grid',
    result_component_reference='cdeadmin/results/ColumnarView',
    result_records_field='rows',
    result_export_formats=('csv', 'json', 'jsonl'),
    result_worker_required=True,
    semantic_sql_dialect={
        'language_profile': 'clickhouse-sql', 'quote_open': '`',
        'quote_close': '`', 'supports_rollup': True,
    },
    semantic_materialization_kind='materialized-view',
    semantic_materialization_defaults={
        'engine': 'MergeTree', 'order_by': 'tuple()',
    },
)


class ClickHousePilotProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    return ClickHousePilotProvider(
        context,
        permissions,
        client or ClickHouseClient(permissions.acquire_secret),
    )
