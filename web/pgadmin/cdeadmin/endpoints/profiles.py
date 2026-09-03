##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Profile-driven endpoint registration catalog for active providers."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from pgadmin.cdeadmin.providers import BUILTIN_PACKAGES

from .connection_capabilities import (
    ConnectionCapabilityError,
    normalize_connection_capabilities,
)
from .capability_sets import expand_capability_set
from .field_sets import expand_field_sets


class EndpointRegistrationError(ValueError):
    """A requested endpoint profile is unavailable or malformed."""


CONNECTION_CONTROLS = frozenset({
    'text', 'multiline', 'json', 'number', 'boolean', 'file', 'select',
})


def _connection_fields(registration):
    fields = registration.get('connection_fields', [])
    if not isinstance(fields, list):
        raise EndpointRegistrationError(
            'endpoint connection_fields must be an array'
        )
    normalized = []
    seen = set()
    for field in fields:
        if not isinstance(field, dict):
            raise EndpointRegistrationError(
                'endpoint connection field must be an object'
            )
        field_id = _required(field.get('field_id'), 'connection field_id')
        route_key = _required(
            field.get('route_key', field_id), 'connection route_key'
        )
        control = _required(field.get('control'), 'connection control')
        if control not in CONNECTION_CONTROLS:
            raise EndpointRegistrationError(
                'endpoint connection field control is invalid'
            )
        if field_id in seen or route_key in {
            item['route_key'] for item in normalized
        }:
            raise EndpointRegistrationError(
                'endpoint connection fields must be unique'
            )
        seen.add(field_id)
        value = {
            'field_id': field_id,
            'route_key': route_key,
            'label': _required(field.get('label'), 'connection label'),
            'control': control,
            'group': str(field.get('group') or 'Advanced connection'),
            'required': field.get('required') is True,
            'sensitive': field.get('sensitive') is True,
            'help': str(field.get('help') or ''),
        }
        value['integer'] = field.get('integer', True) is not False
        if 'default' in field:
            value['default'] = copy.deepcopy(field['default'])
        for name in ('minimum', 'maximum'):
            if name in field:
                limit = field[name]
                if isinstance(limit, bool) or not isinstance(limit, int):
                    raise EndpointRegistrationError(
                        f'connection field {name} must be an integer'
                    )
                value[name] = limit
        if control == 'select':
            options = field.get('options')
            if not isinstance(options, list) or not options:
                raise EndpointRegistrationError(
                    'select connection field options must not be empty'
                )
            normalized_options = []
            option_values = set()
            for option in options:
                if not isinstance(option, dict):
                    raise EndpointRegistrationError(
                        'select connection field option is invalid'
                    )
                option_value = _required(
                    option.get('value'), 'connection option value'
                )
                if option_value in option_values:
                    raise EndpointRegistrationError(
                        'select connection option values must be unique'
                    )
                option_values.add(option_value)
                normalized_options.append({
                    'value': option_value,
                    'label': _required(
                        option.get('label'), 'connection option label'
                    ),
                })
            if (
                'default' in value and
                value['default'] not in option_values
            ):
                raise EndpointRegistrationError(
                    'select connection field default is invalid'
                )
            value['options'] = normalized_options
        visible_when = field.get('visible_when')
        if visible_when is not None:
            if not isinstance(visible_when, dict):
                raise EndpointRegistrationError(
                    'connection field visible_when must be an object'
                )
            dependency = _required(
                visible_when.get('field_id'),
                'connection visible_when field_id',
            )
            predicates = [
                name for name in ('equals', 'in') if name in visible_when
            ]
            if len(predicates) != 1:
                raise EndpointRegistrationError(
                    'connection visible_when requires equals or in'
                )
            predicate = predicates[0]
            expected = copy.deepcopy(visible_when[predicate])
            if predicate == 'in' and (
                not isinstance(expected, list) or not expected
            ):
                raise EndpointRegistrationError(
                    'connection visible_when in must be a non-empty array'
                )
            value['visible_when'] = {
                'field_id': dependency,
                predicate: expected,
            }
        for name in ('requires_fields', 'conflicts_with'):
            if name not in field:
                continue
            references = field[name]
            if not isinstance(references, list) or not all(
                isinstance(item, str) and item.strip()
                for item in references
            ):
                raise EndpointRegistrationError(
                    f'connection field {name} must be a string array'
                )
            value[name] = [item.strip() for item in references]
        normalized.append(value)
    field_ids = {item['field_id'] for item in normalized}
    for field in normalized:
        references = []
        condition = field.get('visible_when')
        if condition:
            references.append(condition['field_id'])
        references.extend(field.get('requires_fields', []))
        references.extend(field.get('conflicts_with', []))
        if field['field_id'] in references or any(
            item not in field_ids for item in references
        ):
            raise EndpointRegistrationError(
                'connection field dependency is invalid'
            )
    return normalized


def _secret_fields(registration, connection_fields):
    fields = registration.get('secret_fields', [])
    if not isinstance(fields, list):
        raise EndpointRegistrationError(
            'endpoint secret_fields must be an array'
        )
    normalized = []
    seen_ids = set()
    seen_kinds = set()
    connection_ids = {
        field['field_id'] for field in connection_fields
    }
    for field in fields:
        if not isinstance(field, dict):
            raise EndpointRegistrationError(
                'endpoint secret field must be an object'
            )
        field_id = _required(field.get('field_id'), 'secret field_id')
        secret_kind = _required(field.get('secret_kind'), 'secret kind')
        if field_id in seen_ids or secret_kind in seen_kinds:
            raise EndpointRegistrationError(
                'endpoint secret fields must be unique'
            )
        seen_ids.add(field_id)
        seen_kinds.add(secret_kind)
        value = {
            'field_id': field_id,
            'secret_kind': secret_kind,
            'label': _required(field.get('label'), 'secret label'),
            'group': str(field.get('group') or 'Authentication'),
            'required': field.get('required') is True,
            'primary': field.get('primary') is True,
            'help': str(field.get('help') or ''),
        }
        condition = field.get('visible_when')
        if condition is not None:
            if not isinstance(condition, dict):
                raise EndpointRegistrationError(
                    'secret field visible_when must be an object'
                )
            dependency = _required(
                condition.get('field_id'),
                'secret visible_when field_id',
            )
            predicates = [
                name for name in ('equals', 'in') if name in condition
            ]
            if dependency not in connection_ids or len(predicates) != 1:
                raise EndpointRegistrationError(
                    'secret field visible_when is invalid'
                )
            predicate = predicates[0]
            expected = copy.deepcopy(condition[predicate])
            if predicate == 'in' and (
                not isinstance(expected, list) or not expected
            ):
                raise EndpointRegistrationError(
                    'secret visible_when in must be a non-empty array'
                )
            value['visible_when'] = {
                'field_id': dependency,
                predicate: expected,
            }
        normalized.append(value)
    return normalized


def _required(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise EndpointRegistrationError(f'{field_name} must not be empty')
    return value.strip()


def _interface_descriptor(registration, experience, adapter, display_name):
    """Normalize the engine/interface identity exposed to registration UIs."""
    descriptor = registration.get('interface')
    if descriptor is None:
        return {
            'engine_id': experience,
            'engine_display_name': display_name,
            'interface_id': experience,
            'interface_display_name': display_name,
            'protocol_id': adapter,
            'explicit': False,
        }
    if not isinstance(descriptor, dict):
        raise EndpointRegistrationError(
            'endpoint interface descriptor must be an object'
        )
    return {
        'engine_id': _required(
            descriptor.get('engine_id'), 'interface engine_id'
        ),
        'engine_display_name': _required(
            descriptor.get('engine_display_name'),
            'interface engine_display_name',
        ),
        'interface_id': _required(
            descriptor.get('interface_id'), 'interface interface_id'
        ),
        'interface_display_name': _required(
            descriptor.get('interface_display_name'),
            'interface interface_display_name',
        ),
        'protocol_id': _required(
            descriptor.get('protocol_id'), 'interface protocol_id'
        ),
        'explicit': True,
    }


def _load_profiles():
    provider_root = Path(__file__).parents[1] / 'providers'
    profiles = []
    for relative_path, _module_name in BUILTIN_PACKAGES:
        manifest = json.loads(
            (provider_root / relative_path).read_text(encoding='utf-8')
        )
        if not (
            manifest.get('enabled') and
            manifest.get('production_registration')
        ):
            continue
        registration = manifest.get('registration')
        if not isinstance(registration, dict):
            raise EndpointRegistrationError(
                'active provider has no endpoint registration profile'
            )
        try:
            connection_fields, secret_fields = expand_field_sets(
                registration
            )
        except KeyError as exc:
            raise EndpointRegistrationError(
                f'endpoint field set is unavailable: {exc.args[0]}'
            ) from exc
        registration = {
            **registration,
            'connection_fields': connection_fields,
            'secret_fields': secret_fields,
        }
        identity = manifest['identity']
        composition = manifest['composition']
        experiences = composition['experience_families']
        adapters = composition['target_adapter_ids']
        if len(experiences) != 1 or len(adapters) != 1:
            raise EndpointRegistrationError(
                'endpoint registration requires one experience and adapter'
            )
        default_port = registration.get('default_port')
        route_kind = registration.get('route_kind', 'network')
        if route_kind not in {'network', 'embedded_file'}:
            raise EndpointRegistrationError(
                'endpoint registration route kind is invalid'
            )
        if route_kind == 'network' and (
            not isinstance(default_port, int) or
            not 1 <= default_port <= 65535
        ):
            raise EndpointRegistrationError(
                'endpoint registration default port is invalid'
            )
        if route_kind == 'embedded_file' and default_port is not None:
            raise EndpointRegistrationError(
                'embedded endpoint registration must not declare a port'
            )
        display_name = _required(
            registration['display_name'], 'display_name'
        )
        experience = _required(experiences[0], 'experience_family')
        adapter = _required(adapters[0], 'target_adapter_id')
        interface = _interface_descriptor(
            registration, experience, adapter, display_name
        )
        connection_fields = _connection_fields(registration)
        secret_fields = _secret_fields(registration, connection_fields)
        profiles.append({
            'profile_id': _required(identity['profile_id'], 'profile_id'),
            'profile_version': _required(
                identity['profile_version'], 'profile_version'
            ),
            'provider_id': _required(identity['provider_id'], 'provider_id'),
            'provider_version': _required(
                identity['provider_version'], 'provider_version'
            ),
            'experience_family': experience,
            'target_adapter_id': adapter,
            'target_adapter_version': _required(
                registration['target_adapter_version'],
                'target_adapter_version',
            ),
            'display_name': display_name,
            'engine_id': interface['engine_id'],
            'engine_display_name': interface['engine_display_name'],
            'interface_id': interface['interface_id'],
            'interface_display_name': interface[
                'interface_display_name'
            ],
            'protocol_id': interface['protocol_id'],
            'explicit_interface': interface['explicit'],
            'workflow': _required(
                registration['workflow'], 'workflow'
            ),
            'route_kind': route_kind,
            'requires_secret': registration.get(
                'requires_secret', route_kind == 'network'
            ) is True,
            'supports_secret': registration.get(
                'supports_secret',
                bool(secret_fields) or registration.get(
                    'requires_secret', route_kind == 'network'
                ),
            ) is True,
            'default_port': default_port,
            'connection_fields': connection_fields,
            'secret_fields': secret_fields,
            'connection_capabilities': _capabilities(registration),
            'default': registration.get('default') is True,
            'available': True,
        })
    profile_ids = [item['profile_id'] for item in profiles]
    if len(profile_ids) != len(set(profile_ids)):
        raise EndpointRegistrationError(
            'endpoint registration profile IDs are not unique'
        )
    interface_ids = [
        (item['engine_id'], item['interface_id']) for item in profiles
    ]
    if len(interface_ids) != len(set(interface_ids)):
        raise EndpointRegistrationError(
            'endpoint engine/interface identities are not unique'
        )
    defaults = [item for item in profiles if item['default']]
    if len(defaults) != 1:
        raise EndpointRegistrationError(
            'exactly one endpoint registration profile must be default'
        )
    return tuple(profiles)


def _capabilities(registration):
    try:
        return normalize_connection_capabilities(
            expand_capability_set(registration)
        )
    except (ConnectionCapabilityError, KeyError) as exc:
        raise EndpointRegistrationError(str(exc)) from exc


def registration_profiles():
    """Return defensive copies of active, production-registerable profiles."""
    return copy.deepcopy(_load_profiles())


def default_registration_profile():
    """Return the one manifest-declared default registration profile."""
    return next(item for item in registration_profiles() if item['default'])


def registration_profile(profile_id=None):
    """Resolve an active profile without accepting provider implementation."""
    if profile_id is None:
        return default_registration_profile()
    profile_id = _required(profile_id, 'profile_id')
    for item in registration_profiles():
        if item['profile_id'] == profile_id:
            return item
    raise EndpointRegistrationError(
        'endpoint profile is not active for registration'
    )


def registration_interfaces(engine_id):
    """Return every selectable native interface for one logical engine."""
    engine_id = _required(engine_id, 'engine_id')
    return tuple(
        item for item in registration_profiles()
        if item['engine_id'] == engine_id
    )


def registration_interface(engine_id, interface_id):
    """Resolve one exact engine/interface pair without protocol fallback."""
    interface_id = _required(interface_id, 'interface_id')
    matches = [
        item for item in registration_interfaces(engine_id)
        if item['interface_id'] == interface_id
    ]
    if len(matches) != 1:
        raise EndpointRegistrationError(
            'endpoint engine interface is not active for registration'
        )
    return matches[0]


def registration_profile_for_endpoint(endpoint):
    """Resolve persisted exact or preserved-legacy endpoint presentation."""
    profiles = registration_profiles()
    for item in profiles:
        if item['profile_id'] == endpoint.profile_id:
            return item
    matches = [
        item for item in profiles
        if item['provider_id'] == endpoint.provider_id and
        item['workflow'] == 'legacy_preserved'
    ]
    if len(matches) == 1:
        return matches[0]
    # Preserve presentation and routing safety if a provider is disabled after
    # registration.  Provider-managed endpoints must never fall through to
    # the PostgreSQL workflow merely because their package is unavailable.
    provider_managed = endpoint.provider_version is not None
    return {
        'profile_id': endpoint.profile_id,
        'profile_version': endpoint.profile_version,
        'provider_id': endpoint.provider_id,
        'provider_version': endpoint.provider_version,
        'experience_family': endpoint.experience_family,
        'target_adapter_id': endpoint.target_adapter_id,
        'target_adapter_version': endpoint.target_adapter_version,
        'display_name': endpoint.profile_id,
        'engine_id': endpoint.experience_family,
        'engine_display_name': endpoint.experience_family,
        'interface_id': endpoint.experience_family,
        'interface_display_name': endpoint.profile_id,
        'protocol_id': endpoint.target_adapter_id,
        'explicit_interface': False,
        'workflow': (
            'provider_endpoint' if provider_managed else 'legacy_preserved'
        ),
        'route_kind': 'network',
        'requires_secret': provider_managed,
        'supports_secret': provider_managed,
        'default_port': None,
        'default': False,
        'available': False,
        'connection_fields': [],
        'secret_fields': [],
        'connection_capabilities': normalize_connection_capabilities(None),
    }


def provider_route_options(profile, data, existing=None):
    """Validate manifest-declared form values into a provider route.

    The generic server model remains unaware of provider option names. Only
    fields declared by the selected provider manifest can enter its persisted
    route, and sensitive inline values are never admitted here.
    """
    if not isinstance(profile, dict) or not isinstance(data, dict):
        raise EndpointRegistrationError(
            'provider route option input must be an object'
        )
    route = copy.deepcopy(existing or {})
    declared = {
        f"cde_route_{field['field_id']}": field
        for field in profile.get('connection_fields', [])
    }
    unknown = sorted(
        key for key in data
        if key.startswith('cde_route_') and key not in declared
    )
    if unknown:
        raise EndpointRegistrationError(
            'provider route contains undeclared connection fields: ' +
            ', '.join(unknown)
        )
    for ui_key, field in declared.items():
        active = _field_is_visible(field, profile, data, route)
        if not active:
            if ui_key in data and data[ui_key] is not None and (
                data[ui_key] != ''
            ):
                raise EndpointRegistrationError(
                    f"{field['label']} is unavailable for this selection"
                )
            route.pop(field['route_key'], None)
            continue
        if ui_key not in data:
            if field['route_key'] not in route and 'default' in field:
                route[field['route_key']] = copy.deepcopy(field['default'])
            continue
        value = data[ui_key]
        if value is None or value == '':
            route.pop(field['route_key'], None)
            continue
        control = field['control']
        if control == 'boolean' and not isinstance(value, bool):
            raise EndpointRegistrationError(
                f"{field['label']} must be true or false"
            )
        if control == 'number':
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EndpointRegistrationError(
                    f"{field['label']} must be numeric"
                )
            minimum = field.get('minimum')
            maximum = field.get('maximum')
            if minimum is not None and value < minimum or (
                maximum is not None and value > maximum
            ):
                raise EndpointRegistrationError(
                    f"{field['label']} is outside the admitted range"
                )
            if field.get('integer', True) and (
                isinstance(value, float) and not value.is_integer()
            ):
                raise EndpointRegistrationError(
                    f"{field['label']} must be an integer"
                )
            if field.get('integer', True):
                value = int(value)
        if control in {'text', 'multiline', 'file'} and not isinstance(
            value, str
        ):
            raise EndpointRegistrationError(
                f"{field['label']} must be text"
            )
        if control == 'json':
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    raise EndpointRegistrationError(
                        f"{field['label']} must be valid JSON"
                    ) from None
            if not isinstance(value, (dict, list)):
                raise EndpointRegistrationError(
                    f"{field['label']} must be a JSON object or array"
                )
        if control == 'select':
            choices = {option['value'] for option in field['options']}
            if not isinstance(value, str) or value not in choices:
                raise EndpointRegistrationError(
                    f"{field['label']} is not an admitted option"
                )
        if field.get('sensitive'):
            raise EndpointRegistrationError(
                'inline sensitive provider route values are forbidden'
            )
        route[field['route_key']] = copy.deepcopy(value)
    for field in profile.get('connection_fields', []):
        if not _field_is_visible(field, profile, data, route):
            continue
        if field.get('required') and not route.get(field['route_key']):
            raise EndpointRegistrationError(
                f"{field['label']} is required"
            )
        if not route.get(field['route_key']):
            continue
        missing = [
            item for item in field.get('requires_fields', [])
            if not _route_field_value(item, profile, data, route)
        ]
        if missing:
            raise EndpointRegistrationError(
                f"{field['label']} requires: " + ', '.join(missing)
            )
        conflicts = [
            item for item in field.get('conflicts_with', [])
            if _route_field_value(item, profile, data, route)
        ]
        if conflicts:
            raise EndpointRegistrationError(
                f"{field['label']} conflicts with: " +
                ', '.join(conflicts)
            )
    return route


def _route_field_value(field_id, profile, data, route):
    field = next(
        item for item in profile.get('connection_fields', [])
        if item['field_id'] == field_id
    )
    ui_key = f'cde_route_{field_id}'
    return data.get(
        ui_key, route.get(field['route_key'], field.get('default'))
    )


def _field_is_visible(field, profile, data, route):
    condition = field.get('visible_when')
    if condition is None:
        return True
    actual = _route_field_value(
        condition['field_id'], profile, data, route
    )
    if 'equals' in condition:
        return actual == condition['equals']
    return actual in condition['in']


def provider_route_form_values(profile, route):
    """Project safe persisted route fields back into the registration UI."""
    result = {}
    if not isinstance(route, dict):
        return result
    for field in profile.get('connection_fields', []):
        if field.get('sensitive'):
            continue
        value = route.get(field['route_key'], field.get('default'))
        if value is not None:
            if field['control'] == 'json':
                value = json.dumps(
                    value, sort_keys=True, separators=(',', ':')
                )
            result[f"cde_route_{field['field_id']}"] = copy.deepcopy(value)
    return result


def active_secret_fields(profile, route):
    """Return credential fields selected by safe, non-secret route values."""
    if not isinstance(profile, dict) or not isinstance(route, dict):
        raise EndpointRegistrationError(
            'secret field selection requires profile and route objects'
        )
    fields_by_id = {
        field['field_id']: field
        for field in profile.get('connection_fields', [])
    }
    active = []
    for field in profile.get('secret_fields', []):
        condition = field.get('visible_when')
        if condition is None:
            active.append(copy.deepcopy(field))
            continue
        dependency = fields_by_id[condition['field_id']]
        actual = route.get(
            dependency['route_key'], dependency.get('default')
        )
        visible = (
            actual == condition['equals'] if 'equals' in condition
            else actual in condition['in']
        )
        if visible:
            active.append(copy.deepcopy(field))
    return active


def provider_secret_values(profile, route, data, require_all=True):
    """Validate typed form secrets without copying them into a route."""
    if not isinstance(data, dict):
        raise EndpointRegistrationError(
            'provider secret input must be an object'
        )
    declared = {
        f"cde_secret_{field['field_id']}": field
        for field in profile.get('secret_fields', [])
    }
    unknown = sorted(
        key for key in data
        if key.startswith('cde_secret_') and key not in declared
    )
    if unknown:
        raise EndpointRegistrationError(
            'provider credentials contain undeclared fields: ' +
            ', '.join(unknown)
        )
    active = {
        field['field_id']: field
        for field in active_secret_fields(profile, route)
    }
    values = {}
    for ui_key, field in declared.items():
        submitted = data.get(ui_key)
        if field['field_id'] not in active:
            if submitted not in {None, ''}:
                raise EndpointRegistrationError(
                    f"{field['label']} is unavailable for this selection"
                )
            continue
        if submitted not in {None, ''}:
            if not isinstance(submitted, str):
                raise EndpointRegistrationError(
                    f"{field['label']} must be text"
                )
            values[field['secret_kind']] = submitted
        elif require_all and field['required']:
            raise EndpointRegistrationError(
                f"{field['label']} is required"
            )
    return values
