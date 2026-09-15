##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Truthful common Properties sections for provider-owned resources."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence


PROPERTY_SECTION_ORDER = (
    'properties', 'definition', 'ddl', 'dependencies', 'dependents',
    'privileges', 'security', 'columns', 'constraints', 'indexes',
    'triggers', 'parameters', 'files', 'statistics', 'state', 'data',
    'operations',
)

# A key being present, including an empty list returned by an exact catalog
# query, is evidence that the provider supports that informational surface.
# A missing key is not interpreted as an empty result because doing so would
# invent universal engine behavior.
PROPERTY_SECTION_KEYS = {
    'definition': ('definition', 'metadata_source'),
    'ddl': ('ddl',),
    'dependencies': ('dependencies',),
    'dependents': ('dependents',),
    'privileges': ('privileges',),
    'security': ('security', 'grants', 'acls'),
    'columns': ('columns',),
    'constraints': ('constraints',),
    'indexes': ('indexes',),
    'triggers': ('triggers',),
    'parameters': ('parameters',),
    'files': ('files',),
    'statistics': ('statistics', 'stats', 'metrics'),
    'state': ('state',),
    'data': ('data',),
}


class ResourcePropertiesError(ValueError):
    """Provider property metadata violates the common resource contract."""


def _declared_sections(native):
    declared = native.get('property_sections')
    if declared is None:
        return None
    if isinstance(declared, (str, bytes)) or not isinstance(
            declared, Sequence):
        raise ResourcePropertiesError(
            'property_sections must be an ordered array'
        )
    sections = list(declared)
    if any(
            not isinstance(section, str) or
            section not in PROPERTY_SECTION_ORDER
            for section in sections):
        raise ResourcePropertiesError(
            'property_sections contains an unknown section'
        )
    if len(sections) != len(set(sections)):
        raise ResourcePropertiesError(
            'property_sections must not contain duplicates'
        )
    if not {'properties', 'operations'}.issubset(sections):
        raise ResourcePropertiesError(
            'property_sections must include properties and operations'
        )
    return sections


def normalize_resource_properties(value):
    """Return native metadata with an explicit, non-invented section list.

    Providers may declare supported sections explicitly when an empty result
    is meaningful (for example, an object with no dependencies). Otherwise
    CDEadmin admits a section only when the provider returned its canonical
    metadata key.
    """
    if not isinstance(value, Mapping):
        raise ResourcePropertiesError('native properties must be an object')
    native = copy.deepcopy(dict(value))
    declared = _declared_sections(native)
    if declared is not None:
        native['property_sections'] = declared
        return native
    sections = ['properties']
    for section in PROPERTY_SECTION_ORDER[1:-1]:
        if any(key in native for key in PROPERTY_SECTION_KEYS[section]):
            sections.append(section)
    sections.append('operations')
    native['property_sections'] = sections
    return native


__all__ = (
    'PROPERTY_SECTION_KEYS',
    'PROPERTY_SECTION_ORDER',
    'ResourcePropertiesError',
    'normalize_resource_properties',
)
