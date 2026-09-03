##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Redis 8.6+ data-structure/RESP semantic provider."""

from pgadmin.cdeadmin.sdk import ActualEnginePilotProvider, PilotProfile

from .client import RedisClient


PROFILE = PilotProfile(
    provider_id='org.cdeadmin.redis',
    profile_id='redis-native',
    engine_id='redis',
    engine_name='Redis',
    exact_version='8.6.2',
    protocol_id='resp',
    model_family='data-structure-key-value',
    language_profile='redis-resp3-command',
    language_name='Redis RESP3 command',
    transaction_model='redis-multi-exec-native-outcome',
    result_kind='key_value',
    resource_kinds=(
        'deployment', 'node', 'replica', 'sentinel', 'cluster-slot',
        'database', 'key', 'string', 'hash', 'list', 'set',
        'sorted-set', 'stream', 'consumer-group', 'consumer',
        'geospatial', 'bitmap', 'hyperloglog', 'vector-set',
        'pubsub-channel', 'function-library', 'script', 'acl-user',
        'module', 'ttl', 'transaction', 'pipeline', 'persistence',
        'configuration', 'client', 'slow-log', 'latency', 'backup',
        'restore', 'import', 'export', 'shell',
    ),
    admin_tools=('redis-cli', 'redis-check-rdb', 'redis-check-aof'),
    required_permissions=('network', 'secret_read'),
    language_mime_type='application/vnd.redis.resp3',
    result_renderer_kind='key-value',
    result_renderer_id='cdeadmin.result.key-value.inspector',
    result_component_reference='cdeadmin/results/KeyValueView',
    result_records_field='entries',
    result_export_formats=('json', 'jsonl'),
    result_worker_required=True,
    minimum_version='8.6.0',
)


class RedisPilotProvider(ActualEnginePilotProvider):
    """Bind the Redis adapter to the common actual-engine facade."""

    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    """Create the Redis provider with an injected or qualified client."""
    return RedisPilotProvider(
        context,
        permissions,
        client or RedisClient(permissions.acquire_secret),
    )
