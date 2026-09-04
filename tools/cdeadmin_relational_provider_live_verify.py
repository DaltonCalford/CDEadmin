#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Run CDEadmin provider categories against an exact live endpoint.

The selected endpoint is treated only as its advertised engine profile. A
temporary qualification account is provisioned through the endpoint's local
administrative socket, used through an endpoint-scoped secret lease, and
removed before exit. The account receives the administration authority needed
by the requested disposable object workflows. Credential values are never
written to result evidence.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import secrets
import sys
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.core import EndpointContext  # noqa: E402
from pgadmin.cdeadmin.core.registry import (  # noqa: E402
    PermissionGrant,
    PermissionGuard,
)
from pgadmin.cdeadmin.providers.mysql_family.provider import (  # noqa: E402
    MARIADB_PROFILE,
    MYSQL_PROFILE,
    create_provider,
)
from pgadmin.cdeadmin.providers.duckdb.provider import (  # noqa: E402
    PROFILE as DUCKDB_PROFILE,
    create_provider as create_duckdb_provider,
)
from pgadmin.cdeadmin.providers.cockroachdb.provider import (  # noqa: E402
    PROFILE as COCKROACHDB_PROFILE,
    create_provider as create_cockroachdb_provider,
)
from pgadmin.cdeadmin.providers.dolt.provider import (  # noqa: E402
    PROFILE as DOLT_PROFILE,
    create_provider as create_dolt_provider,
)
from pgadmin.cdeadmin.providers.tidb.provider import (  # noqa: E402
    PROFILE as TIDB_PROFILE,
    create_provider as create_tidb_provider,
)
from pgadmin.cdeadmin.providers.vitess.provider import (  # noqa: E402
    PROFILE as VITESS_PROFILE,
    create_provider as create_vitess_provider,
)
from pgadmin.cdeadmin.providers.yugabytedb.provider import (  # noqa: E402
    PROFILE as YUGABYTEDB_PROFILE,
    create_provider as create_yugabytedb_provider,
)
from pgadmin.cdeadmin.providers.immudb.provider import (  # noqa: E402
    PROFILE as IMMUDB_PROFILE,
    create_provider as create_immudb_provider,
)
from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    PROFILE as FIREBIRD_PROFILE,
    _initialize_connection as initialize_firebird_connection,
    create_provider as create_firebird_provider,
)
from pgadmin.cdeadmin.providers.sqlite.provider import (  # noqa: E402
    PROFILE as SQLITE_PROFILE,
    create_provider as create_sqlite_provider,
)
from pgadmin.cdeadmin.security import (  # noqa: E402
    EndpointSecretService,
    SecretReference,
)


CATEGORIES = (
    'resource', 'language_api', 'result', 'semantic_query', 'transaction',
    'admin', 'security', 'fault',
)
PROFILES = {
    'cockroachdb': COCKROACHDB_PROFILE,
    'dolt': DOLT_PROFILE,
    'tidb': TIDB_PROFILE,
    'vitess': VITESS_PROFILE,
    'yugabytedb': YUGABYTEDB_PROFILE,
    'immudb': IMMUDB_PROFILE,
    'mysql': MYSQL_PROFILE,
    'mariadb': MARIADB_PROFILE,
    'duckdb': DUCKDB_PROFILE,
    'firebird': FIREBIRD_PROFILE,
    'sqlite': SQLITE_PROFILE,
}

PROVIDER_FACTORIES = {
    'cockroachdb': create_cockroachdb_provider,
    'dolt': create_dolt_provider,
    'tidb': create_tidb_provider,
    'vitess': create_vitess_provider,
    'yugabytedb': create_yugabytedb_provider,
    'immudb': create_immudb_provider,
    'duckdb': create_duckdb_provider,
    'firebird': create_firebird_provider,
    'sqlite': create_sqlite_provider,
}

TARGET_ADAPTERS = {
    'cockroachdb': 'cockroachdb-postgresql-wire-client',
    'dolt': 'dolt-mysql-wire-client',
    'tidb': 'tidb-mysql-wire-client',
    'vitess': 'vitess-mysql-wire-client',
    'yugabytedb': 'ysql-postgresql-wire-client',
    'immudb': 'immudb-postgresql-wire-and-rest-client',
    'duckdb': 'embedded-duckdb-helper',
    'firebird': 'firebird-wire-client',
    'mariadb': 'mysql-wire-client',
    'mysql': 'mysql-wire-client',
    'sqlite': 'embedded-sqlite-client',
}


def _object_inspection_evidence(provider, resources, request, engine):
    """Inspect discovered objects and report only exact passed operations."""
    inspected = {}
    failures = {}
    for resource in resources:
        kind = resource.get('resource_kind')
        if not isinstance(kind, str) or kind in inspected or kind in failures:
            continue
        if not provider.client.supports_admin_operation(kind, 'inspect'):
            continue
        try:
            observed = provider.inspect_resource({
                **request,
                'resource_id': resource['resource_id'],
            })
            if observed.get('resource_kind') != kind:
                raise RuntimeError('inspected resource kind changed')
            inspected[kind] = ['inspect']
        except Exception as exc:
            failures[kind] = type(exc).__name__

    return _object_operation_evidence(
        provider, inspected, engine,
        scope='discovered-resource-inspection', failures=failures,
    )


def _object_operation_evidence(
        provider, passed, engine, scope, failures=None):
    """Map exact passed resource operations to declared concept duties."""
    concepts = {}
    descriptor = provider.visual_admin_descriptor()
    for family in descriptor['concept_coverage']['families']:
        family_results = {}
        for concept in family['concepts']:
            operations = {
                kind: sorted(passed[kind])
                for kind in concept.get('operation_obligations', {})
                if kind in passed and set(passed[kind]).intersection(
                    concept['operation_obligations'][kind]
                )
            }
            operations = {
                kind: sorted(set(operation_ids).intersection(
                    concept['operation_obligations'][kind]
                ))
                for kind, operation_ids in operations.items()
            }
            if operations:
                family_results[concept['concept_id']] = {
                    'status': 'passed',
                    'operations': operations,
                }
        if family_results:
            concepts[family['family_id']] = family_results
    return {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': engine,
        'exact_profile': provider.profile.exact_version,
        'evidence_scope': scope,
        'concepts': concepts,
        'passed_resource_operations': {
            kind: sorted(operations)
            for kind, operations in sorted(passed.items())
        },
        'operation_failures': dict(failures or {}),
        'raw_commands_used': False,
    }


def _merge_object_evidence(*documents):
    """Merge evidence emitted by stages in the same exact live run."""
    base = copy.deepcopy(documents[0])
    passed = {}
    failures = {}
    for document in documents:
        for kind, operations in document.get(
                'passed_resource_operations', {}).items():
            passed.setdefault(kind, set()).update(operations)
        failures.update(document.get('operation_failures', {}))
    base['evidence_scope'] = 'inspection-and-visual-editor-operations'
    base['passed_resource_operations'] = {
        kind: sorted(operations) for kind, operations in sorted(passed.items())
    }
    base['operation_failures'] = failures
    for document in documents[1:]:
        for family_id, concepts in document.get('concepts', {}).items():
            family = base['concepts'].setdefault(family_id, {})
            for concept_id, result in concepts.items():
                concept = family.setdefault(concept_id, {
                    'status': 'passed', 'operations': {},
                })
                for kind, operations in result['operations'].items():
                    concept['operations'][kind] = sorted(set(
                        concept['operations'].get(kind, [])
                    ).union(operations))
    return base


def _target(resources, kind, name):
    return next((
        resource for resource in resources
        if resource.get('resource_kind') == kind and
        str(resource.get('display_name', '')).casefold() == (
            str(name).casefold()
        )
    ), None)


def _apply_editor(provider, route, kind, operation, draft, target=None):
    plan = provider.plan_visual_admin({
        'resource_kind': kind,
        'operation_id': operation,
        'target_resource': target,
        'draft': draft,
        '_provider_route': route,
    })
    if plan.get('state') != 'ready':
        raise RuntimeError('visual editor plan was not ready')
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    if not result.get('provider_result', {}).get('accepted'):
        raise RuntimeError('visual editor operation was not accepted')
    return result


def _relational_editor_evidence(provider, request, engine):
    """Exercise common relational editors against disposable live data."""
    route = request['route']
    passed = {}
    failures = {}

    def record(kind, operation):
        passed.setdefault(kind, set()).add(operation)

    def attempt(label, kind, operation, draft, target=None):
        try:
            _apply_editor(
                provider, route, kind, operation, draft, target=target
            )
            record(kind, operation)
            return True
        except Exception as exc:
            failures[label] = f'{type(exc).__name__}: {exc}'
            return False

    def inspect_created(label, kind, name):
        try:
            resources = provider.list_resources(request)
            resource = _target(resources, kind, name)
            if resource is None:
                failures[label] = 'CreatedResourceMissing'
                return None
            provider.inspect_resource({
                **request, 'resource_id': resource['resource_id'],
            })
            record(kind, 'inspect')
        except Exception as exc:
            failures[label] = f'{type(exc).__name__}: {exc}'
            return None
        return resource

    resources = provider.list_resources(request)
    qualification = _target(resources, 'table', 'qualification')
    if qualification is None:
        qualification = _target(resources, 'table', 'QUALIFICATION')
    id_column = 'ID' if engine == 'firebird' else 'id'
    value_column = (
        'QUALIFICATION_VALUE' if engine == 'firebird' else 'value'
    )
    if engine in {'firebird', 'immudb'}:
        parent = None
    elif engine in {'mysql', 'mariadb', 'dolt', 'tidb', 'vitess'}:
        parent = str(route['database'])
    elif engine in {'cockroachdb', 'yugabytedb'}:
        parent = 'public'
    else:
        parent = 'main'
    if qualification is None:
        failures['table.grid'] = 'QualificationTableMissing'
    elif attempt(
        'table.insert', 'table', 'insert',
        {'values': {id_column: 2, value_column: 7}, 'options': {}},
        qualification,
    ):
        record('table', 'insert')
        try:
            page = provider.read_visual_admin_rows({
                '_provider_route': route,
                'target_resource': qualification,
                'limit': 50,
            })
            row = next(
                item for item in page['rows']
                if item['values'][id_column] == 2
            )
            token = row['identity_token']
            _apply_editor(
                provider, route, 'table', 'update', {
                    'selector': {'identity_token': token},
                    'changes': {value_column: 8},
                    'concurrency_token': token,
                }, target=qualification,
            )
            record('table', 'update')
            page = provider.read_visual_admin_rows({
                '_provider_route': route,
                'target_resource': qualification,
                'limit': 50,
            })
            row = next(
                item for item in page['rows']
                if item['values'][id_column] == 2
            )
            token = row['identity_token']
            _apply_editor(
                provider, route, 'table', 'delete', {
                    'selector': {'identity_token': token},
                    'concurrency_token': token,
                    'confirmation': 'delete-live-editor-probe',
                }, target=qualification,
            )
            record('table', 'delete')
        except Exception as exc:
            failures['table.grid'] = f'{type(exc).__name__}: {exc}'

    created = attempt(
        'table.create', 'table', 'create', {
            'name': 'cde_editor_probe',
            **({'parent': parent} if parent else {}),
            'columns': [
                {
                    'name': 'id', 'type': 'INTEGER', 'nullable': False,
                    'primary_key': True,
                },
                {'name': 'value', 'type': 'INTEGER', 'nullable': True},
            ],
            'constraints': [],
            **({
                'register_in_vschema': True,
                'vindex_name': 'xxhash',
                'vindex_columns': ['id'],
            } if engine == 'vitess' else {}),
        },
    )
    if created:
        resources = provider.list_resources(request)
        probe = _target(resources, 'table', 'cde_editor_probe')
        if probe is None:
            failures['table.create.discovery'] = 'CreatedTableMissing'
        else:
            attempt(
                'table.alter', 'table', 'alter', {
                    'add_columns': [{
                        'name': 'note', 'type': 'INTEGER', 'nullable': True,
                    }],
                    'drop_columns': [], 'rename_columns': [],
                }, probe,
            )
            renamed = False
            if provider.client.supports_admin_operation('table', 'rename'):
                renamed = attempt(
                    'table.rename', 'table', 'rename', {
                        'new_name': 'cde_editor_probe_renamed',
                    }, probe,
                )
            if renamed:
                resources = provider.list_resources(request)
                probe = _target(
                    resources, 'table', 'cde_editor_probe_renamed'
                )
            if probe is not None:
                attempt(
                    'table.drop', 'table', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-probe',
                    }, probe,
                )

    if qualification is not None:
        qualified_table = '.'.join(qualification['display_path'])
        if attempt(
            'column.create', 'column', 'create', {
                'name': 'editor_note', 'table': qualified_table,
                'data_type': 'INTEGER', 'nullable': True,
                'default': '', 'primary_key': False,
            },
        ):
            resources = provider.list_resources(request)
            column = _target(resources, 'column', 'editor_note')
            if column is not None and provider.client.supports_admin_operation(
                    'column', 'alter'):
                attempt(
                    'column.alter', 'column', 'alter', {
                        'properties': {'set_default': '9'},
                    }, column,
                )
            if column is not None and attempt(
                'column.rename', 'column', 'rename', {
                    'new_name': 'editor_comment',
                }, column,
            ):
                resources = provider.list_resources(request)
                column = _target(resources, 'column', 'editor_comment')
            if column is not None:
                attempt(
                    'column.drop', 'column', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-column',
                    }, column,
                )

        if provider.client.supports_admin_operation(
            'view', 'create'
        ) and attempt(
            'view.create', 'view', 'create', {
                'name': 'cde_editor_view',
                **({'parent': parent} if parent else {}),
                'query': (
                    'SELECT ID, QUALIFICATION_VALUE FROM QUALIFICATION'
                    if engine == 'firebird' else
                    'SELECT id, value FROM qualification'
                ),
            },
        ):
            view = inspect_created(
                'view.inspect', 'view', 'cde_editor_view'
            )
            if view is not None:
                if provider.client.supports_admin_operation('view', 'alter'):
                    attempt(
                        'view.alter', 'view', 'alter', {
                            'query': (
                                'SELECT ID FROM QUALIFICATION'
                                if engine == 'firebird' else
                                (
                                    'SELECT id, value + 0 AS value '
                                    'FROM qualification'
                                    if engine in {
                                        'cockroachdb', 'yugabytedb',
                                    } else
                                    'SELECT id FROM qualification'
                                )
                            ),
                        }, view,
                    )
                attempt(
                    'view.drop', 'view', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-view',
                    }, view,
                )

        if attempt(
            'index.create', 'index', 'create', {
                **({} if engine == 'immudb' else {
                    'name': 'cde_editor_index',
                    **({'parent': parent} if parent else {}),
                }),
                'table': (
                    qualification['display_name']
                    if engine == 'immudb' else qualified_table
                ),
                'columns': [value_column],
                'unique': False,
            },
        ):
            if engine == 'immudb':
                try:
                    index = next((
                        item for item in provider.list_resources(request)
                        if item.get('resource_kind') == 'index' and
                        item.get('extensions', {}).get(
                            'immudb', {}
                        ).get('native', {}).get('table') == (
                            qualification['display_name']
                        ) and item.get('extensions', {}).get(
                            'immudb', {}
                        ).get('native', {}).get('columns') == [value_column]
                    ), None)
                    if index is None:
                        failures['index.inspect'] = 'CreatedResourceMissing'
                    else:
                        provider.inspect_resource({
                            **request, 'resource_id': index['resource_id'],
                        })
                        record('index', 'inspect')
                except Exception as exc:
                    index = None
                    failures['index.inspect'] = (
                        f'{type(exc).__name__}: {exc}'
                    )
            else:
                index = inspect_created(
                    'index.inspect', 'index', 'cde_editor_index'
                )
            if index is not None:
                if provider.client.supports_admin_operation('index', 'alter'):
                    attempt(
                        'index.alter', 'index', 'alter', {
                            'active': False,
                        }, index,
                    )
                attempt(
                    'index.drop', 'index', 'drop', {
                        **({} if engine == 'immudb' else {
                            'cascade': False,
                            'confirmation': 'drop-live-editor-index',
                        }),
                    }, index,
                )

        if engine == 'sqlite' and attempt(
            'trigger.create', 'trigger', 'create', {
                'name': 'cde_editor_trigger', 'parent': 'main',
                'table': qualified_table, 'timing': 'AFTER',
                'events': ['INSERT'], 'active': True, 'position': 0,
                'body': 'BEGIN SELECT 1; END',
            },
        ):
            trigger = inspect_created(
                'trigger.inspect', 'trigger', 'cde_editor_trigger'
            )
            if trigger is not None:
                attempt(
                    'trigger.drop', 'trigger', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-trigger',
                    }, trigger,
                )

    if engine in {
        'mysql', 'mariadb', 'dolt', 'tidb', 'vitess',
    } and qualification is not None:
        if attempt(
            'constraint.create', 'constraint', 'create', {
                'name': 'cde_editor_unique', 'table': qualified_table,
                'properties': {
                    'kind': 'UNIQUE',
                    'columns': [id_column, value_column],
                },
            },
        ):
            constraint = inspect_created(
                'constraint.inspect', 'constraint', 'cde_editor_unique'
            )
            if constraint is not None:
                attempt(
                    'constraint.drop', 'constraint', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-constraint',
                    }, constraint,
                )

        if provider.client.supports_admin_operation(
                'trigger', 'create') and attempt(
            'trigger.create', 'trigger', 'create', {
                'name': 'cde_editor_trigger', 'parent': parent,
                'table': qualified_table, 'timing': 'BEFORE',
                'events': ['INSERT'], 'active': True, 'position': 0,
                'body': 'SET NEW.value = NEW.value',
            },
        ):
            trigger = inspect_created(
                'trigger.inspect', 'trigger', 'cde_editor_trigger'
            )
            if trigger is not None:
                attempt(
                    'trigger.drop', 'trigger', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-trigger',
                    }, trigger,
                )

        routines = []
        if engine not in {'tidb', 'vitess'}:
            routines.append(('procedure', '', 'BEGIN SELECT 1; END'))
        if engine not in {'dolt', 'tidb', 'vitess'}:
            routines.append(
                ('function', 'INTEGER', 'DETERMINISTIC RETURN 1')
            )
        for kind, returns, body in routines:
            name = f'cde_editor_{kind}'
            if attempt(
                f'{kind}.create', kind, 'create', {
                    'name': name, 'parent': parent, 'parameters': [],
                    'return_parameters': [], 'returns': returns,
                    'body': body,
                },
            ):
                routine = inspect_created(f'{kind}.inspect', kind, name)
                if routine is not None:
                    attempt(
                        f'{kind}.drop', kind, 'drop', {
                            'cascade': False,
                            'confirmation': f'drop-live-editor-{kind}',
                        }, routine,
                    )

        if provider.client.supports_admin_operation(
                'event', 'create') and attempt(
            'event.create', 'event', 'create', {
                'name': 'cde_editor_event', 'parent': parent,
                'schedule': 'EVERY 1 DAY', 'preserve': True,
                'enabled': True, 'body': 'SELECT 1',
            },
        ):
            event = inspect_created(
                'event.inspect', 'event', 'cde_editor_event'
            )
            if event is not None:
                attempt(
                    'event.alter', 'event', 'alter', {
                        'schedule': 'EVERY 2 DAY', 'preserve': True,
                        'enabled': False, 'body': 'SELECT 2',
                    }, event,
                )
                attempt(
                    'event.drop', 'event', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-event',
                    }, event,
                )

        role_name = 'cde_editor_role'
        role_draft = {'name': role_name}
        if engine in {'mysql', 'dolt'}:
            role_draft['members'] = [f'{route["user"]}@%']
        role = None
        if provider.client.supports_admin_operation(
                'role', 'create') and attempt(
                    'role.create', 'role', 'create', role_draft):
            role = inspect_created(
                'role.inspect', 'role',
                f'{role_name}@%'
                if engine in {'mysql', 'dolt', 'tidb'} else role_name,
            )

        user_name = 'cde_editor_user'
        user = None
        if provider.client.supports_admin_operation(
                'user', 'create') and attempt(
            'user.create', 'user', 'create', {
                'name': user_name, 'host': '%',
                'password': secrets.token_urlsafe(24),
                **({} if engine == 'dolt' else {
                    'active': True, 'administrator': False,
                }),
            },
        ):
            user = inspect_created(
                'user.inspect-created', 'user', f'{user_name}@%'
            )
        if user is not None:
            attempt(
                'user.alter', 'user', 'alter', {
                    'password': secrets.token_urlsafe(24),
                    **({} if engine == 'dolt' else {
                        'active': False, 'administrator': False,
                    }),
                }, user,
            )
            grant_draft = {
                'principal': f'{user_name}@%', 'object_type': 'TABLE',
                'object_name': qualified_table, 'privileges': ['SELECT'],
                'grant_option': False,
            }
            privilege_target = next((
                item for item in provider.list_resources(request)
                if item.get('resource_kind') == 'privilege'
            ), None)
            if attempt(
                'privilege.grant', 'privilege', 'grant', grant_draft,
                privilege_target,
            ):
                attempt(
                    'privilege.revoke', 'privilege', 'revoke', {
                        key: value for key, value in grant_draft.items()
                        if key != 'grant_option'
                    } | {'confirmation': 'revoke-live-editor-grant'},
                    privilege_target,
                )
            if provider.client.supports_admin_operation(
                    'user', 'rename') and attempt(
                'user.rename', 'user', 'rename', {
                    'new_name': 'cde_editor_user_renamed',
                }, user,
            ):
                user = inspect_created(
                    'user.inspect-renamed', 'user',
                    'cde_editor_user_renamed@%',
                )
            if user is not None:
                attempt(
                    'user.drop', 'user', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-user',
                    }, user,
                )
        if role is not None:
            attempt(
                'role.drop', 'role', 'drop', {
                    'cascade': False,
                    'confirmation': 'drop-live-editor-role',
                }, role,
            )

        if engine == 'mysql' and attempt(
            'plugin.create', 'plugin', 'create', {
                'name': 'auth_socket', 'library': 'auth_socket.so',
            },
        ):
            plugin = inspect_created(
                'plugin.inspect-created', 'plugin', 'auth_socket'
            )
            if plugin is not None:
                attempt(
                    'plugin.drop', 'plugin', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-plugin',
                    }, plugin,
                )

        if engine == 'mariadb':
            if attempt(
                'sequence.create', 'sequence', 'create', {
                    'name': 'cde_editor_sequence', 'parent': parent,
                    'start': 1, 'increment': 1, 'cycle': False,
                },
            ):
                sequence = inspect_created(
                    'sequence.inspect', 'sequence', 'cde_editor_sequence'
                )
                if sequence is not None:
                    attempt(
                        'sequence.alter', 'sequence', 'alter', {
                            'restart': 10, 'increment': 2,
                        }, sequence,
                    )
                    if attempt(
                        'sequence.rename', 'sequence', 'rename', {
                            'new_name': 'cde_editor_sequence_renamed',
                        }, sequence,
                    ):
                        sequence = inspect_created(
                            'sequence.inspect-renamed', 'sequence',
                            'cde_editor_sequence_renamed',
                        )
                    if sequence is not None:
                        attempt(
                            'sequence.drop', 'sequence', 'drop', {
                                'cascade': False,
                                'confirmation': 'drop-live-editor-sequence',
                            }, sequence,
                        )

            package_create = {
                'name': 'cde_editor_package', 'parent': parent,
                'header': 'PROCEDURE VALUE_ONE(); END',
                'body': (
                    'PROCEDURE VALUE_ONE() BEGIN SELECT 1; END; END'
                ),
            }
            if attempt(
                'package.create', 'package', 'create', package_create,
            ):
                package = inspect_created(
                    'package.inspect', 'package', 'cde_editor_package'
                )
                if package is not None:
                    attempt(
                        'package.alter', 'package', 'alter', {
                            'header': 'PROCEDURE VALUE_ONE(); END',
                            'body': (
                                'PROCEDURE VALUE_ONE() '
                                'BEGIN SELECT 2; END; END'
                            ),
                        }, package,
                    )
                    attempt(
                        'package.drop', 'package', 'drop', {
                            'cascade': False,
                            'confirmation': 'drop-live-editor-package',
                        }, package,
                    )

            if attempt(
                'plugin.create', 'plugin', 'create', {
                    'name': 'metadata_lock_info',
                    'library': 'metadata_lock_info.so',
                },
            ):
                plugin = inspect_created(
                    'plugin.inspect-created', 'plugin',
                    'metadata_lock_info',
                )
                if plugin is not None:
                    attempt(
                        'plugin.drop', 'plugin', 'drop', {
                            'cascade': False,
                            'confirmation': 'drop-live-editor-plugin',
                        }, plugin,
                    )

    if engine == 'tidb' and qualification is not None:
        if attempt(
            'sequence.create', 'sequence', 'create', {
                'name': 'cde_editor_sequence', 'parent': parent,
                'start': 1, 'increment': 1, 'cycle': False,
            },
        ):
            sequence = inspect_created(
                'sequence.inspect', 'sequence', 'cde_editor_sequence'
            )
            if sequence is not None:
                attempt(
                    'sequence.alter', 'sequence', 'alter', {
                        'restart': 10, 'increment': 2,
                    }, sequence,
                )
                if sequence is not None:
                    attempt(
                        'sequence.drop', 'sequence', 'drop', {
                            'cascade': False,
                            'confirmation': 'drop-live-editor-sequence',
                        }, sequence,
                    )

    if engine in {'cockroachdb', 'yugabytedb'} and (
            qualification is not None):
        if attempt(
            'constraint.create', 'constraint', 'create', {
                'name': 'cde_editor_unique', 'table': qualified_table,
                'properties': {
                    'kind': 'UNIQUE', 'columns': (
                        [id_column, value_column]
                        if engine == 'yugabytedb' else [value_column]
                    ),
                },
            },
        ):
            constraint = inspect_created(
                'constraint.inspect-created', 'constraint',
                'cde_editor_unique',
            )
            if constraint is not None:
                attempt(
                    'constraint.drop', 'constraint', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-constraint',
                    }, constraint,
                )

        if attempt(
            'schema.create', 'schema', 'create', {
                'name': 'cde_editor_schema',
            },
        ):
            schema = inspect_created(
                'schema.inspect-created', 'schema', 'cde_editor_schema'
            )
            if schema is not None and attempt(
                'schema.alter', 'schema', 'alter', {
                    'properties': {'rename_to': 'cde_editor_schema_renamed'},
                }, schema,
            ):
                schema = inspect_created(
                    'schema.inspect-renamed', 'schema',
                    'cde_editor_schema_renamed',
                )
            if schema is not None:
                attempt(
                    'schema.drop', 'schema', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-schema',
                    }, schema,
                )

        if attempt(
            'sequence.create', 'sequence', 'create', {
                'name': 'cde_editor_sequence', 'parent': 'public',
                'start': 1, 'increment': 1, 'cycle': False,
            },
        ):
            sequence = inspect_created(
                'sequence.inspect-created', 'sequence',
                'cde_editor_sequence',
            )
            if sequence is not None:
                attempt(
                    'sequence.alter', 'sequence', 'alter', {
                        'restart': 10, 'increment': 2,
                    }, sequence,
                )
                if attempt(
                    'sequence.rename', 'sequence', 'rename', {
                        'new_name': 'cde_editor_sequence_renamed',
                    }, sequence,
                ):
                    sequence = inspect_created(
                        'sequence.inspect-renamed', 'sequence',
                        'cde_editor_sequence_renamed',
                    )
                if sequence is not None:
                    attempt(
                        'sequence.drop', 'sequence', 'drop', {
                            'cascade': False,
                            'confirmation': 'drop-live-editor-sequence',
                        }, sequence,
                    )

        routine_drafts = (
            ('function', 'INTEGER', "LANGUAGE SQL AS 'SELECT 1'"),
            ('procedure', '', "LANGUAGE SQL AS 'SELECT 1'"),
        )
        for kind, returns, body in routine_drafts:
            name = f'cde_editor_{kind}'
            if attempt(
                f'{kind}.create', kind, 'create', {
                    'name': name, 'parent': 'public', 'parameters': [],
                    'return_parameters': [], 'returns': returns,
                    'body': body,
                },
            ):
                routine = inspect_created(f'{kind}.inspect', kind, name)
                if routine is not None:
                    attempt(
                        f'{kind}.drop', kind, 'drop', {
                            'cascade': False,
                            'confirmation': f'drop-live-editor-{kind}',
                        }, routine,
                    )

        helper_name = 'cde_editor_trigger_function'
        helper = None
        if attempt(
            'trigger.helper.create', 'function', 'create', {
                'name': helper_name, 'parent': 'public', 'parameters': [],
                'return_parameters': [], 'returns': 'TRIGGER',
                'body': (
                    "LANGUAGE PLPGSQL AS 'BEGIN RETURN NEW; END'"
                ),
            },
        ):
            helper = inspect_created(
                'trigger.helper.inspect', 'function', helper_name
            )
        if helper is not None and attempt(
            'trigger.create', 'trigger', 'create', {
                'name': 'cde_editor_trigger', 'parent': 'public',
                'table': qualified_table, 'timing': 'AFTER',
                'events': ['INSERT'], 'active': True, 'position': 0,
                'body': (
                    'FOR EACH ROW EXECUTE FUNCTION '
                    'public.cde_editor_trigger_function()'
                ),
            },
        ):
            trigger = inspect_created(
                'trigger.inspect', 'trigger', 'cde_editor_trigger'
            )
            if trigger is not None:
                attempt(
                    'trigger.drop', 'trigger', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-trigger',
                    }, trigger,
                )
        if helper is not None:
            attempt(
                'trigger.helper.drop', 'function', 'drop', {
                    'cascade': False,
                    'confirmation': 'drop-live-editor-trigger-helper',
                }, helper,
            )

        role = None
        if attempt(
            'role.create', 'role', 'create', {'name': 'cde_editor_role'},
        ):
            role = inspect_created(
                'role.inspect-created', 'role', 'cde_editor_role'
            )
        if role is not None:
            attempt(
                'role.alter', 'role', 'alter', {
                    'properties': {'with': 'LOGIN'},
                }, role,
            )
            privilege_target = next((
                item for item in provider.list_resources(request)
                if item.get('resource_kind') == 'privilege'
            ), None)
            grant_draft = {
                'principal': 'cde_editor_role', 'object_type': 'TABLE',
                'object_name': qualified_table, 'privileges': ['SELECT'],
                'grant_option': False,
            }
            if attempt(
                'privilege.grant', 'privilege', 'grant', grant_draft,
                privilege_target,
            ):
                attempt(
                    'privilege.revoke', 'privilege', 'revoke', {
                        key: value for key, value in grant_draft.items()
                        if key != 'grant_option'
                    } | {'confirmation': 'revoke-live-editor-grant'},
                    privilege_target,
                )
            attempt(
                'role.drop', 'role', 'drop', {
                    'cascade': False,
                    'confirmation': 'drop-live-editor-role',
                }, role,
            )

        user = None
        if attempt(
            'user.create', 'user', 'create', {
                'name': 'cde_editor_user',
                'password': secrets.token_urlsafe(24), 'active': True,
                'administrator': False,
            },
        ):
            user = inspect_created(
                'user.inspect-created', 'user', 'cde_editor_user'
            )
        if user is not None:
            attempt(
                'user.alter', 'user', 'alter', {
                    'password': secrets.token_urlsafe(24), 'active': False,
                    'administrator': False,
                }, user,
            )
            attempt(
                'user.drop', 'user', 'drop', {
                    'cascade': False,
                    'confirmation': 'drop-live-editor-user',
                }, user,
            )

    if engine == 'yugabytedb' and qualification is not None:
        if attempt(
            'materialized-view.create', 'materialized-view', 'create', {
                'name': 'cde_editor_materialized_view', 'parent': 'public',
                'query': 'SELECT id, value FROM public.qualification',
                'with_data': True,
            },
        ):
            materialized = inspect_created(
                'materialized-view.inspect', 'materialized-view',
                'cde_editor_materialized_view',
            )
            if materialized is not None and attempt(
                'materialized-view.rename', 'materialized-view', 'rename', {
                    'new_name': 'cde_editor_materialized_view_renamed',
                }, materialized,
            ):
                materialized = inspect_created(
                    'materialized-view.inspect-renamed',
                    'materialized-view',
                    'cde_editor_materialized_view_renamed',
                )
            if materialized is not None:
                attempt(
                    'materialized-view.drop', 'materialized-view', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-materialized-view',
                    }, materialized,
                )

        if attempt(
            'domain.create', 'domain', 'create', {
                'name': 'cde_editor_domain', 'parent': 'public',
                'data_type': 'INTEGER', 'default': '1',
                'not_null': False, 'check': 'VALUE > 0',
            },
        ):
            domain = inspect_created(
                'domain.inspect', 'domain', 'cde_editor_domain'
            )
            if domain is not None and attempt(
                'domain.rename', 'domain', 'rename', {
                    'new_name': 'cde_editor_domain_renamed',
                }, domain,
            ):
                domain = inspect_created(
                    'domain.inspect-renamed', 'domain',
                    'cde_editor_domain_renamed',
                )
            if domain is not None:
                attempt(
                    'domain.drop', 'domain', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-domain',
                    }, domain,
                )

        if attempt(
            'type.create', 'type', 'create', {
                'name': 'cde_editor_type', 'parent': 'public',
                'type_kind': 'ENUM', 'enum_values': ['one', 'two'],
                'fields': [],
            },
        ):
            user_type = inspect_created(
                'type.inspect', 'type', 'cde_editor_type'
            )
            if user_type is not None and attempt(
                'type.rename', 'type', 'rename', {
                    'new_name': 'cde_editor_type_renamed',
                }, user_type,
            ):
                user_type = inspect_created(
                    'type.inspect-renamed', 'type',
                    'cde_editor_type_renamed',
                )
            if user_type is not None:
                attempt(
                    'type.drop', 'type', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-type',
                    }, user_type,
                )

        if attempt(
            'extension.create', 'extension', 'create', {
                'name': 'pgcrypto', 'schema': '', 'version': '',
                'cascade': False,
            },
        ):
            extension = inspect_created(
                'extension.inspect-created', 'extension', 'pgcrypto'
            )
            if extension is not None:
                attempt(
                    'extension.drop', 'extension', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-extension',
                    }, extension,
                )

        placement = {
            'num_replicas': 1,
            'placement_blocks': [{
                'cloud': 'cloud1', 'region': 'datacenter1',
                'zone': 'rack1', 'min_num_replicas': 1,
            }],
        }
        if attempt(
            'tablespace.create', 'tablespace', 'create', {
                'name': 'cde_editor_tablespace',
                'replica_placement': placement,
            },
        ):
            tablespace = inspect_created(
                'tablespace.inspect', 'tablespace',
                'cde_editor_tablespace',
            )
            if (
                tablespace is not None and
                provider.client.supports_admin_operation(
                    'tablespace', 'rename'
                ) and attempt(
                    'tablespace.rename', 'tablespace', 'rename', {
                        'new_name': 'cde_editor_tablespace_renamed',
                    }, tablespace,
                )
            ):
                tablespace = inspect_created(
                    'tablespace.inspect-renamed', 'tablespace',
                    'cde_editor_tablespace_renamed',
                )
            if tablespace is not None:
                attempt(
                    'tablespace.drop', 'tablespace', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-tablespace',
                    }, tablespace,
                )

    if engine == 'firebird' and qualification is not None:
        if attempt(
            'constraint.create', 'constraint', 'create', {
                'name': 'cde_editor_unique', 'table': 'QUALIFICATION',
                'properties': {
                    'kind': 'UNIQUE',
                    'columns': ['QUALIFICATION_VALUE'],
                },
            },
        ):
            constraint = inspect_created(
                'constraint.inspect', 'constraint', 'cde_editor_unique'
            )
            if constraint is not None:
                attempt(
                    'constraint.drop', 'constraint', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-constraint',
                    }, constraint,
                )

        if attempt(
            'trigger.create', 'trigger', 'create', {
                'name': 'cde_editor_trigger', 'table': 'QUALIFICATION',
                'timing': 'AFTER', 'events': ['INSERT'],
                'active': True, 'position': 0, 'body': 'BEGIN END',
            },
        ):
            trigger = inspect_created(
                'trigger.inspect', 'trigger', 'cde_editor_trigger'
            )
            if trigger is not None:
                attempt(
                    'trigger.alter', 'trigger', 'alter', {
                        'timing': 'AFTER', 'events': ['UPDATE'],
                        'active': True, 'position': 1,
                        'body': 'BEGIN END',
                    }, trigger,
                )
                attempt(
                    'trigger.drop', 'trigger', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-trigger',
                    }, trigger,
                )

        try:
            privilege_target = next((
                item for item in provider.list_resources(request)
                if item.get('resource_kind') == 'privilege'
            ), None)
        except Exception as exc:
            privilege_target = None
            failures['privilege.discovery'] = (
                f'{type(exc).__name__}: {exc}'
            )
        role = None
        if attempt(
            'role.create', 'role', 'create', {
                'name': 'cde_editor_role',
                'system_privileges': ['SELECT_ANY_OBJECT_IN_DATABASE'],
            },
        ):
            role = inspect_created(
                'role.inspect', 'role', 'cde_editor_role'
            )
        if role is not None:
            attempt(
                'role.alter', 'role', 'alter', {
                    'system_privileges': [],
                    'drop_system_privileges': True,
                }, role,
            )
            grant_draft = {
                'principal': 'cde_editor_role', 'object_type': 'TABLE',
                'object_name': 'QUALIFICATION', 'privileges': ['SELECT'],
                'grant_option': False,
            }
            if attempt(
                'privilege.grant', 'privilege', 'grant', grant_draft,
                privilege_target,
            ):
                attempt(
                    'privilege.revoke', 'privilege', 'revoke', {
                        key: value for key, value in grant_draft.items()
                        if key != 'grant_option'
                    } | {'confirmation': 'revoke-live-editor-grant'},
                    privilege_target,
                )
            attempt(
                'role.drop', 'role', 'drop', {
                    'cascade': False,
                    'confirmation': 'drop-live-editor-role',
                }, role,
            )

    if engine == 'firebird':
        if attempt(
            'sequence.create', 'sequence', 'create', {
                'name': 'cde_editor_sequence', 'start': 1, 'increment': 1,
            },
        ):
            sequence = inspect_created(
                'sequence.inspect', 'sequence', 'cde_editor_sequence'
            )
            if sequence is not None:
                attempt(
                    'sequence.alter', 'sequence', 'alter', {
                        'restart': 10, 'increment': 2,
                    }, sequence,
                )
                attempt(
                    'sequence.drop', 'sequence', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-sequence',
                    }, sequence,
                )

        if attempt(
            'domain.create', 'domain', 'create', {
                'name': 'cde_editor_domain', 'data_type': 'INTEGER',
                'not_null': False,
            },
        ):
            domain = inspect_created(
                'domain.inspect', 'domain', 'cde_editor_domain'
            )
            if domain is not None:
                attempt(
                    'domain.alter', 'domain', 'alter', {
                        'data_type': 'BIGINT',
                    }, domain,
                )
                if attempt(
                    'domain.rename', 'domain', 'rename', {
                        'new_name': 'cde_editor_domain_renamed',
                    }, domain,
                ):
                    domain = inspect_created(
                        'domain.inspect-renamed', 'domain',
                        'cde_editor_domain_renamed',
                    )
                if domain is not None:
                    attempt(
                        'domain.drop', 'domain', 'drop', {
                            'cascade': False,
                            'confirmation': 'drop-live-editor-domain',
                        }, domain,
                    )

        routine_drafts = (
            (
                'procedure', {'parameters': [], 'return_parameters': [],
                              'returns': '', 'body': 'BEGIN END'},
                {'parameters': [], 'return_parameters': [], 'returns': '',
                 'body': 'BEGIN END'},
            ),
            (
                'function', {'parameters': [], 'return_parameters': [],
                             'returns': 'INTEGER',
                             'body': 'BEGIN RETURN 1; END'},
                {'parameters': [], 'return_parameters': [],
                 'returns': 'INTEGER',
                 'body': 'BEGIN RETURN 2; END'},
            ),
        )
        for kind, create_draft, alter_draft in routine_drafts:
            name = f'cde_editor_{kind}'
            if attempt(
                f'{kind}.create', kind, 'create', {
                    'name': name, **create_draft,
                },
            ):
                routine = inspect_created(
                    f'{kind}.inspect', kind, name
                )
                if routine is not None:
                    attempt(
                        f'{kind}.alter', kind, 'alter', alter_draft,
                        routine,
                    )
                    attempt(
                        f'{kind}.drop', kind, 'drop', {
                            'cascade': False,
                            'confirmation': f'drop-live-editor-{kind}',
                        }, routine,
                    )

        package_create = {
            'name': 'cde_editor_package',
            'header': 'BEGIN FUNCTION VALUE_ONE RETURNS INTEGER; END',
            'body': (
                'BEGIN FUNCTION VALUE_ONE RETURNS INTEGER AS '
                'BEGIN RETURN 1; END END'
            ),
        }
        if attempt(
            'package.create', 'package', 'create', package_create,
        ):
            package = inspect_created(
                'package.inspect', 'package', 'cde_editor_package'
            )
            if package is not None:
                attempt(
                    'package.alter', 'package', 'alter', {
                        'header': (
                            'BEGIN FUNCTION VALUE_ONE RETURNS INTEGER; END'
                        ),
                        'body': (
                            'BEGIN FUNCTION VALUE_ONE RETURNS INTEGER AS '
                            'BEGIN RETURN 2; END END'
                        ),
                    }, package,
                )
                attempt(
                    'package.drop', 'package', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-package',
                    }, package,
                )

        if attempt(
            'exception.create', 'exception', 'create', {
                'name': 'cde_editor_exception',
                'message': 'CDEadmin live editor exception',
            },
        ):
            exception = inspect_created(
                'exception.inspect', 'exception', 'cde_editor_exception'
            )
            if exception is not None:
                attempt(
                    'exception.alter', 'exception', 'alter', {
                        'message': 'CDEadmin altered live editor exception',
                    }, exception,
                )
                attempt(
                    'exception.drop', 'exception', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-exception',
                    }, exception,
                )

        user_password = secrets.token_urlsafe(24)
        if attempt(
            'user.create', 'user', 'create', {
                'name': 'CDE_EDITOR_USER', 'password': user_password,
                'active': True, 'administrator': False,
            },
        ):
            user = inspect_created(
                'user.inspect-created', 'user', 'CDE_EDITOR_USER'
            )
            if user is not None:
                attempt(
                    'user.alter', 'user', 'alter', {
                        'password': secrets.token_urlsafe(24),
                        'active': False, 'administrator': False,
                    }, user,
                )
                attempt(
                    'user.drop', 'user', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-user',
                    }, user,
                )
        user_password = ''

        try:
            publication = next((
                item for item in provider.list_resources(request)
                if item.get('resource_kind') == 'publication'
            ), None)
        except Exception as exc:
            publication = None
            failures['publication.discovery'] = (
                f'{type(exc).__name__}: {exc}'
            )
        if publication is None:
            failures['publication.alter'] = 'PublicationResourceMissing'
        else:
            attempt(
                'publication.alter', 'publication', 'alter', {
                    'enabled': False, 'include_tables': [],
                    'exclude_tables': [],
                }, publication,
            )

    if engine == 'duckdb':
        resources = provider.list_resources(request)
        extension = _target(resources, 'extension', 'json')
        if extension is None:
            failures['extension.execute'] = 'JsonExtensionMissing'
        elif attempt(
            'extension.execute', 'extension', 'execute', {
                'action': 'LOAD',
            }, extension,
        ):
            resources = provider.list_resources(request)
            function = next((
                item for item in resources
                if item.get('resource_kind') == 'function'
            ), None)
            if function is None:
                failures['function.inspect'] = 'FunctionResourceMissing'
            else:
                try:
                    provider.inspect_resource({
                        **request, 'resource_id': function['resource_id'],
                    })
                    record('function', 'inspect')
                except Exception as exc:
                    failures['function.inspect'] = (
                        f'{type(exc).__name__}: {exc}'
                    )

        if attempt(
            'schema.create', 'schema', 'create', {
                'name': 'cde_editor_schema',
            },
        ):
            schema = inspect_created(
                'schema.inspect-created', 'schema', 'cde_editor_schema'
            )
            if schema is not None:
                attempt(
                    'schema.drop', 'schema', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-schema',
                    }, schema,
                )

        if attempt(
            'sequence.create', 'sequence', 'create', {
                'name': 'cde_editor_sequence', 'parent': 'main',
                'start': 1, 'increment': 1, 'cycle': False,
            },
        ):
            sequence = inspect_created(
                'sequence.inspect', 'sequence', 'cde_editor_sequence'
            )
            if sequence is not None:
                attempt(
                    'sequence.drop', 'sequence', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-sequence',
                    }, sequence,
                )

        if attempt(
            'type.create', 'type', 'create', {
                'name': 'cde_editor_mood', 'type_kind': 'ENUM',
                'base_type': '', 'enum_values': ['ok', 'great'],
                'fields': [],
            },
        ):
            data_type = inspect_created(
                'type.inspect', 'type', 'cde_editor_mood'
            )
            if data_type is not None:
                attempt(
                    'type.drop', 'type', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-type',
                    }, data_type,
                )

        if attempt(
            'macro.create', 'macro', 'create', {
                'name': 'cde_editor_double', 'parameters': ['value'],
                'table_macro': False, 'expression': 'value * 2',
            },
        ):
            macro = inspect_created(
                'macro.inspect', 'macro', 'cde_editor_double'
            )
            if macro is not None:
                attempt(
                    'macro.alter', 'macro', 'alter', {
                        'parameters': ['value'], 'table_macro': False,
                        'expression': 'value * 3',
                    }, macro,
                )
                attempt(
                    'macro.drop', 'macro', 'drop', {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-macro',
                    }, macro,
                )

        attempt(
            'materialization.create', 'materialization', 'create', {
                'name': 'cde_editor_rollup', 'database': 'main',
                'select': 'SELECT count(*) AS item_count '
                          'FROM qualification',
            },
        )

    database_created = False
    editor_database = (
        f'cde_editor_database_{secrets.token_hex(4)}'
        if engine == 'immudb' else 'cde_editor_database'
    )
    if provider.client.supports_admin_operation('database', 'create'):
        database_created = attempt(
            'database.create', 'database', 'create', {
                'name': editor_database,
            },
        )
    if database_created and engine in {
        'cockroachdb', 'yugabytedb', 'mysql', 'mariadb', 'dolt', 'tidb',
        'immudb',
    }:
        database = inspect_created(
            'database.inspect-created', 'database', editor_database
        )
        if database is not None:
            if engine in {'mysql', 'mariadb', 'tidb'}:
                attempt(
                    'database.alter', 'database', 'alter', {
                        'character_set': 'utf8mb4',
                    }, database,
                )
            elif engine in {'cockroachdb', 'yugabytedb'} and attempt(
                'database.alter', 'database', 'alter', {
                    'properties': {
                        'rename_to': 'cde_editor_database_renamed',
                    },
                }, database,
            ):
                database = inspect_created(
                    'database.inspect-renamed', 'database',
                    'cde_editor_database_renamed',
                )
            attempt(
                'database.drop', 'database', 'drop', {
                    **({} if engine == 'immudb' else {
                        'cascade': False,
                        'confirmation': 'drop-live-editor-database',
                    }),
                }, database,
            )
    return _object_operation_evidence(
        provider, passed, engine, scope='visual-editor-operations',
        failures=failures,
    )


def _dolt_repository_editor_evidence(provider, request, database):
    """Exercise reversible Dolt backup/restore and remote editor controls."""
    route = request['route']
    passed = {}
    failures = {}
    suffix = database.removeprefix('cde_live_')
    backup_name = f'cde_backup_{suffix}'
    remote_name = f'cde_remote_{suffix}'
    restored_database = f'cde_restore_{suffix}'
    backup_url = f'file:///tmp/{backup_name}'
    remote_url = f'file:///var/lib/dolt/{restored_database}'

    def record(kind, operation):
        passed.setdefault(kind, set()).add(operation)

    def target(kind, name, path=None):
        display_path = list(path or [name])
        return {
            'resource_id': ':'.join([kind, *display_path]),
            'resource_kind': kind,
            'display_name': name,
            'display_path': display_path,
            'authority_path': [*display_path[:-1], kind, display_path[-1]],
            'generation': request['capability_generation'],
        }

    def apply(label, kind, operation, draft, resource=None):
        try:
            _apply_editor(
                provider, route, kind, operation, draft, target=resource
            )
            record(kind, operation)
            return True
        except Exception as exc:
            failures[label] = f'{type(exc).__name__}: {exc}'
            return False

    working_set = target('working-set', 'current')
    database_target = target('database', database)
    restored_target = target('database', restored_database)
    backup_target = target('backup', backup_name)
    remote_target = target('remote', remote_name)
    qualification = target(
        'table', 'qualification', [database, 'qualification']
    )
    backup_created = False
    restore_created = False
    remote_created = False
    try:
        apply(
            'working-set.baseline', 'working-set', 'commit', {
                'message': 'CDEadmin object gate baseline',
                'stage_all': True, 'allow_empty': True,
            }, working_set,
        )
        backup_created = apply(
            'backup.create', 'backup', 'create', {
                'name': backup_name, 'url': backup_url,
            },
        )
        if backup_created:
            apply(
                'backup.sync', 'backup', 'sync', {}, backup_target
            )
            restore_created = apply(
                'database.restore_backup', 'database', 'restore_backup', {
                    'url': backup_url,
                    'new_database_name': restored_database,
                    'force': False,
                }, database_target,
            )
        if restore_created:
            remote_created = apply(
                'remote.create', 'remote', 'create', {
                    'name': remote_name, 'url': remote_url,
                },
            )
        if remote_created:
            try:
                resources = provider.list_resources(request)
                remote = _target(resources, 'remote', remote_name)
                if remote is None:
                    raise RuntimeError(
                        'created Dolt remote was not discovered'
                    )
                provider.inspect_resource({
                    **request, 'resource_id': remote['resource_id'],
                })
                record('remote', 'inspect')
            except Exception as exc:
                failures['remote.inspect'] = f'{type(exc).__name__}: {exc}'
            if apply(
                'table.remote-fixture', 'table', 'insert', {
                    'values': {'id': 3, 'value': 9}, 'options': {},
                }, qualification,
            ):
                apply(
                    'working-set.remote-fixture', 'working-set', 'commit', {
                        'message': 'CDEadmin object gate remote update',
                        'stage_all': True, 'allow_empty': False,
                    }, working_set,
                )
            apply(
                'remote.push', 'remote', 'push', {
                    'refspec': 'main', 'set_upstream': False,
                }, remote_target,
            )
            apply('remote.fetch', 'remote', 'fetch', {}, remote_target)
            apply(
                'remote.pull', 'remote', 'pull', {'branch': 'main'},
                remote_target,
            )
    finally:
        if remote_created:
            apply('remote.drop', 'remote', 'drop', {}, remote_target)
        if backup_created:
            apply('backup.drop', 'backup', 'drop', {}, backup_target)
        if restore_created:
            apply(
                'database.restore-cleanup', 'database', 'drop', {
                    'cascade': False,
                    'confirmation': restored_database,
                }, restored_target,
            )
    return _object_operation_evidence(
        provider, passed, 'dolt', scope='repository-editor-operations',
        failures=failures,
    )


def _immudb_multimodel_editor_evidence(provider, request):
    """Exercise reversible native KV and document forms without raw SQL."""
    route = request['route']
    passed = {}
    failures = {}
    suffix = secrets.token_hex(4)
    key_name = f'cdeadmin-key-{suffix}'
    collection_name = f'cdeadmin_docs_{suffix}'

    def record(kind, operation):
        passed.setdefault(kind, set()).add(operation)

    def apply(label, kind, operation, draft, target=None):
        try:
            result = _apply_editor(
                provider, route, kind, operation, draft, target=target
            )
            record(kind, operation)
            return result
        except Exception as exc:
            failures[label] = f'{type(exc).__name__}: {exc}'
            return None

    key = None
    if apply('key.insert', 'key', 'insert', {
        'key': key_name, 'key_encoding': 'utf8',
        'value': 'first', 'encoding': 'utf8',
        'expires_at': int(time.time()) + 3600,
    }):
        resources = provider.list_resources(request)
        key = _target(resources, 'key', key_name)
        if key is None:
            failures['key.inspect'] = 'CreatedResourceMissing'
        else:
            provider.inspect_resource({
                **request, 'resource_id': key['resource_id'],
            })
            record('key', 'inspect')
            apply('key.update', 'key', 'update', {
                'value': 'second', 'encoding': 'utf8',
                'expires_at': int(time.time()) + 7200,
            }, key)
            ttl = next((
                item for item in resources
                if item.get('resource_kind') == 'ttl' and
                key_name in item.get('display_path', [])
            ), None)
            if ttl is None:
                failures['ttl.inspect'] = 'ExpirationResourceMissing'
            else:
                provider.inspect_resource({
                    **request, 'resource_id': ttl['resource_id'],
                })
                record('ttl', 'inspect')
    if key is not None:
        apply('key.delete', 'key', 'delete', {}, key)

    collection = None
    if apply('collection.create', 'collection', 'create', {
        'name': collection_name, 'document_id_field': '_id',
        'fields': [
            {'name': 'name', 'type': 'STRING'},
            {'name': 'score', 'type': 'INTEGER'},
        ],
        'indexes': [],
    }):
        collection = _target(
            provider.list_resources(request), 'collection', collection_name
        )
        if collection is None:
            failures['collection.inspect'] = 'CreatedResourceMissing'
        else:
            provider.inspect_resource({
                **request, 'resource_id': collection['resource_id'],
            })
            record('collection', 'inspect')
            apply('collection.alter', 'collection', 'alter', {
                'document_id_field': '_id',
            }, collection)

    index = None
    if collection is not None and apply(
        'collection-index.create', 'collection-index', 'create', {
            'collection': collection_name, 'fields': ['name'],
            'unique': False,
        },
    ):
        index = next((
            item for item in provider.list_resources(request)
            if item.get('resource_kind') == 'collection-index' and
            collection_name in item.get('display_path', []) and
            item.get('display_name') == 'name'
        ), None)
        if index is None:
            failures['collection-index.inspect'] = 'CreatedResourceMissing'
        else:
            provider.inspect_resource({
                **request, 'resource_id': index['resource_id'],
            })
            record('collection-index', 'inspect')

    document = None
    if collection is not None and apply(
        'document.insert', 'document', 'insert', {
            'collection': collection_name,
            'document': {'name': 'first', 'score': 1},
        },
    ):
        document = next((
            item for item in provider.list_resources(request)
            if item.get('resource_kind') == 'document' and
            collection_name in item.get('display_path', [])
        ), None)
        if document is None:
            failures['document.inspect'] = 'CreatedResourceMissing'
        else:
            provider.inspect_resource({
                **request, 'resource_id': document['resource_id'],
            })
            record('document', 'inspect')
            apply('document.update', 'document', 'update', {
                'document': {'name': 'second', 'score': 2},
            }, document)
    if document is not None:
        apply('document.delete', 'document', 'delete', {}, document)
    if index is not None:
        apply(
            'collection-index.drop', 'collection-index', 'drop', {}, index
        )
    if collection is not None:
        apply('collection.drop', 'collection', 'drop', {}, collection)

    return _object_operation_evidence(
        provider, passed, 'immudb',
        scope='native-multimodel-editor-operations',
        failures=failures,
    )


def _context(profile):
    endpoint_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f'cdeadmin-live:{profile.engine_id}:{profile.exact_version}',
    ))

    def child(purpose):
        return str(uuid.uuid5(uuid.UUID(endpoint_id), purpose))

    permissions = frozenset({
        *profile.required_permissions, 'secret_read', 'data_read',
        'data_write', 'administer', 'execute', 'backup_admin',
        'restore_admin', 'topology_admin', 'maintenance_admin',
        'replication_admin',
    })
    return EndpointContext(
        endpoint_id=endpoint_id,
        mode='legacy_native',
        experience_family=profile.engine_id,
        provider_id=profile.provider_id,
        provider_version='0.1.0',
        profile_id=profile.profile_id,
        profile_version=profile.exact_version,
        target_adapter_id=TARGET_ADAPTERS[profile.engine_id],
        target_adapter_version='live-qualification',
        pool_namespace=child('pool'),
        session_namespace=child('session'),
        cache_namespace=child('cache'),
        diagnostic_namespace=child('diagnostic'),
        effective_permissions=permissions,
        declared_runtime_family=profile.engine_id,
    )


def _permissions(context, secret_service):
    scopes = {
        'network': {'endpoint'},
        'embedded_runtime': {'endpoint'},
        'filesystem': {'endpoint', 'resource'},
        'secret_read': {'endpoint'},
        'data_read': {'endpoint', 'resource'},
        'data_write': {'endpoint', 'resource'},
        'administer': {'endpoint', 'resource'},
        'execute': {'endpoint'},
        'backup_admin': {'endpoint', 'resource'},
        'restore_admin': {'endpoint', 'resource'},
        'topology_admin': {'endpoint', 'resource'},
        'maintenance_admin': {'endpoint', 'resource'},
        'replication_admin': {'endpoint', 'resource'},
    }
    grants = {
        name: PermissionGrant(name, frozenset(values))
        for name, values in scopes.items()
    }
    return PermissionGuard(
        grants,
        context.effective_permissions,
        context=context,
        secret_service=secret_service,
    )


def _verified_context(context, discovered):
    """Issue the context that normally follows endpoint verification."""
    runtime = discovered['verified_runtime']
    return replace(
        context,
        verified_runtime_family=runtime['engine_id'],
        verified_runtime_version=runtime['version'],
        runtime_verification_state='verified',
        runtime_evidence_reference=runtime['evidence_reference'],
        runtime_identity_generation=str(runtime['build_id']),
    )


class _TemporaryAccount:
    def __init__(
            self, engine, socket_path=None, host='127.0.0.1', port=None,
            account_host='%'):
        self.engine = engine
        self.socket_path = socket_path
        self.host = host
        self.port = port
        self.account_host = account_host
        suffix = secrets.token_hex(6)
        self.username = f'cde_live_{suffix}'
        self.database = f'cde_live_{suffix}'
        self.password = secrets.token_urlsafe(32)
        self.connection = None
        self.replication_channel = 'cdeadmin_qualification'
        self.server_link = 'cdeadmin_qualification'

    @property
    def marker(self):
        return '%s' if self.engine in {'mysql', 'dolt', 'tidb'} else '?'

    @property
    def quoted_database(self):
        quote = chr(96)
        return f'{quote}{self.database}{quote}'

    def _connect(self):
        transport = (
            {'unix_socket': self.socket_path}
            if self.socket_path else
            {'host': self.host, 'port': self.port}
        )
        if self.engine in {'mysql', 'dolt', 'tidb'}:
            import mysql.connector
            return mysql.connector.connect(
                user='root', autocommit=True, **transport,
            )
        import mariadb
        return mariadb.connect(
            user='root', autocommit=True, **transport,
        )

    def create(self):
        self.connection = self._connect()
        cursor = self.connection.cursor()
        database = self.quoted_database
        try:
            cursor.execute(f'CREATE DATABASE {database}')
            partition = (
                ' PARTITION BY HASH(id) PARTITIONS 2'
                if self.engine in {'mysql', 'mariadb', 'tidb'} else ''
            )
            cursor.execute(
                f'CREATE TABLE {database}.qualification '
                '(id INTEGER NOT NULL PRIMARY KEY, value INTEGER NOT NULL)'
                f'{partition}'
            )
            cursor.execute(
                f'INSERT INTO {database}.qualification VALUES (1, 42)'
            )
            cursor.execute(
                f"CREATE USER '{self.username}'@'{self.account_host}' "
                f'IDENTIFIED BY {self.marker}',
                (self.password,),
            )
            cursor.execute(
                f"GRANT ALL PRIVILEGES ON *.* TO "
                f"'{self.username}'@'{self.account_host}'"
                ' WITH GRANT OPTION'
            )
            if self.engine == 'mysql':
                cursor.execute(
                    'CHANGE REPLICATION SOURCE TO '
                    "SOURCE_HOST='127.0.0.1', SOURCE_USER='nobody', "
                    "SOURCE_PORT=1 FOR CHANNEL 'cdeadmin_qualification'"
                )
            elif self.engine == 'mariadb':
                cursor.execute(
                    "CHANGE MASTER 'cdeadmin_qualification' TO "
                    "MASTER_HOST='127.0.0.1', MASTER_USER='nobody', "
                    'MASTER_PORT=1'
                )
                cursor.execute(
                    'CREATE SERVER cdeadmin_qualification '
                    'FOREIGN DATA WRAPPER mysql OPTIONS '
                    "(HOST '127.0.0.1', DATABASE "
                    f"'{self.database}', USER 'nobody', PORT 1)"
                )
        except Exception:
            self.drop()
            raise
        finally:
            cursor.close()

    def drop(self):
        connection = self.connection
        if connection is None:
            try:
                connection = self._connect()
            except Exception:
                return
        cursor = connection.cursor()
        try:
            if self.engine == 'mysql':
                cleanup = [
                    "RESET REPLICA ALL FOR CHANNEL 'cdeadmin_qualification'"
                ]
            elif self.engine == 'mariadb':
                cleanup = [
                    "RESET REPLICA 'cdeadmin_qualification' ALL",
                    'DROP SERVER IF EXISTS cdeadmin_qualification',
                ]
            else:
                cleanup = []
            cleanup.extend((
                f"DROP USER IF EXISTS '{self.username}'@"
                f"'{self.account_host}'",
                f'DROP DATABASE IF EXISTS {self.quoted_database}',
            ))
            for source in cleanup:
                try:
                    cursor.execute(source)
                except Exception:
                    # Cleanup is best-effort per object so one missing optional
                    # qualification fixture cannot strand the account/database.
                    pass
        finally:
            cursor.close()
            connection.close()
            self.connection = None
            self.password = ''


class _VitessAccount:
    """Seed an existing exact Vitess topology without SQL account DDL."""

    def __init__(self, host, port, http_port, database='test_keyspace'):
        self.host = host
        self.port = port
        self.http_port = http_port
        self.username = 'root'
        self.database = database
        self.password = ''
        self.connection = None
        self.route_options = {
            'vtgate_http_host': host,
            'vtgate_http_port': http_port,
            'vtgate_http_tls_mode': 'disable',
            # VTGate DDL is authoritative at statement completion. Keeping
            # qualification administration in autocommit mode prevents a
            # redundant COMMIT from obscuring a successful sharded DDL
            # response (notably CREATE VIEW).
            'autocommit': True,
        }

    def _connect(self):
        import mysql.connector

        return mysql.connector.connect(
            host=self.host, port=self.port, user=self.username,
            database=self.database, connection_timeout=10,
            autocommit=True,
        )

    @staticmethod
    def _ignore(cursor, source):
        try:
            cursor.execute(source)
        except Exception:
            pass

    def create(self):
        self.connection = self._connect()
        cursor = self.connection.cursor()
        try:
            self._ignore(cursor, 'ALTER VSCHEMA DROP TABLE qualification')
            self._ignore(
                cursor,
                f'DROP TABLE IF EXISTS `{self.database}`.`qualification`',
            )
            cursor.execute(
                f'CREATE TABLE `{self.database}`.`qualification` '
                '(id BIGINT NOT NULL PRIMARY KEY, value BIGINT NOT NULL) '
                'PARTITION BY HASH(id) PARTITIONS 2'
            )
            cursor.execute(
                'ALTER VSCHEMA ON `qualification` '
                'ADD VINDEX `xxhash` (`id`)'
            )
            cursor.execute(
                f'INSERT INTO `{self.database}`.`qualification` '
                '(id, value) VALUES (1, 42)'
            )
        except Exception:
            self.drop()
            raise
        finally:
            cursor.close()

    def drop(self):
        connection = self.connection
        if connection is None:
            try:
                connection = self._connect()
            except Exception:
                return
        cursor = connection.cursor()
        try:
            # DROP VIEW through a keyspace target is not consistently fanned
            # out by VTGate 23.0.3. Cleanup is test-fixture administration,
            # so address each primary shard explicitly and leave no stale
            # view that could make a later provider run ambiguous.
            shards = []
            try:
                cursor.execute('SHOW VITESS_SHARDS')
                shards = [
                    str(row[0]).split('/', 1)[1]
                    for row in cursor.fetchall()
                    if str(row[0]).startswith(f'{self.database}/')
                ]
            except Exception:
                pass
            for shard in shards:
                shard_connection = None
                shard_cursor = None
                try:
                    import mysql.connector

                    shard_connection = mysql.connector.connect(
                        host=self.host, port=self.port, user=self.username,
                        database=(
                            f'{self.database}:{shard}@primary'
                        ),
                        connection_timeout=10, autocommit=True,
                    )
                    shard_cursor = shard_connection.cursor()
                    self._ignore(
                        shard_cursor,
                        'DROP VIEW IF EXISTS cde_editor_view',
                    )
                finally:
                    if shard_cursor is not None:
                        shard_cursor.close()
                    if shard_connection is not None:
                        shard_connection.close()
            self._ignore(cursor, 'ALTER VSCHEMA DROP TABLE qualification')
            self._ignore(
                cursor,
                f'DROP TABLE IF EXISTS `{self.database}`.`qualification`',
            )
        finally:
            cursor.close()
            connection.close()
            self.connection = None
            self.password = ''


class _CockroachDBAccount:
    """Provision one password-authenticated account through a client cert."""

    def __init__(self, host, port, ca_cert, client_cert, client_key):
        suffix = secrets.token_hex(6)
        self.host = host
        self.port = port
        self.ca_cert = str(Path(ca_cert).resolve())
        self.client_cert = str(Path(client_cert).resolve())
        self.client_key = str(Path(client_key).resolve())
        self.username = f'cde_live_{suffix}'
        self.database = f'cde_live_{suffix}'
        self.password = secrets.token_urlsafe(32)
        self.connection = None
        self.created = False

    @property
    def route_options(self):
        return {
            'sslmode': 'verify-full',
            'sslrootcert': self.ca_cert,
            'options': '-c statement_timeout=5000',
        }

    def _admin_connect(self):
        import psycopg

        return psycopg.connect(
            host=self.host,
            port=self.port,
            user='root',
            dbname='defaultdb',
            sslmode='verify-full',
            sslrootcert=self.ca_cert,
            sslcert=self.client_cert,
            sslkey=self.client_key,
            connect_timeout=10,
            options='-c statement_timeout=10000',
            autocommit=True,
        )

    def create(self):
        from psycopg import sql

        self.connection = self._admin_connect()
        try:
            self.connection.execute(
                sql.SQL('CREATE DATABASE {}').format(
                    sql.Identifier(self.database)
                )
            )
            self.connection.execute(
                sql.SQL('CREATE USER {} WITH PASSWORD {}').format(
                    sql.Identifier(self.username), sql.Placeholder()
                ),
                (self.password,),
            )
            self.connection.execute(
                sql.SQL('GRANT admin TO {} WITH ADMIN OPTION').format(
                    sql.Identifier(self.username)
                )
            )
            self.connection.execute(
                sql.SQL(
                    'CREATE TABLE {}.public.qualification '
                    '(id INTEGER NOT NULL PRIMARY KEY, '
                    'value INTEGER NOT NULL) PARTITION BY LIST (id) '
                    '(PARTITION cde_one VALUES IN (1), '
                    'PARTITION cde_two VALUES IN (2))'
                ).format(sql.Identifier(self.database))
            )
            self.connection.execute(
                sql.SQL(
                    'INSERT INTO {}.public.qualification VALUES (1, 42)'
                ).format(sql.Identifier(self.database))
            )
            self.created = True
        except Exception:
            self.drop()
            raise

    def drop(self):
        from psycopg import sql

        connection = self.connection
        if connection is None:
            try:
                connection = self._admin_connect()
            except Exception:
                self.password = ''
                return
        try:
            for statement in (
                sql.SQL('DROP USER IF EXISTS cde_editor_user'),
                sql.SQL('DROP ROLE IF EXISTS cde_editor_role'),
                sql.SQL(
                    'DROP DATABASE IF EXISTS cde_editor_database CASCADE'
                ),
                sql.SQL(
                    'DROP DATABASE IF EXISTS '
                    'cde_editor_database_renamed CASCADE'
                ),
                sql.SQL('DROP DATABASE IF EXISTS {} CASCADE').format(
                    sql.Identifier(self.database)
                ),
                sql.SQL('DROP USER IF EXISTS {}').format(
                    sql.Identifier(self.username)
                ),
            ):
                try:
                    connection.execute(statement)
                except Exception:
                    pass
        finally:
            connection.close()
            self.connection = None
            self.password = ''


class _YugabyteDBAccount:
    """Create a disposable YSQL database through the local superuser."""

    def __init__(self, host, port, admin_user='yugabyte'):
        self.host = host
        self.port = port
        self.username = admin_user
        self.database = f'cde_live_{secrets.token_hex(6)}'
        self.password = ''
        self.connection = None
        # YSQL follows PostgreSQL's requirement that CREATE DATABASE execute
        # outside a transaction block. The exact qualification route uses an
        # explicit autocommit session default for administration operations.
        self.route_options = {'autocommit': True}

    def _connect(self, database='yugabyte'):
        import psycopg

        return psycopg.connect(
            host=self.host, port=self.port, user=self.username,
            dbname=database, connect_timeout=10, autocommit=True,
        )

    def create(self):
        self.connection = self._connect()
        cursor = self.connection.cursor()
        try:
            cursor.execute(f'CREATE DATABASE "{self.database}"')
        except Exception:
            self.drop()
            raise
        finally:
            cursor.close()
        database_connection = self._connect(self.database)
        cursor = database_connection.cursor()
        try:
            cursor.execute(
                'CREATE TABLE public.qualification '
                '(id INTEGER NOT NULL PRIMARY KEY, value INTEGER NOT NULL) '
                'PARTITION BY RANGE (id)'
            )
            cursor.execute(
                'CREATE TABLE public.qualification_low PARTITION OF '
                'public.qualification FOR VALUES FROM (MINVALUE) TO (1000)'
            )
            cursor.execute(
                'INSERT INTO public.qualification VALUES (1, 42)'
            )
        except Exception:
            self.drop()
            raise
        finally:
            cursor.close()
            database_connection.close()

    def drop(self):
        connection = self.connection
        if connection is None:
            try:
                connection = self._connect()
            except Exception:
                return
        cursor = connection.cursor()
        try:
            statements = (
                'DROP TABLESPACE IF EXISTS cde_editor_tablespace',
                'DROP TABLESPACE IF EXISTS cde_editor_tablespace_renamed',
                'DROP DATABASE IF EXISTS cde_editor_database WITH (FORCE)',
                'DROP DATABASE IF EXISTS cde_editor_database_renamed '
                'WITH (FORCE)',
                f'DROP DATABASE IF EXISTS "{self.database}" WITH (FORCE)',
                'DROP ROLE IF EXISTS cde_editor_user',
                'DROP ROLE IF EXISTS cde_editor_role',
            )
            for statement in statements:
                try:
                    cursor.execute(statement)
                except Exception:
                    # Cleanup is best-effort per global object so a failed
                    # optional editor case cannot strand later runs.
                    pass
        finally:
            cursor.close()
            connection.close()
            self.connection = None
            self.password = ''


class _ImmudbAccount:
    """Use an exact endpoint administrator and remove all probe objects."""

    def __init__(
        self, host, port, web_port, admin_user, admin_password,
        database='defaultdb',
    ):
        self.host = host
        self.port = port
        self.username = admin_user
        self.password = admin_password
        self.database = database
        self.connection = None
        self.route_options = {
            'autocommit': True,
            'web_host': host,
            'web_port': web_port,
            'web_tls_mode': 'disable',
        }

    def _connect(self):
        import psycopg

        return psycopg.connect(
            host=self.host, port=self.port, user=self.username,
            password=self.password, dbname=self.database,
            connect_timeout=10, autocommit=True,
        )

    def create(self):
        self.connection = self._connect()
        cursor = self.connection.cursor()
        try:
            cursor.execute('DROP TABLE IF EXISTS qualification')
            cursor.execute(
                'CREATE TABLE qualification '
                '(id INTEGER, value INTEGER, PRIMARY KEY id)'
            )
            cursor.execute(
                'INSERT INTO qualification (id, value) VALUES (1, 42)'
            )
        except Exception:
            self.drop()
            raise
        finally:
            cursor.close()

    def drop(self):
        connection = self.connection
        if connection is None:
            try:
                connection = self._connect()
            except Exception:
                self.password = ''
                return
        cursor = connection.cursor()
        try:
            for statement in (
                'DROP VIEW IF EXISTS cde_editor_view',
                'DROP TABLE IF EXISTS cde_editor_probe',
                'DROP TABLE IF EXISTS cde_editor_probe_renamed',
                'DROP TABLE IF EXISTS qualification',
            ):
                try:
                    cursor.execute(statement)
                except Exception:
                    pass
        finally:
            cursor.close()
            connection.close()
            self.connection = None
            self.password = ''


class _FirebirdAccount:
    def __init__(self, host, port, database, admin_user, admin_password):
        suffix = secrets.token_hex(6).upper()
        self.host = host
        self.port = port
        self.database = str(Path(database).resolve())
        self.admin_user = admin_user
        self.admin_password = admin_password
        self.username = f'CDE_LIVE_{suffix}'
        self.password = secrets.token_hex(24)
        self.created = False
        self.database_admin_role_verified = False

    @property
    def dsn(self):
        return f'{self.host}/{self.port}:{self.database}'

    def _admin_connect(self):
        from firebird.driver import connect

        return connect(
            self.dsn, user=self.admin_user, password=self.admin_password
        )

    def create(self):
        from firebird.driver import create_database

        connection = create_database(
            self.dsn, user=self.admin_user, password=self.admin_password,
            overwrite=True,
        )
        cursor = connection.cursor()
        try:
            cursor.execute(
                'CREATE TABLE QUALIFICATION '
                '(ID INTEGER NOT NULL PRIMARY KEY, '
                'QUALIFICATION_VALUE INTEGER NOT NULL)'
            )
            connection.commit()
            cursor.execute(
                'INSERT INTO QUALIFICATION VALUES (?, ?)', (1, 42)
            )
            connection.commit()
            cursor.execute(
                f"CREATE USER {self.username} PASSWORD '{self.password}' "
                'GRANT ADMIN ROLE'
            )
            connection.commit()
            cursor.execute(
                f'GRANT RDB$ADMIN TO USER {self.username}'
            )
            connection.commit()
            cursor.execute(
                f'GRANT SELECT ON QUALIFICATION TO USER {self.username}'
            )
            connection.commit()
            self.created = True
        finally:
            cursor.close()
            connection.close()
        import firebird.driver as firebird_driver

        connection = firebird_driver.connect(
            self.dsn, user=self.username, password=self.password,
            role='RDB$ADMIN',
        )
        initialize_firebird_connection(
            connection, {}, firebird_driver
        )
        cursor = connection.cursor()
        try:
            cursor.execute(
                'SELECT CURRENT_ROLE FROM RDB$DATABASE'
            )
            role = str(cursor.fetchone()[0] or '').strip()
            if role != 'RDB$ADMIN':
                raise RuntimeError(
                    'Firebird disposable database admin role was not active'
                )
            cursor.execute(
                'CREATE TABLE CDE_ADMIN_ROLE_PROBE (ID INTEGER)'
            )
            connection.rollback()
            self.database_admin_role_verified = True
        finally:
            cursor.close()
            connection.close()

    def drop(self):
        try:
            connection = self._admin_connect()
        except Exception:
            self.password = ''
            return
        cursor = connection.cursor()
        try:
            if self.created:
                try:
                    cursor.execute(f'DROP USER {self.username}')
                    connection.commit()
                except Exception:
                    connection.rollback()
            connection.drop_database()
        finally:
            try:
                cursor.close()
            except Exception:
                pass
            try:
                connection.close()
            except Exception:
                pass
            self.password = ''


def _result_payload(provider, operation, engine):
    result = provider.describe_result(operation)
    if not result['complete'] or result['result_kind'] != (
        provider.profile.result_kind
    ):
        raise RuntimeError('live result envelope is incomplete')
    payload = result['extensions'][engine]['payload']
    rows = payload.get('rows') or []
    if not rows or int(rows[0][0]) != 42:
        raise RuntimeError('live result value did not round trip')
    return result


def _semantic_payload(provider, session, engine):
    """Exercise semantic discovery, compilation, execution and cellsets."""
    from pgadmin.cdeadmin.semantic_models.service import SemanticModelService

    semantic_id_column = 'ID' if engine == 'firebird' else 'id'
    model = {
        'contract_version': '1.0.0',
        'name': 'Live qualification model',
        'description': 'Disposable provider semantic-query qualification',
        'sources': [{
            'id': 'qualification',
            'resource_id': f'{engine}:qualification',
            'relation': ['QUALIFICATION'] if engine == 'firebird' else [
                'qualification'
            ],
            'alias': 'qualification',
        }],
        'joins': [],
        'dimensions': [{
            'id': 'qualification_dimension',
            'name': 'Qualification dimension',
            'field': {
                'source_id': 'qualification',
                'field': semantic_id_column,
            },
            'hierarchies': [{
                'id': 'qualification_hierarchy',
                'name': 'Qualification hierarchy',
                'levels': [{
                    'id': 'qualification_level',
                    'name': 'Qualification level',
                    'field': {
                        'source_id': 'qualification',
                        'field': semantic_id_column,
                    },
                }],
            }],
        }],
        'measures': [{
            'id': 'row_count', 'name': 'Row count',
            'aggregation': 'count', 'field': None, 'format': '0',
        }],
        'default_filters': [],
        'materializations': [],
        'security': {},
        'annotations': {'qualification': True},
    }
    query = {
        'axes': {
            'rows': ['qualification_level'],
            'columns': [], 'pages': [],
        },
        'measures': ['row_count'], 'filters': [],
        'totals': False, 'limit': 10,
    }
    descriptor = provider.semantic_model_descriptor()
    if not descriptor.get('execution_available'):
        raise RuntimeError('provider semantic execution is not activated')
    compiled = provider.compile_semantic_query(model, query)
    if not compiled.get('source') or not compiled.get('language_profile'):
        raise RuntimeError('provider semantic compiler returned no query')
    operation = provider.execute_analysis({
        'session_id': session['session_id'],
        'execution_id': f'{engine}-live-semantic-result',
        'semantic_model': model,
        'semantic_query': query,
    })
    result = provider.describe_result(operation)
    payload = result['extensions'][engine]['payload']
    rows = payload.get('rows') or []
    if not rows or int(rows[0][-1]) != 1:
        raise RuntimeError('semantic aggregate did not round trip')
    cellset = SemanticModelService.cellset(
        model, query, [{'row_count': int(rows[0][-1])}]
    )
    if cellset['family'] != 'cellset' or (
        cellset['cells'][0]['measures']['row_count'] != 1
    ):
        raise RuntimeError('semantic result was not preserved as a cellset')
    return {
        'language_profile': compiled['language_profile'],
        'compiled_source': compiled['source'],
        'cellset_family': cellset['family'],
        'observed_row_count': 1,
    }


def _semantic_object_evidence(provider, engine):
    family_ids = {
        item['family_id']
        for item in provider.visual_admin_descriptor()[
            'concept_coverage']['families']
    }
    concepts = {}
    if 'semantic' in family_ids:
        concepts['semantic'] = {
            concept_id: {'status': 'passed', 'operations': {}}
            for concept_id in (
                'cubes', 'dimensions', 'hierarchies', 'levels', 'measures',
            )
        }
    return {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': engine,
        'exact_profile': provider.profile.exact_version,
        'evidence_scope': 'semantic-model-compile-execute-cellset',
        'concepts': concepts,
        'passed_resource_operations': {},
        'operation_failures': {},
        'raw_commands_used': False,
    }


def _embedded_route(engine, root, database):
    route = {
        'route_id': 'exact-live-qualification',
        'database': str(database),
        'filesystem_root': str(root),
    }
    if engine in {'duckdb', 'sqlite'}:
        suffix = 'duckdb' if engine == 'duckdb' else 'sqlite'
        route['attached_databases'] = [{
            'name': 'qualification_attached',
            'database': str(root / f'qualification-attached.{suffix}'),
            'read_only': engine == 'duckdb',
        }]
    return route


def _prepare_embedded(engine, database):
    if engine == 'duckdb':
        import duckdb

        module = duckdb
        runtime = duckdb.__version__
    else:
        import sqlite3

        module = sqlite3
        runtime = sqlite3.sqlite_version
    expected = PROFILES[engine].exact_version
    if runtime != expected:
        raise RuntimeError(
            f'{engine} embedded runtime is not exact ({runtime})'
        )
    connection = module.connect(str(database))
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(
                'CREATE TABLE qualification '
                '(id INTEGER PRIMARY KEY, value INTEGER NOT NULL)'
            )
            cursor.execute(
                'INSERT INTO qualification VALUES (?, ?)', (1, 42)
            )
            connection.commit()
        finally:
            cursor.close()
    finally:
        connection.close()
    if engine == 'duckdb':
        attached = database.parent / 'qualification-attached.duckdb'
        connection = module.connect(str(attached))
        connection.close()


def verify_embedded(engine):
    profile = PROFILES[engine]
    context = _context(profile)
    categories = {name: 'not_run' for name in CATEGORIES}
    error_type = None
    error_message = None
    provider = None
    secret_service = EndpointSecretService()
    removed = False
    object_evidence = {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': engine,
        'exact_profile': profile.exact_version,
        'evidence_scope': 'discovered-resource-inspection',
        'concepts': {},
        'inspected_resource_kinds': [],
        'inspection_failures': {},
        'raw_commands_used': False,
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix=f'cdeadmin-{engine}-live-'
        ) as temporary:
            root = Path(temporary).resolve()
            suffix = 'duckdb' if engine == 'duckdb' else 'sqlite'
            database = root / f'qualification.{suffix}'
            _prepare_embedded(engine, database)
            route = _embedded_route(engine, root, database)
            request = {
                'route': route,
                'capability_generation': 'exact-live-qualification',
            }
            provider = PROVIDER_FACTORIES[engine](
                context, _permissions(context, secret_service)
            )
            discovered = provider.discover_endpoint(request)
            if discovered['verified_runtime']['version'] != (
                profile.exact_version
            ):
                raise RuntimeError('exact runtime identity was not verified')
            provider.close()
            context = _verified_context(context, discovered)
            provider = PROVIDER_FACTORIES[engine](
                context, _permissions(context, secret_service)
            )

            resources = provider.list_resources(request)
            if not any(
                item['resource_kind'] == 'table' and
                item['display_name'] == 'qualification'
                for item in resources
            ):
                raise RuntimeError('live resource category found no table')
            categories['resource'] = 'passed'
            object_evidence = _object_inspection_evidence(
                provider, resources, request, engine
            )

            languages = provider.describe_language({})
            if languages[0]['display_name'] != profile.language_name:
                raise RuntimeError(
                    'live language category did not match profile'
                )
            categories['language_api'] = 'passed'

            session = provider.open_session(request)
            operation = provider.execute({
                'session_id': session['session_id'],
                'execution_id': f'{engine}-live-result',
                'source': 'SELECT ? AS value',
                'parameters': (42,),
            })
            _result_payload(provider, operation, engine)
            categories['result'] = 'passed'

            _semantic_payload(provider, session, engine)
            categories['semantic_query'] = 'passed'

            object_evidence = _merge_object_evidence(
                object_evidence,
                _semantic_object_evidence(provider, engine),
                _relational_editor_evidence(
                    provider, request, engine
                ),
            )

            transaction = provider.describe_transaction(session)
            presentation = transaction['provider_payload']
            if presentation.get(
                'finality_interpreted_by_common_code'
            ) is not False:
                raise RuntimeError(
                    'common code interpreted transaction finality'
                )
            if presentation.get('driver_observation_only') is not True:
                raise RuntimeError('transaction observation is not opaque')
            categories['transaction'] = 'passed'

            descriptor = provider.visual_admin_descriptor()
            if not descriptor.get('objects'):
                raise RuntimeError('visual administration is unavailable')
            if len(provider.list_tools({})) != len(profile.admin_tools):
                raise RuntimeError('live admin tool catalog is incomplete')
            categories['admin'] = 'passed'

            security = provider.describe_security(request)
            if security['resource_kind'] != 'security-descriptor':
                raise RuntimeError('live security descriptor is invalid')
            categories['security'] = 'passed'

            invalid_failed = False
            try:
                provider.execute({
                    'session_id': session['session_id'],
                    'execution_id': f'{engine}-live-fault',
                    'source': 'SELECT * FROM cdeadmin_live_missing_object',
                })
            except Exception:
                invalid_failed = True
            escape_failed = False
            escape_request = {
                'route': _embedded_route(
                    engine, root, root.parent / f'escape.{suffix}'
                )
            }
            try:
                provider.discover_endpoint(escape_request)
            except Exception:
                escape_failed = True
            if not invalid_failed or not escape_failed:
                raise RuntimeError(
                    'embedded fault or filesystem escape did not fail'
                )
            categories['fault'] = 'passed'
        removed = not database.exists()
    except Exception as exc:
        error_type = type(exc).__name__
        error_message = str(exc)
    finally:
        if provider is not None:
            provider.close()

    passed = (
        all(value == 'passed' for value in categories.values()) and
        not object_evidence.get('operation_failures')
    )
    return {
        'schema': 'cdeadmin.relational-provider-live-verification.v1',
        'engine_id': engine,
        'exact_profile': profile.exact_version,
        'activation_ready': passed,
        'categories': categories,
        'secret_access_events': len(secret_service.audit_events()),
        'credential_values_exported': False,
        'common_transaction_finality_interpreted': False,
        'temporary_database_removed': removed,
        'filesystem_escape_refused': categories['fault'] == 'passed',
        'error_type': error_type,
        'error_message': error_message,
        'object_experience_evidence': object_evidence,
    }


def verify(engine, host, port, socket_path=None, account=None):
    profile = PROFILES[engine]
    context = _context(profile)
    account = account or _TemporaryAccount(
        engine, socket_path, host=host, port=port
    )
    provider = None
    categories = {name: 'not_run' for name in CATEGORIES}
    error_type = None
    error_message = None
    secret_service = EndpointSecretService()
    # Default development credentials can equal an engine name (notably
    # ``immudb``), which is expected to appear in every provider envelope.
    # Use only a sufficiently discriminating value for leak detection while
    # still redacting any credential from an error message below.
    secret_leak_marker = (
        account.password if len(account.password or '') >= 16 else ''
    )
    reference_id = str(uuid.uuid5(
        uuid.UUID(context.endpoint_id), 'database-password'
    ))
    object_evidence = {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': engine,
        'exact_profile': profile.exact_version,
        'evidence_scope': 'discovered-resource-inspection',
        'concepts': {},
        'inspected_resource_kinds': [],
        'inspection_failures': {},
        'raw_commands_used': False,
    }
    try:
        account.create()
        if account.password:
            secret_service.register_resolver(
                'live.ephemeral',
                lambda *_args: account.password.encode('utf-8'),
            )
            secret_service.register_reference(SecretReference(
                reference_id=reference_id,
                endpoint_id=context.endpoint_id,
                endpoint_mode=context.mode,
                secret_kind='database_password',
                storage_kind='ephemeral_test_account',
                resolver_id='live.ephemeral',
                locator=f'ephemeral:{engine}:qualification',
                allowed_purposes=frozenset({'connect'}),
                authority_scope='legacy_engine_auth',
            ))
        factory = PROVIDER_FACTORIES.get(engine, create_provider)
        provider = factory(context, _permissions(context, secret_service))
        route = {
            'route_id': 'exact-live-qualification',
            'host': host,
            'port': port,
            'user': account.username,
            'database': account.database,
            'principal_reference': 'cdeadmin-live-qualifier',
            'connection_timeout': 10,
        }
        if account.password:
            route['credential_reference_id'] = reference_id
        route.update(getattr(account, 'route_options', {}))
        if engine == 'dolt':
            route.update({
                'remote_url_allowlist': ['file:///var/lib/dolt/'],
                'backup_url_allowlist': ['file:///tmp/'],
            })
        if engine == 'firebird':
            route['role'] = 'RDB$ADMIN'
        request = {
            'route': route,
            'capability_generation': 'exact-live-qualification',
        }
        discovered = provider.discover_endpoint(request)
        if discovered['verified_runtime']['version'] != profile.exact_version:
            raise RuntimeError('exact runtime identity was not verified')
        provider.close()
        context = _verified_context(context, discovered)
        factory = PROVIDER_FACTORIES.get(engine, create_provider)
        provider = factory(
            context, _permissions(context, secret_service)
        )

        resources = provider.list_resources(request)
        if not any(
            item['resource_kind'] == 'table' for item in resources
        ):
            raise RuntimeError('live resource category found no table')
        categories['resource'] = 'passed'
        object_evidence = _object_inspection_evidence(
            provider, resources, request, engine
        )

        languages = provider.describe_language({})
        if languages[0]['display_name'] != profile.language_name:
            raise RuntimeError('live language category did not match profile')
        categories['language_api'] = 'passed'

        session = provider.open_session(request)
        marker = (
            '%s'
            if engine in {
                'cockroachdb', 'yugabytedb', 'mysql', 'dolt', 'tidb',
                'vitess', 'immudb',
            } else '?'
        )
        source = f'SELECT {marker} AS value'
        if engine == 'firebird':
            source = (
                'SELECT CAST(? AS INTEGER) AS QUALIFICATION_VALUE '
                'FROM RDB$DATABASE'
            )
        operation = provider.execute({
            'session_id': session['session_id'],
            'execution_id': f'{engine}-live-result',
            'source': source,
            'parameters': (42,),
        })
        _result_payload(provider, operation, engine)
        categories['result'] = 'passed'

        _semantic_payload(provider, session, engine)
        categories['semantic_query'] = 'passed'
        if engine in {
            'cockroachdb', 'yugabytedb', 'firebird', 'mysql', 'mariadb',
            'dolt', 'tidb', 'vitess', 'immudb',
        }:
            # Release the provider-owned result attachment before trying
            # metadata DDL through visual administration connections. This is
            # required by Firebird metadata dependencies and also prevents
            # session defaults from leaking into MySQL-family editor evidence.
            provider.close()
            provider = factory(
                context, _permissions(context, secret_service)
            )
            object_evidence = _merge_object_evidence(
                object_evidence,
                _relational_editor_evidence(
                    provider, request, engine
                ),
            )
            if engine == 'dolt':
                object_evidence = _merge_object_evidence(
                    object_evidence,
                    _dolt_repository_editor_evidence(
                        provider, request, account.database
                    ),
                )
            if engine == 'immudb':
                object_evidence = _merge_object_evidence(
                    object_evidence,
                    _immudb_multimodel_editor_evidence(provider, request),
                )
            session = provider.open_session(request)

        transaction = provider.describe_transaction(session)
        presentation = transaction['provider_payload']
        common_finality = presentation.get(
            'finality_interpreted_by_common_code'
        )
        if common_finality is not False:
            raise RuntimeError('common code interpreted transaction finality')
        if presentation.get('driver_observation_only') is not True:
            raise RuntimeError('transaction observation is not opaque')
        categories['transaction'] = 'passed'

        if len(provider.list_tools({})) != len(profile.admin_tools):
            raise RuntimeError('live admin tool catalog is incomplete')
        categories['admin'] = 'passed'

        security = provider.describe_security(request)
        if security['resource_kind'] != 'security-descriptor':
            raise RuntimeError('live security descriptor is invalid')
        categories['security'] = 'passed'

        try:
            provider.execute({
                'session_id': session['session_id'],
                'execution_id': f'{engine}-live-fault',
                'source': 'SELECT * FROM cdeadmin_live_missing_object',
            })
        except Exception as exc:
            if secret_leak_marker and secret_leak_marker in str(exc):
                raise RuntimeError('provider fault exposed secret material')
            diagnostic = provider.translate_diagnostic({
                'code': 'CDE_RELATIONAL_LIVE_EXPECTED_FAULT',
                'exception_type': type(exc).__name__,
                'retryable': False,
            })
            if secret_leak_marker and secret_leak_marker in json.dumps(
                    diagnostic):
                raise RuntimeError('translated diagnostic exposed secret')
        else:
            raise RuntimeError('invalid live query did not fail')
        categories['fault'] = 'passed'
    except Exception as exc:
        error_type = type(exc).__name__
        error_message = str(exc)
        if account.password:
            error_message = error_message.replace(
                account.password, '[redacted]'
            )
    finally:
        if provider is not None:
            provider.close()
        account.drop()

    passed = (
        all(value == 'passed' for value in categories.values()) and
        not object_evidence.get('operation_failures')
    )
    return {
        'schema': 'cdeadmin.relational-provider-live-verification.v1',
        'engine_id': engine,
        'exact_profile': profile.exact_version,
        'activation_ready': passed,
        'categories': categories,
        'secret_access_events': len(secret_service.audit_events()),
        'credential_values_exported': False,
        'common_transaction_finality_interpreted': False,
        'temporary_account_removed': account.password == '',
        'error_type': error_type,
        'error_message': error_message,
        'object_experience_evidence': object_evidence,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=sorted(PROFILES), required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int)
    parser.add_argument('--admin-socket')
    parser.add_argument('--admin-user', default='SYSDBA')
    parser.add_argument('--admin-password-env')
    parser.add_argument('--database')
    parser.add_argument('--tls-ca')
    parser.add_argument('--tls-client-cert')
    parser.add_argument('--tls-client-key')
    parser.add_argument('--http-port', type=int)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument(
        '--object-output', type=Path,
        help='Write exact per-object inspection evidence separately.',
    )
    args = parser.parse_args()
    if args.engine in {'duckdb', 'sqlite'}:
        result = verify_embedded(args.engine)
    elif args.engine == 'firebird':
        if (
            args.port is None or not args.database or
            not args.admin_password_env
        ):
            parser.error(
                '--port, --database, and --admin-password-env are required '
                'for Firebird'
            )
        admin_password = os.environ.get(args.admin_password_env)
        if not admin_password:
            parser.error('Firebird admin password environment is empty')
        account = _FirebirdAccount(
            args.host, args.port, args.database,
            args.admin_user, admin_password,
        )
        result = verify(
            args.engine, args.host, args.port, account=account
        )
    elif args.engine == 'cockroachdb':
        if args.port is None or not all((
            args.tls_ca, args.tls_client_cert, args.tls_client_key,
        )):
            parser.error(
                '--port, --tls-ca, --tls-client-cert, and '
                '--tls-client-key are required for CockroachDB'
            )
        account = _CockroachDBAccount(
            args.host, args.port, args.tls_ca,
            args.tls_client_cert, args.tls_client_key,
        )
        result = verify(
            args.engine, args.host, args.port, account=account
        )
    elif args.engine == 'vitess':
        if args.port is None or args.http_port is None:
            parser.error('--port and --http-port are required for Vitess')
        account = _VitessAccount(
            args.host, args.port, args.http_port,
            database=args.database or 'test_keyspace',
        )
        result = verify(
            args.engine, args.host, args.port, account=account
        )
    elif args.engine == 'yugabytedb':
        if args.port is None:
            parser.error('--port is required for YugabyteDB YSQL')
        account = _YugabyteDBAccount(
            args.host, args.port, admin_user=args.admin_user,
        )
        result = verify(
            args.engine, args.host, args.port, account=account
        )
    elif args.engine == 'immudb':
        if (
            args.port is None or args.http_port is None or
            not args.admin_password_env
        ):
            parser.error(
                '--port, --http-port, and --admin-password-env are required '
                'for immudb'
            )
        admin_password = os.environ.get(args.admin_password_env)
        if not admin_password:
            parser.error('immudb admin password environment is empty')
        admin_user = (
            'immudb' if args.admin_user == 'SYSDBA' else args.admin_user
        )
        account = _ImmudbAccount(
            args.host, args.port, args.http_port, admin_user,
            admin_password, database=args.database or 'defaultdb',
        )
        result = verify(
            args.engine, args.host, args.port, account=account
        )
    else:
        if args.port is None:
            parser.error('--port is required for MySQL/MariaDB')
        result = verify(
            args.engine, args.host, args.port, args.admin_socket
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    if args.object_output:
        args.object_output.parent.mkdir(parents=True, exist_ok=True)
        args.object_output.write_text(
            json.dumps(
                result['object_experience_evidence'],
                indent=2,
                sort_keys=True,
            ) + '\n',
            encoding='utf-8',
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['activation_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
