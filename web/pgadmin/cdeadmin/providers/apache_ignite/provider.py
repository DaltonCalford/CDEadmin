"""Apache Ignite 2.17.0 distributed cache and SQL provider."""

from pgadmin.cdeadmin.sdk import ActualEnginePilotProvider, PilotProfile
from ..native_distributed import (
    NativeDistributedClient,
)


PROFILE = PilotProfile(
    'org.cdeadmin.apache_ignite', 'apache-ignite-native', 'apache_ignite',
    'Apache Ignite', '2.17.0', 'ignite_thin', 'distributed',
    'ignite-sql-keyvalue', 'Ignite SQL and key/value API',
    'ignite-native-transaction', 'key_value',
    (
        'cluster', 'node', 'baseline-topology', 'sql-schema', 'table',
        'index', 'cache', 'cache-template', 'data-region', 'compute-task',
        'service', 'user', 'snapshot',
    ),
    ('control-script', 'snapshot', 'index-validate', 'idle-verify'),
    language_mime_type='application/vnd.apache.ignite.command+json',
    result_renderer_kind='key-value',
    result_renderer_id='cdeadmin.result.key-value.inspector',
    result_component_reference='cdeadmin/results/KeyValueView',
    result_records_field='entries',
    result_export_formats=('json', 'jsonl'),
    result_worker_required=True,
)


OPERATIONS = {
    'cluster': {'inspect', 'set_state'}, 'node': {'inspect'},
    'baseline-topology': {
        'inspect', 'add_nodes', 'remove_nodes', 'set_nodes', 'set_version',
        'configure_auto_adjust',
    }, 'sql-schema': {'inspect'},
    'table': {'inspect'}, 'index': {'inspect'},
    'cache': {
        'inspect', 'create', 'insert', 'update', 'delete', 'drop', 'clear',
        'validate_indexes', 'idle_verify', 'reset_lost_partitions',
        'rebuild_indexes',
    },
    'cache-template': {'inspect'}, 'data-region': {'inspect'},
    'compute-task': {'inspect', 'cancel'},
    'service': {'inspect', 'cancel'},
    'user': {'inspect'},
    'snapshot': {'inspect', 'create', 'check', 'restore'},
}


class ApacheIgniteProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    if client is None:
        from .client import IgniteBackend
        client = NativeDistributedClient(
            PROFILE, IgniteBackend(permissions.acquire_secret), OPERATIONS
        )
    return ApacheIgniteProvider(context, permissions, client)
