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


class EndpointRegistrationError(ValueError):
    """A requested endpoint profile is unavailable or malformed."""


CONNECTION_CONTROLS = frozenset({
    'text', 'number', 'boolean', 'file', 'select',
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
        normalized.append(value)
    return normalized


def _required(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise EndpointRegistrationError(f'{field_name} must not be empty')
    return value.strip()


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
        profiles.append({
            'profile_id': _required(identity['profile_id'], 'profile_id'),
            'profile_version': _required(
                identity['profile_version'], 'profile_version'
            ),
            'provider_id': _required(identity['provider_id'], 'provider_id'),
            'provider_version': _required(
                identity['provider_version'], 'provider_version'
            ),
            'experience_family': _required(
                experiences[0], 'experience_family'
            ),
            'target_adapter_id': _required(
                adapters[0], 'target_adapter_id'
            ),
            'target_adapter_version': _required(
                registration['target_adapter_version'],
                'target_adapter_version',
            ),
            'display_name': _required(
                registration['display_name'], 'display_name'
            ),
            'workflow': _required(
                registration['workflow'], 'workflow'
            ),
            'route_kind': route_kind,
            'requires_secret': registration.get(
                'requires_secret', route_kind == 'network'
            ) is True,
            'supports_secret': registration.get(
                'supports_secret',
                registration.get('requires_secret', route_kind == 'network'),
            ) is True,
            'default_port': default_port,
            'connection_fields': _connection_fields(registration),
            'default': registration.get('default') is True,
            'available': True,
        })
    profile_ids = [item['profile_id'] for item in profiles]
    if len(profile_ids) != len(set(profile_ids)):
        raise EndpointRegistrationError(
            'endpoint registration profile IDs are not unique'
        )
    defaults = [item for item in profiles if item['default']]
    if len(defaults) != 1:
        raise EndpointRegistrationError(
            'exactly one endpoint registration profile must be default'
        )
    return tuple(profiles)


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
        if ui_key not in data:
            if field['route_key'] not in route and 'default' in field:
                route[field['route_key']] = copy.deepcopy(field['default'])
            continue
        value = data[ui_key]
        if value in {None, ''}:
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
            if isinstance(value, float) and not value.is_integer():
                raise EndpointRegistrationError(
                    f"{field['label']} must be an integer"
                )
            value = int(value)
        if control in {'text', 'file'} and not isinstance(value, str):
            raise EndpointRegistrationError(
                f"{field['label']} must be text"
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
        if field.get('required') and not route.get(field['route_key']):
            raise EndpointRegistrationError(
                f"{field['label']} is required"
            )
    return route


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
            result[f"cde_route_{field['field_id']}"] = copy.deepcopy(value)
    return result
