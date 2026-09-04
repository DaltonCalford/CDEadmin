"""TiKV 8.5.6 native distributed key/value provider."""

from pgadmin.cdeadmin.sdk import ActualEnginePilotProvider, PilotProfile
from ..native_distributed import (
    NativeDistributedClient,
)
from .client import ADMIN_OPERATIONS

PROFILE = PilotProfile(
    'org.cdeadmin.tikv', 'tikv-native', 'tikv', 'TiKV', '8.5.6',
    'tikv_grpc', 'distributed-key-value', 'tikv-keyvalue-json',
    'TiKV raw and transactional key/value API',
    'tikv-transaction-native', 'key_value',
    ('cluster', 'store', 'region', 'peer', 'keyspace', 'key-range',
     'raw-key', 'transaction', 'lock', 'placement-rule', 'scheduler',
     'configuration', 'ttl', 'backup', 'restore', 'import-job',
     'coprocessor'),
    ('pd-ctl', 'tikv-ctl', 'br', 'cdc'),
    language_mime_type='application/vnd.tikv.operation+json',
    result_renderer_kind='key-value',
    result_renderer_id='cdeadmin.result.key-value.inspector',
    result_component_reference='cdeadmin/results/KeyValueView',
    result_records_field='entries', result_export_formats=('json', 'jsonl'),
    result_worker_required=True,
)
OPERATIONS = ADMIN_OPERATIONS


class TiKVProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    if client is None:
        from .client import TiKVBackend
        client = NativeDistributedClient(PROFILE, TiKVBackend(), OPERATIONS)
    return TiKVProvider(context, permissions, client)
