"""OpenSearch 3.6.0 search provider."""

from pgadmin.cdeadmin.sdk import ActualEnginePilotProvider, PilotProfile

from .client import OpenSearchClient


PROFILE = PilotProfile(
    provider_id='org.cdeadmin.opensearch',
    profile_id='opensearch-native',
    engine_id='opensearch',
    engine_name='OpenSearch',
    exact_version='3.6.0',
    protocol_id='http_json',
    model_family='search-document-analytic',
    language_profile='opensearch-query-dsl',
    language_name='OpenSearch Query DSL',
    transaction_model='opensearch-request-native-outcome',
    result_kind='search',
    resource_kinds=(
        'cluster', 'node', 'index', 'index-template',
        'component-template', 'data-stream', 'document', 'mapping', 'field',
        'analyzer', 'normalizer', 'tokenizer', 'ingest-pipeline', 'script',
        'alias', 'repository', 'snapshot', 'user', 'role', 'role-mapping',
        'tenant', 'policy', 'settings', 'shard', 'ingest-processor',
        'reindex-operation', 'query-profile',
    ),
    admin_tools=('snapshot-restore', 'cluster-operations'),
    required_permissions=('network', 'secret_read'),
    language_mime_type='application/vnd.opensearch.query-dsl+json',
    result_renderer_kind='search',
    result_renderer_id='cdeadmin.result.search.hits',
    result_component_reference='cdeadmin/results/SearchView',
    result_records_field='hits',
    result_export_formats=('json', 'jsonl'),
    result_worker_required=True,
)


class OpenSearchPilotProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    return OpenSearchPilotProvider(
        context, permissions,
        client or OpenSearchClient(permissions.acquire_secret),
    )
