"""Milvus 2.6.5 vector provider."""

from pgadmin.cdeadmin.sdk import ActualEnginePilotProvider, PilotProfile

from .client import MilvusClientAdapter


PROFILE = PilotProfile(
    provider_id='org.cdeadmin.milvus',
    profile_id='milvus-native',
    engine_id='milvus',
    engine_name='Milvus',
    exact_version='2.6.5',
    protocol_id='grpc',
    model_family='vector-analytic',
    language_profile='milvus-query-search-api',
    language_name='Milvus Query and Vector Search API',
    transaction_model='milvus-consistency-native-outcome',
    result_kind='vector',
    resource_kinds=(
        'cluster', 'resource-group', 'database', 'collection', 'field',
        'partition', 'vector-index', 'alias', 'load-state', 'compaction',
        'user', 'role', 'privilege', 'credential',
    ),
    admin_tools=('bulk-import', 'compaction', 'load-management'),
    required_permissions=('network', 'secret_read'),
    language_mime_type='application/vnd.milvus.search+json',
    result_renderer_kind='vector',
    result_renderer_id='cdeadmin.result.vector.explorer',
    result_component_reference='cdeadmin/results/VectorView',
    result_records_field='matches',
    result_export_formats=('json', 'jsonl'),
    result_worker_required=True,
)


class MilvusPilotProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    return MilvusPilotProvider(
        context, permissions,
        client or MilvusClientAdapter(permissions.acquire_secret),
    )
