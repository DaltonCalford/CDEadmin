"""InfluxDB 3.9.0 time-series provider."""

from pgadmin.cdeadmin.sdk import ActualEnginePilotProvider, PilotProfile

from .client import InfluxDBClient


PROFILE = PilotProfile(
    provider_id='org.cdeadmin.influxdb',
    profile_id='influxdb-native',
    engine_id='influxdb',
    engine_name='InfluxDB',
    exact_version='3.9.0',
    protocol_id='http_json',
    model_family='time-series-analytic',
    language_profile='influxdb3-sql-influxql',
    language_name='InfluxDB 3 SQL and InfluxQL',
    transaction_model='influxdb-request-native-outcome',
    result_kind='time_series',
    resource_kinds=(
        'cluster', 'node', 'database', 'table', 'column', 'tag', 'field',
        'retention-policy', 'last-cache', 'distinct-cache', 'token',
        'trigger', 'plugin',
        'processing-engine', 'compaction',
    ),
    admin_tools=('influxdb3-cli', 'processing-engine-admin'),
    required_permissions=('network', 'secret_read'),
    language_mime_type='application/vnd.influxdb.sql',
    result_renderer_kind='time-series',
    result_renderer_id='cdeadmin.result.time-series.explorer',
    result_component_reference='cdeadmin/results/TimeSeriesView',
    result_records_field='points',
    result_export_formats=('csv', 'json', 'jsonl'),
    result_worker_required=True,
    semantic_sql_dialect={
        'language_profile': 'influxdb3-sql-influxql', 'quote_open': '"',
        'supports_rollup': False,
    },
)


class InfluxDBPilotProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    return InfluxDBPilotProvider(
        context, permissions,
        client or InfluxDBClient(permissions.acquire_secret),
    )
