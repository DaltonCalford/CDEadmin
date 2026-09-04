"""Neo4j 2026.04.0 graph/Bolt semantic provider."""

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
)
from .client import Neo4jClient, QUALIFIED_GDS_SHA256


PROFILE = PilotProfile(
    provider_id='org.cdeadmin.neo4j',
    profile_id='neo4j-native',
    engine_id='neo4j',
    engine_name='Neo4j',
    exact_version='2026.04.0',
    protocol_id='bolt',
    model_family='graph',
    language_profile='cypher',
    language_name='Cypher',
    transaction_model='neo4j-bolt-transaction',
    result_kind='graph',
    resource_kinds=(
        'dbms', 'server', 'database', 'composite-database', 'alias',
        'graph', 'node', 'label', 'relationship', 'relationship-type',
        'property', 'index', 'constraint', 'procedure', 'function',
        'setting', 'transaction', 'query', 'user', 'role', 'privilege',
        'query-plan', 'graph-projection',
        'backup', 'restore', 'import', 'export', 'shell',
        'consistency-check',
    ),
    admin_tools=(
        'cypher-shell', 'backup-restore', 'database-admin',
        'cluster-admin', 'import', 'consistency-check',
    ),
    required_permissions=('network', 'secret_read'),
    language_mime_type='application/vnd.neo4j.cypher',
    result_renderer_kind='graph',
    result_renderer_id='cdeadmin.result.graph.canvas',
    result_component_reference='cdeadmin/results/GraphView',
    result_records_field='graphs',
    result_export_formats=('json', 'jsonl'),
    result_worker_required=True,
    semantic_compiler_kind='neo4j-cypher',
    semantic_time_operations=(
        'as_of', 'range', 'period_to_date', 'period_comparison',
    ),
)


class Neo4jPilotProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)

    @staticmethod
    def compile_semantic_query(model, query):
        from .semantic import compile_neo4j_cypher
        return compile_neo4j_cypher(model, query)


def create_provider(context, permissions, client=None):
    return Neo4jPilotProvider(
        context,
        permissions,
        client or Neo4jClient(
            permissions.acquire_secret,
            gds_surface_sha256=QUALIFIED_GDS_SHA256,
        ),
    )
