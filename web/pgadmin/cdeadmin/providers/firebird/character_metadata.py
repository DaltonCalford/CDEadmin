"""Firebird collation DDL and existing character-set administration."""

import re
from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .ddl_dialect import identifier_sql


OPERATIONS = {
    'collation': frozenset({'inspect', 'create', 'comment', 'drop'}),
    'character-set': frozenset({'inspect', 'alter', 'comment'}),
}


def text(value, label):
    if not isinstance(value, str) or '\x00' in value:
        raise RelationalClientError(label + ' must be text without NUL')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise RelationalClientError(label + ' must be valid Unicode') from None
    return value


def metadata_name(value):
    """Validate native name storage without applying SQL identifier syntax."""
    value = text(value, 'Firebird identifier')
    if not value or len(value) > 63:
        raise RelationalClientError(
            'Firebird identifiers require 1 to 63 characters')
    return value


def identifier(value):
    return identifier_sql(metadata_name(value))


def literal(value):
    return "'" + text(value, 'Collation text').replace("'", "''") + "'"


def specific_attributes(records):
    if not isinstance(records, list):
        raise RelationalClientError('Collation attributes must be a list')
    result = []
    for record in records:
        if (not isinstance(record, Mapping) or 'name' not in record or
                set(record) - {'name', 'value'}):
            raise RelationalClientError(
                'Each collation attribute requires only name and value')
        name = text(record['name'], 'Collation attribute name')
        if not re.fullmatch(r'[A-Za-z_-]+', name):
            raise RelationalClientError(
                'Collation attribute names use letters, hyphens '
                'or underscores')
        value = text(record.get('value', ''), 'Collation attribute value')
        # IntlUtil::escapeAttribute, followed separately by SQL quoting.
        value = re.sub(r'([\\=;])', r'\\\1', value)
        result.append(name + '=' + value)
    # Preserve order and empty values: native last-wins and inherited removal.
    return ';'.join(result)


def compile_operation(kind, operation, draft, target=None):
    if (not isinstance(kind, str) or kind not in OPERATIONS or
            not isinstance(operation, str) or
            operation not in OPERATIONS[kind] - {'inspect'}):
        raise RelationalClientError('Unknown Firebird character metadata task')
    if not isinstance(draft, Mapping):
        raise RelationalClientError(
            'Character metadata draft must be an object')
    if operation == 'create':
        allowed = {'name', 'character_set', 'source_mode', 'base_collation',
                   'external_name', 'padding', 'case_sensitivity',
                   'accent_sensitivity', 'specific_attributes', 'description'}
    else:
        allowed = {'comment': {'description'}, 'drop': {'confirmation'},
                   'alter': {'default_collation'}}[operation]
    if set(draft) - allowed:
        raise RelationalClientError('Unknown character metadata form fields')
    name = (draft.get('name') if operation == 'create' else
            (target or {}).get('display_name'))
    object_sql = ('COLLATION ' if kind == 'collation' else 'CHARACTER SET ')
    object_sql += identifier(name)
    if operation == 'comment':
        comment = text(draft.get('description', ''), 'Comment')
        return ['COMMENT ON ' + object_sql + ' IS ' + (
            literal(comment) if comment else 'NULL')]
    if operation == 'drop':
        if draft.get('confirmation') != name:
            raise RelationalClientError('Confirm the exact collation name')
        return ['DROP ' + object_sql]
    if operation == 'alter':
        return ['ALTER ' + object_sql + ' SET DEFAULT COLLATION ' +
                identifier(draft.get('default_collation'))]
    source = 'CREATE ' + object_sql + ' FOR ' + identifier(
        draft.get('character_set'))
    mode = draft.get('source_mode', 'EXISTING')
    if mode not in ('EXISTING', 'EXTERNAL', 'SAME_NAME'):
        raise RelationalClientError('Invalid collation source mode')
    base, external = draft.get('base_collation'), draft.get('external_name')
    if (mode != 'EXISTING' and base) or (mode != 'EXTERNAL' and external):
        raise RelationalClientError('Collation source fields conflict')
    if mode == 'EXISTING':
        source += ' FROM ' + identifier(base)
    elif mode == 'EXTERNAL':
        if not external:
            raise RelationalClientError('Installed external name is required')
        # RDB$BASE_COLLATION_NAME is a native 63-character metadata name,
        # despite FROM EXTERNAL accepting a string literal in the grammar.
        metadata_name(external)
        source += ' FROM EXTERNAL (' + literal(external) + ')'
    for key, clauses in (
            ('padding', {'PAD_SPACE': 'PAD SPACE', 'NO_PAD': 'NO PAD'}),
            ('case_sensitivity', {'SENSITIVE': 'CASE SENSITIVE',
                                  'INSENSITIVE': 'CASE INSENSITIVE'}),
            ('accent_sensitivity', {'SENSITIVE': 'ACCENT SENSITIVE',
                                    'INSENSITIVE': 'ACCENT INSENSITIVE'})):
        value = draft.get(key, 'INHERIT')
        if value == 'INHERIT':
            continue
        if not isinstance(value, str) or value not in clauses:
            raise RelationalClientError('Invalid collation ' + key)
        source += ' ' + clauses[value]
    attributes = specific_attributes(draft.get('specific_attributes', []))
    if attributes:
        source += ' ' + literal(attributes)
    statements = [source]
    if draft.get('description') is not None:
        statements.extend(compile_operation(kind, 'comment', {
            'description': draft['description']}, {'display_name': name}))
    return statements


def form(kind, operation, field):
    if (kind not in OPERATIONS or
            operation not in OPERATIONS[kind] - {'inspect'}):
        raise RelationalClientError('Unknown Firebird character metadata form')
    fields = []
    if operation == 'create':
        fields = [
            field('name', 'Collation name', 'text', True),
            field('character_set', 'Character set', 'text', True, '', 'UTF8'),
            field('source_mode', 'Collation source', 'select', True,
                  'Existing copies a database collation. External uses an '
                  'installed collation implementation. Same name omits FROM '
                  'and requires an installed implementation with '
                  'the new name.',
                  'EXISTING', options=('EXISTING', 'EXTERNAL', 'SAME_NAME')),
            {**field('base_collation', 'Existing collation', 'text', True),
             'visible_when': {'field_id': 'source_mode',
                              'equals': 'EXISTING'}},
            {**field('external_name', 'Installed external name', 'text', True),
             'visible_when': {'field_id': 'source_mode',
                              'equals': 'EXTERNAL'}},
        ]
        for key, title, options in (
                ('padding', 'Space padding', ('PAD_SPACE', 'NO_PAD')),
                ('case_sensitivity', 'Case comparison',
                 ('SENSITIVE', 'INSENSITIVE')),
                ('accent_sensitivity', 'Accent comparison',
                 ('SENSITIVE', 'INSENSITIVE'))):
            help_text = 'INHERIT keeps the native attribute.'
            if key == 'accent_sensitivity':
                help_text += (
                    ' Firebird ICU collations require case-insensitive '
                    'comparison when accent-insensitive is selected. '
                    'The installed implementation validates combinations.')
            fields.append(field(key, title, 'select', True, help_text,
                                'INHERIT', options=('INHERIT', *options)))
        fields.extend([
            {**field('specific_attributes', 'Specific collation attributes',
                     'json', False,
                     'Installed implementation options, for example '
                     'NUMERIC-SORT=1. Empty values remove inherited options; '
                     'the last duplicate name wins. '
                     'Native validation applies.',
                     []),
             'json_type': 'array',
             'array_editor': {'item_kind': 'object', 'fields': [
                 field('name', 'Attribute name', 'text', True),
                 field('value', 'Attribute value', 'text', False,
                       'Empty removes the inherited attribute.', '')]}},
            field('description', 'Comment', 'multiline', False, '', ''),
        ])
    elif operation == 'alter':
        fields = [{**field('default_collation', 'Default collation',
                           'text', True,
                           'Changes the default in this database; does not '
                           'change existing column definitions.'),
                   'initial_value_path': ['default_collation'],
                   'submit_unchanged': True}]
    elif operation == 'comment':
        fields = [{**field('description', 'Comment', 'multiline', False,
                           'Empty removes the comment.', ''),
                   'initial_value_path': ['description'],
                   'submit_unchanged': True}]
    else:
        fields = [field('confirmation', 'Confirm collation name', 'text', True,
                        'Enter the exact name. Native dependency and '
                        'permission checks are not bypassed.')]
    return {'form_id': f'firebird.{kind}.{operation}',
            'title': operation.title() + ' ' + kind.replace('-', ' '),
            'fields': fields}


def recreation(kind, name, native):
    """Recreate observable metadata, not missing external implementations."""
    if kind == 'character-set':
        statements = compile_operation(kind, 'alter', {
            'default_collation': native.get('default_collation')},
            {'display_name': name})
    elif kind == 'collation':
        attributes = native.get('attributes')
        if type(attributes) is str and re.fullmatch(r'[0-7]', attributes):
            attributes = int(attributes)
        if type(attributes) is not int or not 0 <= attributes <= 7:
            raise RelationalClientError(
                'Collation attribute bits are unavailable or unknown')
        source = 'CREATE COLLATION ' + identifier(name) + ' FOR ' + identifier(
            native.get('character_set'))
        base = native.get('base_collation')
        if base:
            metadata_name(base)
            source += ' FROM EXTERNAL (' + literal(base) + ')'
        source += ' PAD SPACE' if attributes & 1 else ' NO PAD'
        source += ' CASE INSENSITIVE' if attributes & 2 else ' CASE SENSITIVE'
        source += (' ACCENT INSENSITIVE' if attributes & 4 else
                   ' ACCENT SENSITIVE')
        specific = native.get('specific_attributes')
        if specific:
            source += ' ' + literal(specific)
        statements = [source]
    else:
        raise RelationalClientError('Unknown character metadata resource')
    if native.get('description') is not None:
        statements.extend(compile_operation(kind, 'comment', {
            'description': native['description']}, {'display_name': name}))
    return statements
