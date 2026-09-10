"""Apache Cassandra 5.0.8 wide-column/CQL semantic provider."""

from pgadmin.cdeadmin.sdk import ActualEnginePilotProvider, PilotProfile

from .client import CassandraClient


PROFILE = PilotProfile(
    provider_id='org.cdeadmin.cassandra',
    profile_id='cassandra-native',
    engine_id='cassandra',
    engine_name='Apache Cassandra',
    exact_version='5.0.8',
    protocol_id='cql',
    model_family='wide-column',
    language_profile='cql-3',
    language_name='Cassandra Query Language (CQL)',
    transaction_model='cassandra-native-operation-outcome',
    result_kind='wide_column',
    resource_kinds=(
        'cluster', 'datacenter', 'node', 'keyspace', 'table', 'column',
        'replication', 'index', 'materialized-view', 'user-defined-type',
        'trigger', 'function', 'aggregate', 'role', 'permission', 'identity',
        'query', 'tracing-session',
        'repair', 'compaction', 'snapshot', 'backup', 'restore', 'shell',
    ),
    admin_tools=(
        'cqlsh', 'nodetool', 'sstableloader',
    ),
    required_permissions=('network', 'secret_read'),
    language_mime_type='application/vnd.apache.cassandra.cql',
    result_renderer_kind='wide-column',
    result_renderer_id='cdeadmin.result.wide-column.grid',
    result_component_reference='cdeadmin/results/WideColumnView',
    result_records_field='rows',
    result_export_formats=('csv', 'json', 'jsonl'),
    result_worker_required=True,
    dialect_contract_id='cassandra.dialect.5.0.8.v1',
    dialect_evidence=(
        'cassandra-5.0.8-source-parser-inventory',
        'cassandra-5.0.8-native-task-live-execution',
    ),
    dialect_contract_file='cassandra_dialect_5_0_8.json',
    metrics_contract_file='cassandra_metrics_5_0_8.json',
)


class CassandraPilotProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    return CassandraPilotProvider(
        context,
        permissions,
        client or CassandraClient(permissions.acquire_secret),
    )
