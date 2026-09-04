##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Thin authenticated workspace orchestration over common CDEadmin services.

The facade does not interpret query text, provider operation state,
cancellation outcome, or transaction finality. Those values remain owned by
the endpoint provider and are returned as opaque contract presentations.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import threading

from pgadmin.cdeadmin.resources import ResourceRef
from pgadmin.cdeadmin.visual_admin.provider import (
    VisualAdminAccessError,
    VisualAdminExecutionError,
)
from pgadmin.cdeadmin.visual_admin.operational_workspace import (
    build_operational_workspace,
)


APP_EXTENSION_KEY = 'cdeadmin_provider_workspace'


class ProviderWorkspaceError(RuntimeError):
    """A provider endpoint workspace request cannot be admitted."""


class ProviderWorkspaceService:
    """Mount explorer, studio and typed results for one endpoint."""

    def __init__(
        self, endpoint_service, resource_service, studio_service,
        result_service, semantic_model_service=None, operation_bus=None,
        report_delivery_service=None,
    ):
        self.endpoint_service = endpoint_service
        self.resource_service = resource_service
        self.studio_service = studio_service
        self.result_service = result_service
        self.semantic_model_service = semantic_model_service
        self.operation_bus = operation_bus
        self.report_delivery_service = report_delivery_service
        self._semantic_executions = {}
        self._semantic_lock = threading.RLock()

    def bootstrap(self, server):
        context, endpoint, root = self.endpoint_service.workspace(server)
        binding = self.endpoint_service.provider_registry.resolve(context)
        describe_admin = getattr(
            binding.instance, 'visual_admin_descriptor', None
        )
        page = self.resource_service.list_page(
            context, root, page_size=500
        ).to_dict()
        visual_admin = describe_admin() if callable(describe_admin) else None
        return {
            'endpoint': {
                'endpoint_id': context.endpoint_id,
                'mode': context.mode,
                'experience_family': context.experience_family,
                'provider_id': context.provider_id,
                'provider_version': context.provider_version,
                'profile_id': context.profile_id,
                'profile_version': context.profile_version,
                'runtime_verification_state': (
                    context.runtime_verification_state
                ),
                'verified_runtime_family': (
                    context.verified_runtime_family
                ),
                'verified_runtime_version': (
                    context.verified_runtime_version
                ),
                'route_management_available': bool(
                    getattr(
                        getattr(server, 'endpoint_profile', None),
                        'provider_version', None,
                    )
                ),
            },
            'languages': list(self.studio_service.languages(context)),
            'resource_page': page,
            'visual_admin': visual_admin,
            'operational_workspace': (
                build_operational_workspace(
                    visual_admin, page.get('items', [])
                ) if visual_admin is not None else None
            ),
            'semantic_models': (
                {
                    'capabilities': self.semantic_model_service.capabilities(
                        binding.instance
                    ),
                    'items': self.semantic_model_service.list(
                        self._principal_id(server), context.endpoint_id
                    ),
                    'delivery': self.report_delivery_service.catalog()
                    if self.report_delivery_service is not None else {
                        'manual_delivery': False, 'profiles': [],
                        'automatic_scheduling': False,
                    },
                } if self.semantic_model_service is not None else {
                    'capabilities': {'designer': False}, 'items': [],
                }
            ),
        }

    def deliver_result(self, server, request):
        """Deliver a bounded export under the authenticated endpoint owner."""
        if self.report_delivery_service is None:
            raise ProviderWorkspaceError(
                'report delivery service is not initialized'
            )
        if not isinstance(request, dict) or set(request) != {
                'request_key', 'result_id', 'format', 'profile_id', 'target'}:
            raise ProviderWorkspaceError('result delivery request is invalid')
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        exported = self.result_service.export(
            request.get('result_id'), request.get('format'),
            endpoint_id=context.endpoint_id,
        )
        content = exported.pop('content')
        export_format = request.get('format')
        return self.report_delivery_service.deliver(
            self._principal_id(server), context.endpoint_id, {
                **request, 'content': content,
                'media_type': self._export_media_type(export_format),
                'filename': (
                    f"cdeadmin-result-{request.get('result_id')}."
                    f'{export_format}'
                ),
            }
        )

    def list_result_deliveries(self, server):
        """Return secret-free durable delivery occurrences for this owner."""
        if self.report_delivery_service is None:
            raise ProviderWorkspaceError(
                'report delivery service is not initialized'
            )
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        return {'items': self.report_delivery_service.list(
            self._principal_id(server), context.endpoint_id
        )}

    def resource_page(self, server, request):
        """Return the next generation-bound root navigator page."""
        if not isinstance(request, dict):
            raise ProviderWorkspaceError(
                'resource page request must be an object'
            )
        if set(request).difference({'continuation', 'generation'}):
            raise ProviderWorkspaceError(
                'resource page request contains unsupported fields'
            )
        continuation = request.get('continuation')
        generation = request.get('generation')
        if continuation is not None and not isinstance(continuation, str):
            raise ProviderWorkspaceError(
                'resource page continuation must be text'
            )
        if generation is not None and not isinstance(generation, str):
            raise ProviderWorkspaceError(
                'resource page generation must be text'
            )
        context, _endpoint, root = self.endpoint_service.workspace(server)
        return self.resource_service.list_page(
            context, root, page_size=500, cursor=continuation,
            expected_generation=generation,
        ).to_dict()

    def refresh_resources(self, server, request):
        """Refresh only this endpoint's resource generation."""
        if not isinstance(request, dict) or set(request).difference({
                'generation'}):
            raise ProviderWorkspaceError(
                'resource refresh request is invalid'
            )
        generation = request.get('generation')
        if not isinstance(generation, str) or not generation:
            raise ProviderWorkspaceError(
                'resource refresh generation is required'
            )
        context, _endpoint, root = self.endpoint_service.workspace(server)
        # Verify the caller's generation before invalidating. A stale browser
        # must reopen or reconcile instead of refreshing a newer tree.
        self.resource_service.list_page(
            context, root, page_size=1,
            expected_generation=generation,
        )
        self.resource_service.invalidate(context)
        return self.resource_service.list_page(
            context, root, page_size=500
        ).to_dict()

    def inspect_resource(self, server, request):
        """Inspect one cached resource without trusting browser identity."""
        if not isinstance(request, dict) or set(request).difference({
                'resource_id', 'generation'}):
            raise ProviderWorkspaceError(
                'resource inspect request is invalid'
            )
        resource_id = request.get('resource_id')
        generation = request.get('generation')
        if not isinstance(resource_id, str) or not resource_id:
            raise ProviderWorkspaceError('resource ID is required')
        if generation is not None and not isinstance(generation, str):
            raise ProviderWorkspaceError(
                'resource generation must be text'
            )
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        return self.resource_service.inspect(
            context, ResourceRef(context.endpoint_id, resource_id),
            expected_generation=generation,
        )

    def semantic_model_action(self, server, action, request):
        """Apply an endpoint-scoped semantic-model lifecycle action."""
        if self.semantic_model_service is None:
            raise ProviderWorkspaceError(
                'semantic model service is not initialized'
            )
        if not isinstance(request, dict):
            raise ProviderWorkspaceError(
                'semantic model request must be an object'
            )
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        user_id = self._principal_id(server)
        service = self.semantic_model_service
        if action == 'semantic_model_list':
            return {'items': service.list(user_id, context.endpoint_id)}
        if action == 'semantic_model_get':
            return service.get(user_id, context.endpoint_id,
                               request.get('model_id'))
        if action == 'semantic_model_create':
            return service.create(user_id, context.endpoint_id,
                                  self._semantic_definition(
                                      context, request.get('definition')
                                  ))
        if action == 'semantic_model_update':
            return service.update(
                user_id, context.endpoint_id, request.get('model_id'),
                request.get('expected_revision'), self._semantic_definition(
                    context, request.get('definition')
                )
            )
        if action == 'semantic_model_status':
            return service.set_status(
                user_id, context.endpoint_id, request.get('model_id'),
                request.get('expected_revision'), request.get('status')
            )
        if action == 'semantic_model_clone':
            return service.clone(
                user_id, context.endpoint_id, request.get('model_id'),
                request.get('name')
            )
        if action == 'semantic_model_delete':
            return service.delete(
                user_id, context.endpoint_id, request.get('model_id'),
                request.get('expected_revision')
            )
        if action == 'semantic_model_history':
            return {'items': service.history(
                user_id, context.endpoint_id, request.get('model_id')
            )}
        if action == 'semantic_model_compare':
            return service.compare(
                user_id, context.endpoint_id, request.get('model_id'),
                request.get('left_revision'), request.get('right_revision')
            )
        if action == 'semantic_model_validate':
            return service.validate(self._semantic_definition(
                context, request.get('definition')
            ))
        if action == 'semantic_model_lineage':
            return service.lineage(self._semantic_definition(
                context, request.get('definition')
            ))
        if action in {
            'semantic_query_compile', 'semantic_query_execute',
            'semantic_query_diagnostics',
        }:
            return self._semantic_query_action(
                server, context, action, request
            )
        if action == 'semantic_materialization_plan':
            return self._semantic_materialization_plan(
                server, context, request
            )
        raise ProviderWorkspaceError('semantic model action is unavailable')

    def _semantic_definition(self, context, value):
        if not isinstance(value, dict):
            raise ProviderWorkspaceError(
                'semantic model definition must be an object'
            )
        definition = copy.deepcopy(value)
        binding = self.endpoint_service.provider_registry.resolve(context)
        capabilities = self.semantic_model_service.capabilities(
            binding.instance
        )
        expected = capabilities['analytical_profile']['semantic_family']
        declared = definition.get('semantic_family')
        if declared is None:
            definition['semantic_family'] = expected
        elif declared != expected:
            raise ProviderWorkspaceError(
                'semantic model family does not match the endpoint provider'
            )
        return definition

    def _semantic_materialization_plan(self, server, context, request):
        user_id = self._principal_id(server)
        record = self.semantic_model_service.get(
            user_id, context.endpoint_id, request.get('model_id')
        )
        model = record['definition']
        materialization_id = request.get('materialization_id')
        materialization = next((item for item in model.get(
            'materializations', []
        ) if item.get('id') == materialization_id), None)
        if materialization is None:
            raise ProviderWorkspaceError('materialization is unavailable')
        materialization = copy.deepcopy(materialization)
        if not materialization.get('target') and model.get('sources'):
            relation = model['sources'][0].get('relation', [])
            materialization['target'] = relation[:-1]
        axes = [item['id'] for item in model.get('dimensions', [])]
        query = {
            'axes': {'rows': axes, 'columns': [], 'pages': []},
            'measures': [item['id'] for item in model.get('measures', [])],
            'filters': [], 'totals': False, 'limit': 10000,
        }
        binding = self.endpoint_service.provider_registry.resolve(context)
        compiled = self.semantic_model_service.compile(
            binding.instance, model, query
        )
        callback = getattr(
            binding.instance, 'plan_semantic_materialization', None
        )
        if not callable(callback):
            raise ProviderWorkspaceError(
                'provider has no semantic materialization planner'
            )
        endpoint = self.endpoint_service.workspace(server)[1]
        return callback({
            'materialization': materialization,
            'compiled': compiled,
            '_provider_route': copy.deepcopy(endpoint['route']),
        })

    def _semantic_query_action(self, server, context, action, request):
        service = self.semantic_model_service
        user_id = self._principal_id(server)
        record = None
        if request.get('model_id'):
            record = service.get(
                user_id, context.endpoint_id, request['model_id']
            )
            model = record['definition']
        else:
            model = request.get('definition')
        query = request.get('query')
        binding = self.endpoint_service.provider_registry.resolve(context)
        security_context = {
            'claims': {'user_id': self._principal_id(server)},
        }
        if action == 'semantic_query_diagnostics':
            diagnostics = service.diagnostics(
                binding.instance, model, query,
                security_context=security_context,
            )
            diagnostics['reproducibility']['model_id'] = (
                record['model_id'] if record else None
            )
            diagnostics['reproducibility']['model_revision'] = (
                record['revision'] if record else None
            )
            return diagnostics
        compiled = service.compile(
            binding.instance, model, query,
            security_context=security_context,
        )
        compiled['reproducibility']['model_id'] = (
            record['model_id'] if record else None
        )
        compiled['reproducibility']['model_revision'] = (
            record['revision'] if record else None
        )
        response = {'compiled': compiled}
        if action == 'semantic_query_compile':
            return response
        session_id = request.get('session_id')
        if not isinstance(session_id, str) or not session_id:
            opened = self.studio_service.open_session(
                context, self.endpoint_service.workspace(server)[1],
                compiled['language_profile'],
            )
            session_id = opened['provider_session']['session_id']
            response['session_id'] = session_id
        occurrence = self.studio_service.execute(
            context, session_id, compiled['source'],
            parameters=compiled.get('parameters', {}),
            output_policy={'redact_keys': []},
        )
        response['occurrence'] = occurrence
        with self._semantic_lock:
            if len(self._semantic_executions) >= 1024:
                self._semantic_executions.pop(next(iter(
                    self._semantic_executions
                )))
            self._semantic_executions[occurrence['occurrence_id']] = {
                'endpoint_id': context.endpoint_id,
                'model': copy.deepcopy(model),
                'query': copy.deepcopy(query),
                'reproducibility': copy.deepcopy(
                    compiled['reproducibility']
                ),
            }
        return response

    def validate_visual_admin(self, server, request):
        return self._visual_admin_call(
            server, 'validate_visual_admin', request
        )

    def plan_visual_admin(self, server, request):
        return self._visual_admin_call(server, 'plan_visual_admin', request)

    def apply_visual_admin(self, server, request):
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        try:
            result = self._visual_admin_call(
                server, 'apply_visual_admin', request
            )
        except VisualAdminExecutionError as exc:
            self._record_visual_admin_audit(
                server, context, exc.operation
            )
            # The provider may have applied the mutation before its response
            # was lost.  Force subsequent discovery to observe provider state
            # instead of serving a potentially stale cached resource tree.
            self.resource_service.invalidate(context)
            raise
        operation = result.get('control_operation')
        if isinstance(operation, dict):
            self._record_visual_admin_audit(server, context, operation)
        self.resource_service.invalidate(context)
        return result

    def plan_visual_admin_bulk(self, server, request):
        """Create bounded, provider-native previews for an ordered batch."""
        if not isinstance(request, dict) or set(request) != {'items'}:
            raise ProviderWorkspaceError('bulk plan request is invalid')
        items = request.get('items')
        if not isinstance(items, list) or not 1 <= len(items) <= 500:
            raise ProviderWorkspaceError(
                'bulk plan requires between 1 and 500 items'
            )
        validations = []
        for item in items:
            if not isinstance(item, dict):
                raise ProviderWorkspaceError('bulk plan item is invalid')
            validations.append(self.validate_visual_admin(server, item))
        if not all(item.get('valid') is True for item in validations):
            return {
                'schema': 'cdeadmin.visual-admin.bulk-plan.v1',
                'item_count': len(items),
                'ready': False,
                'atomicity': 'not-claimed',
                'automatic_retry': False,
                'validations': validations,
                'plans': [],
            }
        plans = []
        for index, item in enumerate(items):
            plan = self.plan_visual_admin(server, item)
            plans.append({'index': index, 'plan': plan})
        ready = all(
            item['plan'].get('state') == 'ready' and
            item['plan'].get('execution_available') is True
            for item in plans
        )
        return {
            'schema': 'cdeadmin.visual-admin.bulk-plan.v1',
            'item_count': len(plans),
            'ready': ready,
            'atomicity': 'not-claimed',
            'automatic_retry': False,
            'plans': plans,
        }

    def apply_visual_admin_bulk(self, server, request):
        """Apply an explicitly confirmed ordered batch without retry."""
        if not isinstance(request, dict) or set(request) != {
                'plans', 'confirmed'}:
            raise ProviderWorkspaceError('bulk apply request is invalid')
        plans = request.get('plans')
        if request.get('confirmed') is not True:
            raise ProviderWorkspaceError(
                'bulk apply requires explicit confirmation'
            )
        if not isinstance(plans, list) or not 1 <= len(plans) <= 500:
            raise ProviderWorkspaceError(
                'bulk apply requires between 1 and 500 plans'
            )
        results = []
        failed_index = None
        for index, item in enumerate(plans):
            if not isinstance(item, dict) or set(item) != {
                    'plan_id', 'plan_digest'}:
                raise ProviderWorkspaceError('bulk apply plan is invalid')
            try:
                result = self.apply_visual_admin(server, {
                    **item, 'confirmed': True,
                })
            except (
                ProviderWorkspaceError,
                VisualAdminAccessError,
                VisualAdminExecutionError,
            ) as exc:
                failed_index = index
                results.append({
                    'index': index,
                    'applied': False,
                    'error_type': type(exc).__name__,
                    'outcome': 'unknown-or-failed-provider-owned',
                })
                break
            results.append({
                'index': index, 'applied': True, 'result': result,
            })
        return {
            'schema': 'cdeadmin.visual-admin.bulk-result.v1',
            'requested_count': len(plans),
            'attempted_count': len(results),
            'applied_count': sum(
                item['applied'] is True for item in results
            ),
            'complete': failed_index is None,
            'failed_index': failed_index,
            'atomicity': 'not-claimed',
            'automatic_retry': False,
            'results': results,
        }

    def read_visual_admin_rows(self, server, request):
        return self._visual_admin_call(
            server, 'read_visual_admin_rows', request
        )

    def cancel_visual_admin_rows(self, server, request):
        return self._visual_admin_call(
            server, 'cancel_visual_admin_rows', request
        )

    def visual_admin_operation_action(self, server, action, request):
        methods = {
            'visual_admin_operation_list': 'list_visual_admin_operations',
            'visual_admin_operation_get': 'get_visual_admin_operation',
            'visual_admin_operation_refresh': (
                'refresh_visual_admin_operation'
            ),
            'visual_admin_operation_cancel': 'cancel_visual_admin_operation',
            'visual_admin_operation_post_state': (
                'validate_visual_admin_post_state'
            ),
        }
        try:
            method = methods[action]
        except KeyError as exc:
            raise ProviderWorkspaceError(
                'visual administration operation action is unavailable'
            ) from exc
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        principal = self._operation_principal(server)
        if action == 'visual_admin_operation_list':
            live = self._visual_admin_call(server, method, request or {})
            live_items = live.get('items', [])
            for item in live_items:
                self._record_visual_admin_audit(server, context, item)
            durable = (
                self.operation_bus.list_provider_audit(
                    context.endpoint_id, principal
                ) if self.operation_bus is not None else []
            )
            merged = {
                item['operation_id']: {
                    **item, 'durable_audit': True,
                    'live_provider_handle_available': False,
                }
                for item in durable
            }
            merged.update({
                item['operation_id']: {
                    **item, 'durable_audit': self.operation_bus is not None,
                    'live_provider_handle_available': True,
                }
                for item in live_items
            })
            return {
                'schema': 'cdeadmin.visual-admin.operation-list.v1',
                'items': list(merged.values()),
                'restart_safe_audit': self.operation_bus is not None,
            }
        try:
            result = self._visual_admin_call(server, method, request or {})
        except VisualAdminExecutionError as exc:
            self._record_visual_admin_audit(server, context, exc.operation)
            if action in {
                    'visual_admin_operation_cancel',
                    'visual_admin_operation_post_state'}:
                self.resource_service.invalidate(context)
            raise
        except VisualAdminAccessError:
            if action != 'visual_admin_operation_get' or \
                    self.operation_bus is None:
                raise
            operation_id = (request or {}).get('operation_id')
            result = self.operation_bus.get_provider_audit(
                context.endpoint_id, principal, operation_id
            )
            return {
                **result,
                'durable_audit': True,
                'live_provider_handle_available': False,
                'restart_safe_audit': True,
            }
        if isinstance(result, dict) and result.get('operation_id'):
            self._record_visual_admin_audit(server, context, result)
            return {
                **result,
                'durable_audit': self.operation_bus is not None,
                'live_provider_handle_available': True,
                'restart_safe_audit': self.operation_bus is not None,
            }
        return result

    def _record_visual_admin_audit(self, server, context, operation):
        if self.operation_bus is None:
            return
        self.operation_bus.record_provider_audit(
            context.endpoint_id, self._operation_principal(server), operation
        )

    def _operation_principal(self, server):
        return f'user:{self._principal_id(server)}'

    def open_session(self, server, language_profile):
        context, endpoint, _root = self.endpoint_service.workspace(server)
        opened = self.studio_service.open_session(
            context, endpoint, language_profile
        )
        return {
            **opened,
            'session_id': opened['provider_session']['session_id'],
        }

    def execute(self, server, session_id, source, parameters=None):
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        if not isinstance(source, str) or not source.strip():
            raise ProviderWorkspaceError('query source must not be empty')
        if parameters is not None and not isinstance(parameters, dict):
            raise ProviderWorkspaceError('query parameters must be an object')
        return self.studio_service.execute(
            context,
            session_id,
            source,
            parameters=copy.deepcopy(parameters or {}),
            output_policy={'redact_keys': []},
        )

    def poll(self, server, occurrence_id):
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        occurrence = self.studio_service.poll(context, occurrence_id)
        response = {'occurrence': occurrence, 'rendered_result': None}
        result = occurrence.get('result')
        if result is not None:
            binding = self.endpoint_service.provider_registry.resolve(context)
            renderer = binding.instance.select_renderer(
                copy.deepcopy(result)
            )
            capabilities = renderer.get('capability_ids', ())
            descriptor = self.studio_service.admit_result(
                context, occurrence_id, capabilities
            )
            response['rendered_result'] = self.result_service.render(
                descriptor['result_id'], page_size=500,
                endpoint_id=context.endpoint_id,
            )
            with self._semantic_lock:
                semantic = self._semantic_executions.get(occurrence_id)
            if semantic is not None:
                if semantic['endpoint_id'] != context.endpoint_id:
                    raise ProviderWorkspaceError(
                        'semantic execution belongs to another endpoint'
                    )
                rendered = response['rendered_result']
                view = rendered.get('view_model') or {}
                records = view.get('rows', view.get('records', []))
                rendered['renderer_id'] = 'cdeadmin.result.cellset.pivot'
                rendered['component_reference'] = (
                    'cdeadmin/results/CubePivotView'
                )
                rendered['view_model'] = self.semantic_model_service.cellset(
                    semantic['model'], semantic['query'], records
                )
                rendered['reproducibility'] = {
                    **copy.deepcopy(semantic['reproducibility']),
                    'rendered_page_digest': hashlib.sha256(json.dumps(
                        rendered['view_model'], sort_keys=True,
                        separators=(',', ':'), ensure_ascii=False,
                        default=repr,
                    ).encode('utf-8')).hexdigest(),
                    'provider_snapshot': None,
                    'exact_data_replay_requires_provider_snapshot': True,
                    'common_layer_infers_snapshot_or_finality': False,
                }
        return response

    def result_page(self, server, request):
        """Render one endpoint-bound local result page."""
        if not isinstance(request, dict) or set(request).difference({
                'result_id', 'cursor', 'page_size'}):
            raise ProviderWorkspaceError('result page request is invalid')
        result_id = request.get('result_id')
        cursor = request.get('cursor')
        page_size = request.get('page_size', 500)
        if not isinstance(result_id, str) or not result_id:
            raise ProviderWorkspaceError('result ID is required')
        if cursor is not None and not isinstance(cursor, str):
            raise ProviderWorkspaceError('result cursor must be text')
        if isinstance(page_size, bool) or not isinstance(page_size, int):
            raise ProviderWorkspaceError('result page size must be an integer')
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        return self.result_service.render(
            result_id, page_size=page_size, cursor=cursor,
            endpoint_id=context.endpoint_id,
        )

    def export_result(self, server, request):
        """Return one bounded, redacted result export for browser download."""
        if not isinstance(request, dict) or set(request) != {
                'result_id', 'format'}:
            raise ProviderWorkspaceError('result export request is invalid')
        result_id = request.get('result_id')
        export_format = request.get('format')
        if not isinstance(result_id, str) or not result_id:
            raise ProviderWorkspaceError('result ID is required')
        if not isinstance(export_format, str) or not export_format:
            raise ProviderWorkspaceError('result export format is required')
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        exported = self.result_service.export(
            result_id, export_format, endpoint_id=context.endpoint_id
        )
        content = exported.pop('content')
        return {
            **exported,
            'content_base64': base64.b64encode(content).decode('ascii'),
            'media_type': self._export_media_type(export_format),
            'filename': f'cdeadmin-result-{result_id}.{export_format}',
        }

    @staticmethod
    def _export_media_type(export_format):
        media_types = {
            'csv': 'text/csv', 'json': 'application/json',
            'jsonl': 'application/x-ndjson',
            'xlsx': ('application/vnd.openxmlformats-officedocument.'
                     'spreadsheetml.sheet'),
            'svg': 'image/svg+xml', 'pdf': 'application/pdf',
        }
        return media_types.get(export_format, 'application/octet-stream')

    def compare_results(self, server, request):
        """Compare two retained redacted presentations from one endpoint."""
        if not isinstance(request, dict) or set(request) != {
                'left_result_id', 'right_result_id'}:
            raise ProviderWorkspaceError(
                'result comparison request is invalid'
            )
        values = (
            request.get('left_result_id'), request.get('right_result_id')
        )
        if any(not isinstance(item, str) or not item for item in values):
            raise ProviderWorkspaceError(
                'result comparison IDs are required'
            )
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        return self.result_service.compare(
            values[0], values[1], endpoint_id=context.endpoint_id
        )

    def cancel(self, server, occurrence_id):
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        return self.studio_service.request_cancel(context, occurrence_id)

    def transaction(self, server, session_id):
        """Return the provider presentation without interpreting its state."""
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        return self.studio_service.refresh_transaction(context, session_id)

    def transaction_action(self, server, session_id, action):
        """Dispatch a provider-owned transaction action without inference."""
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        return self.studio_service.control_transaction(
            context, session_id, action
        )

    def _visual_admin_call(self, server, method_name, request):
        context, endpoint, _root = self.endpoint_service.workspace(server)
        binding = self.endpoint_service.provider_registry.resolve(context)
        callback = getattr(binding.instance, method_name, None)
        if not callable(callback):
            raise ProviderWorkspaceError(
                'endpoint provider has no visual administration contract'
            )
        if not isinstance(request, dict):
            raise ProviderWorkspaceError(
                'visual administration request must be an object'
            )
        payload = copy.deepcopy(request)
        # Route authority is server-side. Never accept a browser-supplied
        # route, even under the reserved internal key.
        payload.pop('_provider_route', None)
        payload['_provider_route'] = copy.deepcopy(endpoint['route'])
        return callback(payload)

    @staticmethod
    def _principal_id(server):
        value = getattr(server, 'user_id', None)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProviderWorkspaceError(
                'workspace owner identity is unavailable'
            )
        return value


def init_app(
    app, endpoint_service, resource_service, studio_service, result_service,
    semantic_model_service=None, operation_bus=None,
    report_delivery_service=None,
):
    existing = app.extensions.get(APP_EXTENSION_KEY)
    if existing is not None:
        return existing
    service = ProviderWorkspaceService(
        endpoint_service, resource_service, studio_service, result_service,
        semantic_model_service, operation_bus, report_delivery_service,
    )
    app.extensions[APP_EXTENSION_KEY] = service
    return service


def service_for_app(app):
    try:
        return app.extensions[APP_EXTENSION_KEY]
    except (AttributeError, KeyError) as exc:
        raise ProviderWorkspaceError(
            'CDEadmin provider workspace is not initialized'
        ) from exc
