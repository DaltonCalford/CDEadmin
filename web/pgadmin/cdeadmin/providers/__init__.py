##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""CDEadmin provider packages and application composition helpers."""

from __future__ import annotations

import json
from pathlib import Path


BUILTIN_PACKAGES = (
    (
        'postgresql/provider_manifest.json',
        'pgadmin.cdeadmin.providers.postgresql.provider',
    ),
    (
        'mysql_family/mysql_provider_manifest.json',
        'pgadmin.cdeadmin.providers.mysql_family.provider',
    ),
    (
        'mysql_family/mariadb_provider_manifest.json',
        'pgadmin.cdeadmin.providers.mysql_family.provider',
    ),
    (
        'duckdb/provider_manifest.json',
        'pgadmin.cdeadmin.providers.duckdb.provider',
    ),
    (
        'firebird/provider_manifest.json',
        'pgadmin.cdeadmin.providers.firebird.provider',
    ),
    (
        'mongodb/provider_manifest.json',
        'pgadmin.cdeadmin.providers.mongodb.provider',
    ),
    (
        'neo4j/provider_manifest.json',
        'pgadmin.cdeadmin.providers.neo4j.provider',
    ),
    (
        'cassandra/provider_manifest.json',
        'pgadmin.cdeadmin.providers.cassandra.provider',
    ),
    (
        'redis/provider_manifest.json',
        'pgadmin.cdeadmin.providers.redis.provider',
    ),
    (
        'xtdb/provider_manifest.json',
        'pgadmin.cdeadmin.providers.xtdb.provider',
    ),
    (
        'clickhouse/provider_manifest.json',
        'pgadmin.cdeadmin.providers.clickhouse.provider',
    ),
    (
        'influxdb/provider_manifest.json',
        'pgadmin.cdeadmin.providers.influxdb.provider',
    ),
    (
        'milvus/provider_manifest.json',
        'pgadmin.cdeadmin.providers.milvus.provider',
    ),
    (
        'opensearch/provider_manifest.json',
        'pgadmin.cdeadmin.providers.opensearch.provider',
    ),
    (
        'opensearch_sql_ppl/provider_manifest.json',
        'pgadmin.cdeadmin.providers.opensearch_sql_ppl.provider',
    ),
    (
        'sqlite/provider_manifest.json',
        'pgadmin.cdeadmin.providers.sqlite.provider',
    ),
    (
        'apache_ignite/provider_manifest.json',
        'pgadmin.cdeadmin.providers.apache_ignite.provider',
    ),
    (
        'cockroachdb/provider_manifest.json',
        'pgadmin.cdeadmin.providers.cockroachdb.provider',
    ),
    (
        'dolt/provider_manifest.json',
        'pgadmin.cdeadmin.providers.dolt.provider',
    ),
    (
        'foundationdb/provider_manifest.json',
        'pgadmin.cdeadmin.providers.foundationdb.provider',
    ),
    (
        'immudb/provider_manifest.json',
        'pgadmin.cdeadmin.providers.immudb.provider',
    ),
    (
        'tidb/provider_manifest.json',
        'pgadmin.cdeadmin.providers.tidb.provider',
    ),
    (
        'tikv/provider_manifest.json',
        'pgadmin.cdeadmin.providers.tikv.provider',
    ),
    (
        'vitess/provider_manifest.json',
        'pgadmin.cdeadmin.providers.vitess.provider',
    ),
    (
        'yugabytedb/provider_manifest.json',
        'pgadmin.cdeadmin.providers.yugabytedb.provider',
    ),
)


def register_builtin_providers(registry):
    """Register built-in packages without importing their implementations.

    Provider imports remain contained by :class:`ProviderRegistry`; a broken
    provider is quarantined instead of aborting application startup.
    """
    provider_root = Path(__file__).parent
    registrations = []
    for relative_path, module_name in BUILTIN_PACKAGES:
        manifest_path = provider_root / relative_path
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        registrations.append(
            registry.register_package(manifest, module_name)
        )
    return tuple(registrations)


__all__ = ('register_builtin_providers',)
