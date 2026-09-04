"""MongoDB 8.2.6 document/topology semantic provider."""

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
)
from .client import MongoDBClient


PROFILE = PilotProfile(
    provider_id='org.cdeadmin.mongodb',
    profile_id='mongodb-native',
    engine_id='mongodb',
    engine_name='MongoDB',
    exact_version='8.2.6',
    protocol_id='mongodb_wire',
    model_family='document',
    language_profile='mongodb-query-api-json',
    language_name='MongoDB Query API (JSON)',
    transaction_model='mongodb-session-transaction',
    result_kind='document',
    resource_kinds=(
        'deployment', 'replica-set', 'shard', 'router', 'database',
        'collection', 'view', 'document', 'index', 'validator', 'user',
        'role', 'privilege', 'zone', 'balancer', 'change-stream',
        'profiling', 'current-operation', 'server-log', 'statistics',
        'aggregation-pipeline', 'backup', 'restore', 'import', 'export',
        'shell',
    ),
    admin_tools=(
        'mongosh', 'backup-restore', 'replica-set-admin', 'sharding-admin',
    ),
    required_permissions=('network', 'secret_read'),
    language_mime_type='application/json',
    result_renderer_kind='document',
    result_renderer_id='cdeadmin.result.document.tree',
    result_component_reference='cdeadmin/results/DocumentTreeView',
    result_records_field='documents',
    result_export_formats=('json', 'jsonl'),
    result_worker_required=True,
    semantic_compiler_kind='mongodb-aggregation',
    semantic_time_operations=(
        'as_of', 'range', 'period_to_date', 'period_comparison',
    ),
    semantic_window_operations=(
        'running_sum', 'moving_sum', 'moving_average', 'lag', 'delta',
    ),
)


class MongoDBPilotProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)

    @staticmethod
    def compile_semantic_query(model, query):
        from .semantic import compile_mongodb_aggregation
        return compile_mongodb_aggregation(model, query)


def create_provider(context, permissions, client=None):
    return MongoDBPilotProvider(
        context,
        permissions,
        client or MongoDBClient(permissions.acquire_secret),
    )
