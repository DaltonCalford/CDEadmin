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

import copy
import threading

from pgadmin.cdeadmin.visual_admin.provider import (
    VisualAdminAccessError,
    VisualAdminExecutionError,
)


APP_EXTENSION_KEY = 'cdeadmin_provider_workspace'


class ProviderWorkspaceError(RuntimeError):
    """A provider endpoint workspace request cannot be admitted."""


class ProviderWorkspaceService:
    """Mount explorer, studio and typed results for one endpoint."""

    def __init__(
        self, endpoint_service, resource_service, studio_service,
        result_service, semantic_model_service=None, operation_bus=None,
    ):
        self.endpoint_service = endpoint_service
        self.resource_service = resource_service
        self.studio_service = studio_service
        self.result_service = result_service
        self.semantic_model_service = semantic_model_service
        self.operation_bus = operation_bus
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
            },
            'languages': list(self.studio_service.languages(context)),
            'resource_page': page,
            'visual_admin': (
                describe_admin() if callable(describe_admin) else None
            ),
            'semantic_models': (
                {
                    'capabilities': self.semantic_model_service.capabilities(
                        binding.instance
                    ),
                    'items': self.semantic_model_service.list(
                        self._principal_id(server), context.endpoint_id
                    ),
                } if self.semantic_model_service is not None else {
                    'capabilities': {'designer': False}, 'items': [],
                }
            ),
        }

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
                                  request.get('definition'))
        if action == 'semantic_model_update':
            return service.update(
                user_id, context.endpoint_id, request.get('model_id'),
                request.get('expected_revision'), request.get('definition')
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
            return service.validate(request.get('definition'))
        if action == 'semantic_model_lineage':
            return service.lineage(request.get('definition'))
        if action in {'semantic_query_compile', 'semantic_query_execute'}:
            return self._semantic_query_action(
                server, context, action, request
            )
        if action == 'semantic_materialization_plan':
            return self._semantic_materialization_plan(
                server, context, request
            )
        raise ProviderWorkspaceError('semantic model action is unavailable')

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
        if request.get('model_id'):
            record = service.get(
                user_id, context.endpoint_id, request['model_id']
            )
            model = record['definition']
        else:
            model = request.get('definition')
        query = request.get('query')
        binding = self.endpoint_service.provider_registry.resolve(context)
        compiled = service.compile(binding.instance, model, query)
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
                descriptor['result_id'], page_size=500
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
        return response

    def cancel(self, server, occurrence_id):
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        return self.studio_service.request_cancel(context, occurrence_id)

    def transaction(self, server, session_id):
        """Return the provider presentation without interpreting its state."""
        context, _endpoint, _root = self.endpoint_service.workspace(server)
        return self.studio_service.refresh_transaction(context, session_id)

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
):
    existing = app.extensions.get(APP_EXTENSION_KEY)
    if existing is not None:
        return existing
    service = ProviderWorkspaceService(
        endpoint_service, resource_service, studio_service, result_service,
        semantic_model_service, operation_bus,
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
