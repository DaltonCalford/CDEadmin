"""FoundationDB 7.3.77 ordered key/value provider."""

from pgadmin.cdeadmin.sdk import ActualEnginePilotProvider, PilotProfile
from ..native_distributed import (
    NativeDistributedClient,
)
from .client import ADMIN_OPERATIONS


PROFILE = PilotProfile(
    'org.cdeadmin.foundationdb', 'foundationdb-native', 'foundationdb',
    'FoundationDB', '7.3.77', 'foundationdb_native', 'ordered-key-value',
    'foundationdb-keyvalue-json', 'FoundationDB key/value operations',
    'foundationdb-acid-transaction-native', 'key_value',
    ('cluster', 'coordinator', 'process', 'configuration', 'tenant',
     'directory', 'subspace', 'key-range', 'key', 'transaction', 'watch',
     'backup', 'restore'),
    ('fdbcli', 'fdbbackup', 'fdbrestore', 'fdbdr'),
    language_mime_type='application/vnd.foundationdb.operation+json',
    result_renderer_kind='key-value',
    result_renderer_id='cdeadmin.result.key-value.inspector',
    result_component_reference='cdeadmin/results/KeyValueView',
    result_records_field='entries', result_export_formats=('json', 'jsonl'),
    result_worker_required=True,
)

OPERATIONS = ADMIN_OPERATIONS


class FoundationDBProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    if client is None:
        from .client import FoundationDBBackend
        client = NativeDistributedClient(
            PROFILE, FoundationDBBackend(permissions.acquire_secret),
            OPERATIONS,
        )
    return FoundationDBProvider(context, permissions, client)
