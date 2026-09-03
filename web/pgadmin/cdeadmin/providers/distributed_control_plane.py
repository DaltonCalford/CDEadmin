##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Reusable mechanics for provider-compiled distributed administration."""

from __future__ import annotations

import copy
from collections.abc import Mapping

from pgadmin.cdeadmin.sdk import RelationalClientError
from pgadmin.cdeadmin.visual_admin import ControlPlaneCatalog

from .relational_admin import RelationalAdministration


class DistributedSQLControlPlane(RelationalAdministration):
    """Add exact typed control operations to a relational provider.

    ``compiler`` is provider code. This class only verifies that the provider
    returned bounded structured statements and retains its declared impact
    model alongside the plan.
    """

    MAX_STATEMENTS = 32
    MAX_STATEMENT_BYTES = 1024 * 1024

    def __init__(
        self, dialect, operations, compiler, *, inspector=None,
        canceller=None, post_validator=None, action_executor=None,
    ):
        super().__init__(dialect)
        self.control_plane = ControlPlaneCatalog(
            dialect.engine_id, operations
        )
        if not callable(compiler):
            raise RelationalClientError(
                'distributed control-plane compiler is required'
            )
        self._control_compiler = compiler
        self._control_inspector = inspector
        self._control_canceller = canceller
        self._control_post_validator = post_validator
        self._control_action_executor = action_executor

    def supports(self, resource_kind, operation_id):
        return self.control_plane.supports(
            resource_kind, operation_id
        ) or super().supports(resource_kind, operation_id)

    def catalog(self, catalog):
        return self.control_plane.apply(super().catalog(catalog))

    def validate(self, request):
        if not self.control_plane.supports(
            request.get('resource_kind'), request.get('operation_id')
        ):
            return super().validate(request)
        return self.control_plane.validate(request)

    def plan(self, request):
        if not self.control_plane.supports(
            request.get('resource_kind'), request.get('operation_id')
        ):
            return super().plan(request)
        route = request.get('_provider_route')
        if not isinstance(route, Mapping) or not route:
            raise RelationalClientError(
                'distributed administration requires a trusted route'
            )
        checked = self.control_plane.validate(request)
        if checked['errors']:
            raise RelationalClientError(
                'distributed control-plane request is invalid'
            )
        operation = self.control_plane.operation(
            request['resource_kind'], request['operation_id']
        )
        compiled = self._control_compiler(copy.deepcopy(dict(request)))
        if not isinstance(compiled, Mapping):
            raise RelationalClientError(
                'provider control-plane compiler returned no plan'
            )
        compiled = copy.deepcopy(dict(compiled))
        preview = []
        statements = compiled.get('statements')
        action = compiled.get('provider_action')
        if statements is not None:
            if not isinstance(statements, list) or not statements:
                raise RelationalClientError(
                    'provider control-plane plan has no statements'
                )
            if len(statements) > self.MAX_STATEMENTS:
                raise RelationalClientError(
                    'provider control-plane plan exceeds statement limit'
                )
            for statement in statements:
                if not isinstance(statement, Mapping):
                    raise RelationalClientError(
                        'provider control-plane statement is invalid'
                    )
                source = statement.get('source')
                if not isinstance(source, str) or not source.strip():
                    raise RelationalClientError(
                        'provider control-plane statement is empty'
                    )
                if len(source.encode('utf-8')) > self.MAX_STATEMENT_BYTES:
                    raise RelationalClientError(
                        'provider control-plane statement exceeds size limit'
                    )
                parameters = statement.get('parameters', ())
                if not isinstance(parameters, (list, tuple)):
                    raise RelationalClientError(
                        'provider control-plane parameters are invalid'
                    )
                preview.append({
                    'source': statement.get('preview_source', source),
                    'parameter_count': len(parameters),
                    'parameters_redacted': bool(parameters),
                })
        elif isinstance(action, Mapping) and callable(
                self._control_action_executor):
            action_preview = compiled.get('action_preview')
            if not isinstance(action_preview, Mapping):
                raise RelationalClientError(
                    'provider control-plane action lacks a safe preview'
                )
            preview.append(copy.deepcopy(dict(action_preview)))
        else:
            raise RelationalClientError(
                'provider control-plane plan has no executable action'
            )
        impact = compiled.get('impact')
        if not isinstance(impact, Mapping):
            raise RelationalClientError(
                'provider control-plane plan lacks an impact assessment'
            )
        return {
            'command_preview': {
                'engine_id': self.dialect.engine_id,
                'operation': operation.operation_id,
                'statements': preview,
                'provider_constructed': True,
            },
            'provider_payload': {
                'route': copy.deepcopy(dict(route)),
                'compiled': compiled,
            },
            'warnings': copy.deepcopy(compiled.get('warnings', [])),
            'impact': copy.deepcopy(dict(impact)),
            'receipt': {
                'planner': 'cdeadmin.distributed-control-plane.v1',
                'provider_finality_authority': True,
                'automatic_mutation_retry': False,
            },
        }

    def apply(self, client, request):
        plan = request.get('plan')
        if isinstance(plan, Mapping) and self.control_plane.supports(
                plan.get('resource_kind'), plan.get('operation_id')):
            payload = request.get('provider_payload')
            compiled = payload.get('compiled') if isinstance(
                payload, Mapping) else None
            if isinstance(compiled, Mapping) and (
                    'provider_action' in compiled):
                if not callable(self._control_action_executor):
                    raise RelationalClientError(
                        'provider action executor is unavailable')
                value = self._control_action_executor(
                    client, copy.deepcopy(dict(request))
                )
                if not isinstance(value, Mapping):
                    raise RelationalClientError(
                        'provider action result is invalid')
                return copy.deepcopy(dict(value))
        return super().apply(client, request)

    def inspect_operation(self, client, request):
        if not callable(self._control_inspector):
            raise RelationalClientError(
                'provider control-plane observation is unavailable'
            )
        return self._control_inspector(client, copy.deepcopy(dict(request)))

    def cancel_operation(self, client, request):
        if not callable(self._control_canceller):
            raise RelationalClientError(
                'provider control-plane cancellation is unavailable'
            )
        return self._control_canceller(client, copy.deepcopy(dict(request)))

    def validate_operation_post_state(self, client, request):
        if not callable(self._control_post_validator):
            return {
                'confirmed': False,
                'reason': 'provider_post_state_validator_unavailable',
            }
        value = self._control_post_validator(
            client, copy.deepcopy(dict(request))
        )
        if not isinstance(value, Mapping) or not isinstance(
            value.get('confirmed'), bool
        ):
            raise RelationalClientError(
                'provider post-state validator returned an invalid result'
            )
        return copy.deepcopy(dict(value))
