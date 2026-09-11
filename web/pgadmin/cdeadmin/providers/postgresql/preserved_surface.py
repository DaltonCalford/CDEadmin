##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Fail-closed inventory for the preserved PostgreSQL administration UI."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path


SURFACE_ID = 'pgadmin.preserved-postgresql-administration'
SURFACE_SCHEMA = 'cdeadmin.postgresql-preserved-surface-audit.v1'


def _entry(
        root, operations, *, extra_roots=(), sql_root=None, markers=None):
    roots = (root,) + tuple(extra_roots)
    value = {
        'roots': roots,
        'operation_obligations': operations,
        'asset_patterns': {
            'controllers': tuple(f'{item}/__init__.py' for item in roots),
            'forms': tuple(f'{item}/static/js/*.ui.js' for item in roots),
            'tests': tuple(f'{item}/tests/**/test_*.py' for item in roots),
            'sql_templates': (
                f'{sql_root or root}/templates/**/*.sql',
            ),
        },
    }
    for marker_id, pattern in (markers or {}).items():
        value['asset_patterns'][f'marker:{marker_id}'] = (pattern,)
    return value


_SERVERS = 'web/pgadmin/browser/server_groups/servers'
_DATABASES = f'{_SERVERS}/databases'
_SCHEMAS = f'{_DATABASES}/schemas'
_TABLES = f'{_SCHEMAS}/tables'


PRESERVED_SURFACE_CONCEPTS = {
    'servers': _entry(
        _SERVERS, {'server': ('create', 'alter', 'drop', 'inspect')}
    ),
    'databases': _entry(
        _DATABASES,
        {'database': ('create', 'alter', 'drop', 'inspect')},
    ),
    'schemas': _entry(
        _SCHEMAS, {'schema': ('create', 'alter', 'drop', 'inspect')}
    ),
    'tables': _entry(
        _TABLES,
        {'table': (
            'create', 'alter', 'drop', 'inspect',
            'insert', 'update', 'delete',
        )},
        extra_roots=('web/pgadmin/tools/sqleditor',),
        sql_root=_TABLES,
        markers={
            'grid_editors': (
                'web/pgadmin/tools/sqleditor/static/js/components/'
                'QueryToolDataGrid/Editors.jsx'
            ),
            'grid_dml_tests': (
                'web/pgadmin/tools/sqleditor/utils/tests/'
                'test_save_changed_data.py'
            ),
            'grid_insert_sql': (
                'web/pgadmin/tools/sqleditor/templates/sqleditor/'
                'sql/default/insert.sql'
            ),
            'grid_update_sql': (
                'web/pgadmin/tools/sqleditor/templates/sqleditor/'
                'sql/default/update.sql'
            ),
            'grid_delete_sql': (
                'web/pgadmin/tools/sqleditor/templates/sqleditor/'
                'sql/default/delete.sql'
            ),
        },
    ),
    'views': _entry(
        f'{_SCHEMAS}/views',
        {'view': ('create', 'alter', 'drop', 'inspect')},
    ),
    'materialized_views': _entry(
        f'{_SCHEMAS}/views',
        {'materialized-view': (
            'create', 'alter', 'drop', 'inspect', 'refresh',
        )},
        markers={
            'mview_form': f'{_SCHEMAS}/views/static/js/mview.ui.js',
            'mview_create_sql': (
                f'{_SCHEMAS}/views/templates/mviews/pg/**/sql/create.sql'
            ),
        },
    ),
    'columns': _entry(
        f'{_TABLES}/columns',
        {'column': ('create', 'alter', 'drop', 'inspect')},
        sql_root=_TABLES,
    ),
    'domains': _entry(
        f'{_SCHEMAS}/domains',
        {'domain': ('create', 'alter', 'drop', 'inspect')},
    ),
    'types': _entry(
        f'{_SCHEMAS}/types',
        {
            'type': ('create', 'alter', 'drop', 'inspect'),
            'aggregate': ('create', 'alter', 'drop', 'inspect'),
            'cast': ('create', 'drop', 'inspect'),
            'collation': ('create', 'alter', 'drop', 'inspect'),
            'conversion': ('create', 'alter', 'drop', 'inspect'),
            'language': ('create', 'alter', 'drop', 'inspect'),
            'operator': ('create', 'alter', 'drop', 'inspect'),
            'operator-class': ('create', 'alter', 'drop', 'inspect'),
            'operator-family': ('create', 'alter', 'drop', 'inspect'),
        },
        extra_roots=(
            f'{_SCHEMAS}/aggregates',
            f'{_DATABASES}/casts',
            f'{_SCHEMAS}/collations',
            f'{_DATABASES}/languages',
            f'{_SCHEMAS}/operators',
        ),
        markers={
            'aggregate_form': (
                f'{_SCHEMAS}/aggregates/static/js/aggregate.ui.js'
            ),
            'cast_form': f'{_DATABASES}/casts/static/js/cast.ui.js',
            'collation_form': (
                f'{_SCHEMAS}/collations/static/js/collation.ui.js'
            ),
            'language_form': (
                f'{_DATABASES}/languages/static/js/language.ui.js'
            ),
            'operator_form': (
                f'{_SCHEMAS}/operators/static/js/operator.ui.js'
            ),
            'cdeadmin_native_type_forms': (
                'web/pgadmin/cdeadmin/providers/postgresql/provider.py'
            ),
        },
    ),
    'sequences': _entry(
        f'{_SCHEMAS}/sequences',
        {'sequence': ('create', 'alter', 'drop', 'inspect')},
    ),
    'functions': _entry(
        f'{_SCHEMAS}/functions',
        {'function': ('create', 'alter', 'drop', 'inspect')},
    ),
    'procedures': _entry(
        f'{_SCHEMAS}/functions',
        {'procedure': ('create', 'alter', 'drop', 'inspect')},
        markers={
            'procedure_model': (
                f'{_SCHEMAS}/functions/static/js/procedure.js'
            ),
            'procedure_create_sql': (
                f'{_SCHEMAS}/functions/templates/procedures/'
                'pg/**/create.sql'
            ),
        },
    ),
    'triggers': _entry(
        f'{_TABLES}/triggers',
        {
            'trigger': ('create', 'alter', 'drop', 'inspect'),
            'event-trigger': ('create', 'alter', 'drop', 'inspect'),
            'rule': ('create', 'alter', 'drop', 'inspect'),
        },
        extra_roots=(
            f'{_DATABASES}/event_triggers', f'{_TABLES}/rules',
        ),
        sql_root=_TABLES,
        markers={
            'table_trigger_form': (
                f'{_TABLES}/triggers/static/js/trigger.ui.js'
            ),
            'event_trigger_form': (
                f'{_DATABASES}/event_triggers/static/js/'
                'event_trigger.ui.js'
            ),
            'rule_form': f'{_TABLES}/rules/static/js/rule.ui.js',
        },
    ),
    'indexes': _entry(
        f'{_TABLES}/indexes',
        {'index': ('create', 'alter', 'drop', 'inspect')},
        sql_root=_TABLES,
    ),
    'constraints': _entry(
        f'{_TABLES}/constraints',
        {'constraint': ('create', 'alter', 'drop', 'inspect')},
        extra_roots=(
            f'{_TABLES}/constraints/check_constraint',
            f'{_TABLES}/constraints/exclusion_constraint',
            f'{_TABLES}/constraints/foreign_key',
            f'{_TABLES}/constraints/index_constraint',
        ),
        sql_root=_TABLES,
        markers={
            'check_constraint_form': (
                f'{_TABLES}/constraints/check_constraint/static/js/'
                'check_constraint.ui.js'
            ),
            'exclusion_constraint_form': (
                f'{_TABLES}/constraints/exclusion_constraint/static/js/'
                'exclusion_constraint.ui.js'
            ),
            'foreign_key_form': (
                f'{_TABLES}/constraints/foreign_key/static/js/'
                'foreign_key.ui.js'
            ),
            'primary_key_form': (
                f'{_TABLES}/constraints/index_constraint/static/js/'
                'primary_key.ui.js'
            ),
            'unique_constraint_form': (
                f'{_TABLES}/constraints/index_constraint/static/js/'
                'unique_constraint.ui.js'
            ),
        },
    ),
    'roles_and_grants': _entry(
        f'{_SERVERS}/roles',
        {
            'role': ('create', 'alter', 'drop', 'inspect'),
            'privilege': ('grant', 'revoke', 'inspect'),
            'row-policy': ('create', 'alter', 'drop', 'inspect'),
        },
        extra_roots=(f'{_TABLES}/row_security_policies',),
        markers={
            'privilege_macro': (
                f'{_SERVERS}/templates/macros/privilege.macros'
            ),
            'row_policy_form': (
                f'{_TABLES}/row_security_policies/static/js/'
                'row_security_policy.ui.js'
            ),
        },
    ),
    'extensions_and_plugins': _entry(
        f'{_DATABASES}/extensions',
        {
            'extension': ('create', 'alter', 'drop', 'inspect'),
            'foreign-data-wrapper': (
                'create', 'alter', 'drop', 'inspect',
            ),
            'foreign-server': ('create', 'alter', 'drop', 'inspect'),
            'user-mapping': ('create', 'alter', 'drop', 'inspect'),
            'foreign-table': ('create', 'alter', 'drop', 'inspect'),
        },
        extra_roots=(
            f'{_DATABASES}/foreign_data_wrappers',
            f'{_DATABASES}/foreign_data_wrappers/foreign_servers',
            f'{_DATABASES}/foreign_data_wrappers/foreign_servers/'
            'user_mappings',
            f'{_SCHEMAS}/foreign_tables',
        ),
        markers={
            'fdw_form': (
                f'{_DATABASES}/foreign_data_wrappers/static/js/'
                'foreign_data_wrapper.ui.js'
            ),
            'foreign_server_form': (
                f'{_DATABASES}/foreign_data_wrappers/foreign_servers/'
                'static/js/foreign_server.ui.js'
            ),
            'user_mapping_form': (
                f'{_DATABASES}/foreign_data_wrappers/foreign_servers/'
                'user_mappings/static/js/user_mapping.ui.js'
            ),
            'foreign_table_form': (
                f'{_SCHEMAS}/foreign_tables/static/js/'
                'foreign_table.ui.js'
            ),
        },
    ),
    'partitions': _entry(
        f'{_TABLES}/partitions',
        {'partition': ('create', 'alter', 'drop', 'inspect')},
        sql_root=_TABLES,
        markers={
            'partition_form': (
                f'{_TABLES}/partitions/static/js/partition.ui.js'
            ),
            'partition_create_sql': (
                f'{_TABLES}/templates/partitions/sql/pg/**/create.sql'
            ),
        },
    ),
    'tablespaces_and_filespaces': _entry(
        f'{_SERVERS}/tablespaces',
        {'tablespace': ('create', 'alter', 'drop', 'inspect')},
    ),
    'replication_objects': _entry(
        f'{_DATABASES}/publications',
        {
            'publication': ('create', 'alter', 'drop', 'inspect'),
            'subscription': ('create', 'alter', 'drop', 'inspect'),
        },
        extra_roots=(f'{_DATABASES}/subscriptions',),
        sql_root=_DATABASES,
        markers={
            'publication_form': (
                f'{_DATABASES}/publications/static/js/publication.ui.js'
            ),
            'subscription_form': (
                f'{_DATABASES}/subscriptions/static/js/'
                'subscription.ui.js'
            ),
            'postgresql_18_subscription_sql': (
                f'{_DATABASES}/subscriptions/templates/subscriptions/'
                'sql/18_plus/update.sql'
            ),
        },
    ),
    'jobs_and_events': _entry(
        f'{_SERVERS}/pgagent',
        {
            'job': ('create', 'alter', 'drop', 'inspect'),
            'schedule': ('create', 'alter', 'drop', 'inspect'),
        },
        extra_roots=(f'{_SERVERS}/pgagent/schedules',),
        sql_root=f'{_SERVERS}/pgagent',
        markers={
            'job_form': (
                f'{_SERVERS}/pgagent/static/js/pga_job.ui.js'
            ),
            'schedule_form': (
                f'{_SERVERS}/pgagent/schedules/static/js/'
                'pga_schedule.ui.js'
            ),
            'job_create_sql': (
                f'{_SERVERS}/pgagent/templates/pga_job/'
                'sql/pre3.4/create.sql'
            ),
            'schedule_create_sql': (
                f'{_SERVERS}/pgagent/templates/pga_schedule/'
                'sql/pre3.4/create.sql'
            ),
        },
    ),
}


def concept_declarations():
    """Return declarations bound to the preserved native surface."""
    return {
        concept_id: {
            'status': 'supported',
            'external_surface': SURFACE_ID,
            'external_surface_digest_required': True,
            'reason': (
                'The preserved PostgreSQL navigator and object dialogs own '
                'this administration workflow.'
            ),
            'evidence': ['provider-preserved-ui:postgresql'],
            'operation_obligations': {
                kind: list(operations)
                for kind, operations in definition[
                    'operation_obligations'].items()
            },
            'live_operations': {},
        }
        for concept_id, definition in PRESERVED_SURFACE_CONCEPTS.items()
    }


def preserved_operations():
    """Return the exact object-operation map owned by the legacy surface."""
    result = {}
    for definition in PRESERVED_SURFACE_CONCEPTS.values():
        for kind, operations in definition['operation_obligations'].items():
            result.setdefault(kind, set()).update(operations)
    return {
        kind: frozenset(operations)
        for kind, operations in result.items()
    }


def adapt_catalog(catalog):
    """Bind the common catalog to exact preserved-surface operations."""
    admitted_operations = preserved_operations()
    result = copy.deepcopy(catalog)
    resources = {
        resource['resource_kind']: resource
        for resource in result.get('objects', [])
    }
    titles = {'job': 'pgAgent job', 'schedule': 'pgAgent schedule'}
    mutation_classes = {
        'inspect': 'read',
        'create': 'admin', 'alter': 'admin',
        'insert': 'write', 'update': 'write',
        'grant': 'admin', 'revoke': 'admin',
        'delete': 'destructive', 'drop': 'destructive',
        'refresh': 'admin',
    }
    native_form_kinds = {
        'conversion', 'operator-class', 'operator-family',
    }

    def native_form(kind, operation_id, title):
        def field(
            field_id, label, control='text', required=False, **extra
        ):
            return {
                'field_id': field_id, 'label': label, 'control': control,
                'required': required, **extra,
            }

        if operation_id == 'inspect':
            fields = []
        elif operation_id == 'create':
            fields = [
                field('schema', 'Schema', required=True),
                field('name', 'Name', required=True),
            ]
            if kind == 'conversion':
                fields.extend((
                    field('source_encoding', 'Source encoding', required=True),
                    field('target_encoding', 'Target encoding', required=True),
                    field('function', 'Conversion function',
                          required=True, catalog_kind='function'),
                    field('default', 'Default conversion', 'boolean'),
                ))
            else:
                fields.append(field(
                    'index_method', 'Index access method',
                    required=True, catalog_kind='access-method',
                ))
                if kind == 'operator-class':
                    fields.extend((
                        field('data_type', 'Indexed data type',
                              required=True, catalog_kind='type'),
                        field('family', 'Operator family',
                              catalog_kind='operator-family'),
                        field('default', 'Default operator class', 'boolean'),
                        field('storage_type', 'Storage data type',
                              catalog_kind='type'),
                        field('operators', 'Strategy operators', 'json', True),
                        field('functions', 'Support functions', 'json', True),
                    ))
        elif operation_id == 'alter':
            fields = [
                field('action', 'Alter action', 'select', True, options=[
                    {'value': 'owner', 'label': 'Change owner'},
                    {'value': 'rename', 'label': 'Rename'},
                    {'value': 'schema', 'label': 'Move to schema'},
                ]),
                field('value', 'New owner, name, or schema', required=True),
            ]
        else:
            fields = [
                field('cascade', 'Drop dependent objects', 'boolean'),
                field('confirmation', 'Type object name to confirm',
                      required=True),
            ]
        return {
            'form_id': f'postgresql-{kind}-{operation_id}',
            'title': f'{operation_id.title()} {title}',
            'fields': fields,
        }
    for kind, admitted in admitted_operations.items():
        resource = resources.get(kind)
        if resource is None:
            resource = {
                'resource_kind': kind,
                'title': titles.get(kind, kind.replace('-', ' ').title()),
                'operations': [],
            }
            result.setdefault('objects', []).append(resource)
            resources[kind] = resource
        existing = {
            operation['operation_id']: operation
            for operation in resource.get('operations', [])
            if operation['operation_id'] in admitted
        }
        for operation_id in admitted:
            operation = existing.get(operation_id, {
                'operation_id': operation_id,
                'title': operation_id.replace('-', ' ').title(),
                'mutation_class': mutation_classes[operation_id],
                'form_id': 'postgresql-preserved-route',
                'target_required': operation_id != 'create',
                'confirmation_required': operation_id in {
                    'delete', 'drop',
                },
            })
            if kind in native_form_kinds:
                operation['form'] = native_form(
                    kind, operation_id, resource['title']
                )
                operation['execution_route'] = 'provider-native'
            else:
                operation['form'] = {
                    'form_id': 'postgresql-preserved-route',
                    'title': f'{operation_id.title()} {resource["title"]}',
                    'fields': [],
                    'external_surface': SURFACE_ID,
                }
                operation['execution_route'] = 'legacy-preserved'
            existing[operation_id] = operation
        resource['operations'] = [
            existing[operation_id]
            for operation_id in sorted(admitted)
        ]
    result['objects'] = [
        resource for resource in result.get('objects', [])
        if resource['resource_kind'] in admitted_operations
    ]
    return result


def _matched_files(root, patterns):
    files = set()
    for pattern in patterns:
        files.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(files)


def audit_preserved_surface(root):
    """Inventory required native assets and bind their contents to a digest."""
    root = Path(root).resolve()
    concepts = {}
    missing = []
    surface_hash = hashlib.sha256()
    for concept_id, definition in PRESERVED_SURFACE_CONCEPTS.items():
        groups = {}
        concept_files = set()
        for group_id, patterns in definition['asset_patterns'].items():
            files = _matched_files(root, patterns)
            if not files:
                missing.append(f'{concept_id}:{group_id}')
            relative = [str(path.relative_to(root)) for path in files]
            groups[group_id] = {
                'count': len(relative),
                'files': relative,
            }
            concept_files.update(files)
        for path in sorted(concept_files):
            relative = str(path.relative_to(root))
            surface_hash.update(relative.encode('utf-8'))
            surface_hash.update(b'\0')
            surface_hash.update(path.read_bytes())
            surface_hash.update(b'\0')
        concepts[concept_id] = {
            'status': 'passed' if all(
                group['count'] for group in groups.values()
            ) else 'failed',
            'asset_groups': groups,
            'operations': {
                kind: list(operations)
                for kind, operations in definition[
                    'operation_obligations'].items()
            },
        }
    return {
        'schema': SURFACE_SCHEMA,
        'surface_id': SURFACE_ID,
        'concept_count': len(concepts),
        'concepts': concepts,
        'missing_asset_groups': missing,
        'surface_sha256': surface_hash.hexdigest(),
        'passed': not missing,
    }


__all__ = (
    'PRESERVED_SURFACE_CONCEPTS', 'SURFACE_ID', 'SURFACE_SCHEMA',
    'adapt_catalog', 'audit_preserved_surface', 'concept_declarations',
    'preserved_operations',
)
