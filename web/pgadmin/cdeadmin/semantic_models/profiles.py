##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider model-family vocabulary for semantic analytics designers."""

from __future__ import annotations

import copy


SEMANTIC_PROFILE_SCHEMA = 'cdeadmin.semantic-analytical-profile.v1'


_PROFILES = {
    'relational': {
        'title': 'Relational and multidimensional',
        'source_kinds': ('table', 'view', 'materialized-view', 'query'),
        'source_classifications': ('fact', 'dimension', 'bridge', 'lookup'),
        'dimension_kinds': (
            'attribute', 'categorical', 'numeric-band', 'geography', 'time',
            'degenerate', 'role-playing',
        ),
        'relationship_kinds': ('join', 'bridge', 'role-playing'),
        'measure_kinds': ('aggregate', 'calculated', 'semi-additive'),
        'grain_vocabulary': 'fact-key',
    },
    'graph': {
        'title': 'Graph analytics',
        'source_kinds': (
            'graph', 'node', 'label', 'relationship', 'relationship-type',
            'graph-projection',
        ),
        'source_classifications': (
            'node-set', 'relationship-set', 'path-set', 'projection',
        ),
        'dimension_kinds': (
            'label', 'property', 'relationship-type', 'path', 'hop',
            'community', 'centrality',
        ),
        'relationship_kinds': (
            'native-edge', 'path-pattern', 'projection-membership',
        ),
        'measure_kinds': (
            'property-aggregate', 'path-count', 'degree', 'score',
            'algorithm-result',
        ),
        'grain_vocabulary': 'node-relationship-path',
    },
    'document': {
        'title': 'Document analytics',
        'source_kinds': (
            'collection', 'view', 'document', 'aggregation-pipeline',
        ),
        'source_classifications': (
            'document-set', 'embedded-array', 'event', 'lookup',
        ),
        'dimension_kinds': (
            'document-path', 'keyword', 'array-element', 'embedded-document',
            'bucket', 'time',
        ),
        'relationship_kinds': ('reference', 'lookup', 'embedded'),
        'measure_kinds': (
            'aggregate', 'calculated', 'document-count', 'array-aggregate',
        ),
        'grain_vocabulary': 'document-path',
    },
    'search': {
        'title': 'Search analytics',
        'source_kinds': ('index', 'alias', 'data-stream', 'saved-query'),
        'source_classifications': (
            'indexed-document-set', 'data-stream', 'search-view',
        ),
        'dimension_kinds': (
            'keyword-facet', 'range-bucket', 'date-histogram',
            'geospatial-bucket', 'analyzer-token', 'nested-path',
        ),
        'relationship_kinds': ('nested', 'parent-child', 'alias-target'),
        'measure_kinds': (
            'document-count', 'bucket-metric', 'score', 'pipeline-metric',
        ),
        'grain_vocabulary': 'indexed-document',
    },
    'vector': {
        'title': 'Vector analytics',
        'source_kinds': ('collection', 'partition', 'vector-index'),
        'source_classifications': (
            'vector-set', 'partition', 'metadata-set',
        ),
        'dimension_kinds': (
            'metadata', 'partition', 'similarity-band', 'distance-band',
            'cluster', 'embedding-model',
        ),
        'relationship_kinds': ('metadata-reference', 'nearest-neighbor'),
        'measure_kinds': (
            'vector-count', 'distance', 'similarity', 'recall', 'score',
        ),
        'grain_vocabulary': 'vector-entity',
    },
    'time-series': {
        'title': 'Time-series analytics',
        'source_kinds': (
            'table', 'measurement', 'database', 'retention-policy',
        ),
        'source_classifications': (
            'measurement', 'event-series', 'metric-series', 'lookup',
        ),
        'dimension_kinds': (
            'time', 'tag', 'field', 'window', 'retention-tier',
        ),
        'relationship_kinds': ('tag-correlation', 'time-alignment', 'lookup'),
        'measure_kinds': (
            'aggregate', 'rate', 'delta', 'moving-window', 'percentile',
        ),
        'grain_vocabulary': 'series-time',
    },
    'columnar': {
        'title': 'Columnar analytics',
        'source_kinds': (
            'table', 'view', 'materialized-view', 'projection', 'dictionary',
            'partition',
        ),
        'source_classifications': (
            'fact', 'dimension', 'projection', 'dictionary',
        ),
        'dimension_kinds': (
            'attribute', 'low-cardinality', 'partition-key', 'sort-key',
            'time', 'bucket',
        ),
        'relationship_kinds': ('join', 'dictionary-lookup', 'projection-of'),
        'measure_kinds': (
            'aggregate', 'calculated', 'aggregate-state', 'quantile',
        ),
        'grain_vocabulary': 'part-row',
    },
    'wide-column': {
        'title': 'Wide-column analytics',
        'source_kinds': ('keyspace', 'table', 'materialized-view'),
        'source_classifications': (
            'partitioned-table', 'materialized-view', 'lookup',
        ),
        'dimension_kinds': (
            'partition-key', 'clustering-key', 'column', 'time-bucket',
        ),
        'relationship_kinds': ('denormalized-reference', 'lookup'),
        'measure_kinds': ('aggregate', 'count', 'calculated'),
        'grain_vocabulary': 'partition-clustering-key',
    },
    'key-value': {
        'title': 'Key-value and data-structure analytics',
        'source_kinds': (
            'database', 'key', 'stream', 'key-range', 'subspace', 'cache',
            'table', 'collection',
        ),
        'source_classifications': (
            'keyspace', 'data-structure', 'stream', 'ordered-range',
        ),
        'dimension_kinds': (
            'key-prefix', 'native-type', 'ttl-band', 'stream-field',
            'score-band',
        ),
        'relationship_kinds': ('prefix-membership', 'stream-consumer'),
        'measure_kinds': ('key-count', 'memory', 'ttl', 'stream-rate'),
        'grain_vocabulary': 'key-or-entry',
    },
    'bitemporal': {
        'title': 'Bitemporal document and relational analytics',
        'source_kinds': (
            'table', 'collection', 'key', 'document', 'entity',
            'transaction-log',
        ),
        'source_classifications': (
            'entity-history', 'document-set', 'transaction-set', 'lookup',
        ),
        'dimension_kinds': (
            'attribute', 'document-path', 'valid-time', 'system-time',
            'time',
        ),
        'relationship_kinds': (
            'join', 'entity-reference', 'temporal-overlap',
        ),
        'measure_kinds': (
            'aggregate', 'calculated', 'duration', 'version-count',
        ),
        'grain_vocabulary': 'entity-valid-system-time',
    },
}


_MODEL_FAMILY_ALIASES = {
    'relational': 'relational',
    'relational-mga': 'relational',
    'embedded-relational': 'relational',
    'distributed-relational': 'relational',
    'versioned-relational': 'relational',
    'embedded-columnar-analytic-relational': 'columnar',
    'columnar-analytic': 'columnar',
    'time-series-analytic': 'time-series',
    'vector-analytic': 'vector',
    'search-analytic': 'search',
    'search-document-analytic': 'search',
    'search-language': 'search',
    'search-relational-analytic': 'search',
    'document': 'document',
    'graph': 'graph',
    'wide-column': 'wide-column',
    'distributed-wide-column': 'wide-column',
    'data-structure-key-value': 'key-value',
    'ordered-key-value': 'key-value',
    'distributed-key-value': 'key-value',
    'distributed': 'key-value',
    'immutable-multimodel': 'bitemporal',
    'bitemporal-document-relational': 'bitemporal',
    'search': 'search',
    'vector': 'vector',
    'time-series': 'time-series',
    'columnar': 'columnar',
    'wide-column': 'wide-column',
    'key-value': 'key-value',
    'bitemporal': 'bitemporal',
}


def analytical_profile(model_family):
    """Return designer vocabulary selected by a provider model family."""
    recognized = model_family in _MODEL_FAMILY_ALIASES
    family = _MODEL_FAMILY_ALIASES.get(model_family, 'document')
    profile = copy.deepcopy(_PROFILES[family])
    return {
        'schema': SEMANTIC_PROFILE_SCHEMA,
        'provider_model_family': model_family,
        'recognized_model_family': recognized,
        'semantic_family': family,
        **profile,
        'designer_capabilities': {
            'relationship_diagram': True,
            'grain': True,
            'hierarchies': True,
            'measures': True,
            'calculated_measures': True,
            'time_intelligence': True,
            'parameters': True,
            'filters': True,
            'drill_down': True,
            'drill_through': True,
            'pivot': True,
            'cross_filtering': True,
            'charts': True,
            'dashboards': True,
            'reports': True,
            'schedules': True,
            'row_level_security': True,
            'tenant_filtering': True,
            'metric_certification': True,
            'lineage': True,
            'versioning': True,
            'diagnostics': True,
            'reproducibility': True,
        },
    }


__all__ = ('SEMANTIC_PROFILE_SCHEMA', 'analytical_profile')
