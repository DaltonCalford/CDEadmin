##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Typed provider-owned distributed control-plane catalog helpers.

The common layer describes forms, impact and authorization boundaries. It
does not generate native commands, infer remote finality or retry mutations.
Each provider must explicitly compile every operation it advertises.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .requirements import EXPERIENCE_REQUIREMENTS


CONTROL_PLANE_PERMISSIONS = frozenset({
    'topology_admin', 'security_admin', 'backup_admin', 'restore_admin',
    'replication_admin', 'maintenance_admin', 'upgrade_admin',
})
IMPACT_SCOPES = frozenset({
    'resource', 'node', 'shard', 'region', 'cluster', 'deployment',
})
CONTROL_PLANE_CONTROLS = frozenset({
    'text', 'multiline', 'password', 'number', 'boolean', 'select',
    'multiselect', 'json', 'secret-reference',
})


class ControlPlaneCatalogError(RuntimeError):
    """A provider control-plane declaration is incomplete or ambiguous."""


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ControlPlaneCatalogError(f'{label} must be a non-empty string')
    return value.strip()


@dataclass(frozen=True)
class ControlPlaneOperation:
    """One exact visual operation compiled by an engine provider."""

    resource_kind: str
    operation_id: str
    title: str
    mutation_class: str
    permission: str
    fields: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    target_required: bool = True
    confirmation_required: bool = True
    impact_scope: str = 'resource'
    long_running: bool = False
    cancellable: bool = False
    post_state_required: bool = True

    def __post_init__(self):
        for name in ('resource_kind', 'operation_id', 'title'):
            object.__setattr__(
                self, name, _required_text(getattr(self, name), name)
            )
        if self.mutation_class not in {'read', 'admin', 'destructive'}:
            raise ControlPlaneCatalogError('invalid control-plane mutation')
        if self.permission not in CONTROL_PLANE_PERMISSIONS:
            raise ControlPlaneCatalogError('invalid control-plane permission')
        if self.impact_scope not in IMPACT_SCOPES:
            raise ControlPlaneCatalogError('invalid impact scope')
        admitted = []
        identifiers = set()
        for raw in self.fields:
            if not isinstance(raw, Mapping):
                raise ControlPlaneCatalogError('operation field is invalid')
            item = copy.deepcopy(dict(raw))
            field_id = _required_text(item.get('field_id'), 'field_id')
            if field_id in identifiers:
                raise ControlPlaneCatalogError(
                    f'duplicate field {field_id!r}'
                )
            identifiers.add(field_id)
            _required_text(item.get('label'), f'{field_id}.label')
            control = _required_text(
                item.get('control'), f'{field_id}.control'
            )
            if control not in CONTROL_PLANE_CONTROLS:
                raise ControlPlaneCatalogError(
                    f'unsupported control-plane control {control!r}'
                )
            if control in {'select', 'multiselect'}:
                choices = item.get('options')
                if not isinstance(choices, list) or not choices:
                    raise ControlPlaneCatalogError(
                        f'{field_id}.options must not be empty'
                    )
                values = []
                for choice in choices:
                    if not isinstance(choice, Mapping):
                        raise ControlPlaneCatalogError(
                            f'{field_id}.options is invalid'
                        )
                    values.append(_required_text(
                        choice.get('value'), f'{field_id}.option.value'
                    ))
                    _required_text(
                        choice.get('label'), f'{field_id}.option.label'
                    )
                if len(values) != len(set(values)):
                    raise ControlPlaneCatalogError(
                        f'{field_id}.options must contain unique values'
                    )
            item['required'] = bool(item.get('required', False))
            admitted.append(item)
        object.__setattr__(self, 'fields', tuple(admitted))

    @property
    def key(self):
        return self.resource_kind, self.operation_id

    def descriptor(self):
        form_id = f'control-plane.{self.resource_kind}.{self.operation_id}'
        return {
            'operation_id': self.operation_id,
            'title': self.title,
            'mutation_class': self.mutation_class,
            'form_id': form_id,
            'form': {
                'form_id': form_id,
                'title': self.title,
                'fields': copy.deepcopy(list(self.fields)),
            },
            'target_required': self.target_required,
            'confirmation_required': self.confirmation_required,
            'required_permissions': [self.permission],
            'control_plane': True,
            'impact_scope': self.impact_scope,
            'long_running': self.long_running,
            'cancellable': self.cancellable,
            'post_state_required': self.post_state_required,
            'provider_finality_authority': True,
            'automatic_mutation_retry': False,
        }


class ControlPlaneCatalog:
    """Validated index of provider-specific control-plane operations."""

    def __init__(self, engine_id, operations):
        self.engine_id = _required_text(engine_id, 'engine_id')
        self._operations = {}
        for operation in operations:
            if not isinstance(operation, ControlPlaneOperation):
                raise ControlPlaneCatalogError(
                    'control-plane operation declaration is invalid'
                )
            if operation.key in self._operations:
                raise ControlPlaneCatalogError(
                    f'duplicate operation {operation.key!r}'
                )
            self._operations[operation.key] = operation

    def supports(self, resource_kind, operation_id):
        return (resource_kind, operation_id) in self._operations

    def operation(self, resource_kind, operation_id):
        try:
            return self._operations[(resource_kind, operation_id)]
        except KeyError as exc:
            raise ControlPlaneCatalogError(
                'control-plane operation is not declared by this provider'
            ) from exc

    def apply(self, catalog):
        value = copy.deepcopy(dict(catalog))
        resources = {
            item['resource_kind']: item for item in value.get('objects', [])
        }
        for operation in self._operations.values():
            resource = resources.get(operation.resource_kind)
            if resource is None:
                raise ControlPlaneCatalogError(
                    f'{self.engine_id} does not declare resource kind '
                    f'{operation.resource_kind!r}'
                )
            existing = {
                item['operation_id']: index
                for index, item in enumerate(resource.get('operations', []))
            }
            descriptor = operation.descriptor()
            if operation.operation_id in existing:
                resource['operations'][existing[operation.operation_id]] = (
                    descriptor
                )
            else:
                resource.setdefault('operations', []).append(descriptor)
        declarations = value.get('concept_declarations', {})
        for family_id, concepts in declarations.items():
            requirements = EXPERIENCE_REQUIREMENTS.get(family_id, {})
            if not isinstance(concepts, dict):
                continue
            for concept_id, declaration in concepts.items():
                if not isinstance(declaration, dict):
                    continue
                requirement = requirements.get(concept_id, {})
                candidates = set(requirement.get('resource_kinds', ()))
                candidates.update(declaration.get('resource_kinds', ()))
                obligations = declaration.setdefault(
                    'operation_obligations', {}
                )
                mutable = False
                for operation in self._operations.values():
                    if operation.resource_kind not in candidates:
                        continue
                    current = obligations.setdefault(
                        operation.resource_kind, []
                    )
                    if operation.operation_id not in current:
                        current.append(operation.operation_id)
                        current.sort()
                    mutable = mutable or operation.mutation_class != 'read'
                if mutable and declaration.get('status') == 'read_only':
                    declaration['status'] = 'supported'
        value['distributed_control_plane_contract'] = (
            'cdeadmin.distributed-control-plane.v1'
        )
        value['control_plane_provider_compilation_required'] = True
        value['control_plane_automatic_mutation_retry'] = False
        value['control_plane_operations'] = len(self._operations)
        return value

    def validate(self, request):
        operation = self.operation(
            request.get('resource_kind'), request.get('operation_id')
        )
        draft = request.get('draft')
        errors = []
        if not isinstance(draft, Mapping):
            return {'errors': [{
                'field_id': None,
                'code': 'invalid_draft',
                'message': 'The control-plane draft must be an object.',
            }]}
        if operation.target_required and not isinstance(
            request.get('target_resource'), Mapping
        ):
            errors.append({
                'field_id': None,
                'code': 'target_required',
                'message': 'The control-plane target is required.',
            })
        if isinstance(draft, Mapping):
            for declaration in operation.fields:
                visible = declaration.get('visible_when')
                if isinstance(visible, Mapping) and draft.get(
                        visible.get('field_id')) != visible.get('equals'):
                    continue
                field_id = declaration['field_id']
                present = field_id in draft and draft[field_id] is not None
                value = draft.get(field_id)
                if declaration.get('required') and (
                        not present or value == '' or value == []):
                    errors.append({
                        'field_id': field_id,
                        'code': 'required',
                        'message': f'{declaration["label"]} is required.',
                    })
                    continue
                if not present or value == '':
                    continue
                error = self._field_error(declaration, value)
                if error is not None:
                    errors.append({
                        'field_id': field_id,
                        'code': error[0],
                        'message': error[1],
                    })
        return {'errors': errors}

    @staticmethod
    def _field_error(declaration, value):
        control = declaration['control']
        label = declaration['label']
        if control in {'text', 'multiline', 'password'}:
            if not isinstance(value, str):
                return 'invalid_type', f'{label} must be text.'
            maximum = declaration.get('max_length')
            if isinstance(maximum, int) and len(value) > maximum:
                return 'too_long', f'{label} exceeds its maximum length.'
            pattern = declaration.get('pattern')
            if pattern is not None and (
                    not isinstance(pattern, str) or re.fullmatch(
                        pattern, value) is None):
                return 'invalid_format', f'{label} has an invalid format.'
        elif control == 'select':
            admitted = {
                item.get('value') for item in declaration.get('options', [])
                if isinstance(item, Mapping)
            }
            if value not in admitted:
                return 'invalid_choice', f'{label} is not an admitted choice.'
        elif control == 'multiselect':
            if not isinstance(value, list):
                return 'invalid_type', f'{label} must be a list.'
            admitted = {
                item.get('value') for item in declaration.get('options', [])
                if isinstance(item, Mapping)
            }
            if len(value) != len(set(value)) or any(
                    item not in admitted for item in value):
                return (
                    'invalid_choice',
                    f'{label} contains an unadmitted or duplicate choice.',
                )
        elif control == 'number':
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return 'invalid_type', f'{label} must be a number.'
            minimum = declaration.get('minimum')
            maximum = declaration.get('maximum')
            if minimum is not None and value < minimum:
                return 'below_minimum', f'{label} is below its minimum.'
            if maximum is not None and value > maximum:
                return 'above_maximum', f'{label} exceeds its maximum.'
        elif control == 'boolean':
            if not isinstance(value, bool):
                return 'invalid_type', f'{label} must be true or false.'
        elif control == 'json':
            json_type = declaration.get('json_type')
            if json_type == 'array' and not isinstance(value, list):
                return 'invalid_type', f'{label} must be an array.'
            if json_type == 'object' and not isinstance(value, Mapping):
                return 'invalid_type', f'{label} must be an object.'
            if json_type not in {'array', 'object'} and not isinstance(
                    value, (Mapping, list)):
                return 'invalid_type', f'{label} must be structured JSON.'
        else:
            return 'invalid_control', f'{label} uses an unsupported control.'
        return None

    @property
    def keys(self):
        return frozenset(self._operations)


def field(field_id, label, control='text', required=False, **options):
    """Build one declarative field without accepting executable source."""
    if control not in CONTROL_PLANE_CONTROLS:
        raise ControlPlaneCatalogError(
            'control-plane fields cannot accept this control type'
        )
    return {
        'field_id': field_id,
        'label': label,
        'control': control,
        'required': required,
        **options,
    }
