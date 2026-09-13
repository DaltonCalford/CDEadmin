"""Ownership annotations for providers using relational catalog paths.

Call only from catalogs that explicitly encode schema/database and relation
names in their paths. Native identities and authority paths are not rewritten.
"""


def annotate_relation_ownership(resources):
    relations = {}
    for item in resources:
        if item['resource_kind'] in {'table', 'view', 'materialized-view'}:
            relations.setdefault(tuple(item['display_path']), []).append(item)
    for item in resources:
        kind = item['resource_kind']
        declared_path = item.get('native', {}).get('navigator_relation_path')
        if kind not in {'column', 'index', 'constraint', 'trigger',
                        'partition', 'table-storage'} and not declared_path:
            continue
        path = tuple(item['display_path'])
        owner_path = tuple(declared_path) if declared_path else (
            path if kind == 'table-storage' else path[:-1])
        owners = relations.get(owner_path, [])
        # Ambiguous/incomplete discovery must not silently choose an owner.
        if len(owners) != 1:
            continue
        native = item.setdefault('native', {})
        native['navigator_parent_resource_id'] = owners[0]['resource_id']
        if kind == 'table-storage':
            item['display_path'] = [*owner_path, 'Storage']
    return resources
