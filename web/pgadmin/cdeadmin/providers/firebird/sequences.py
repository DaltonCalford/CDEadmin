"""Native Firebird sequence lifecycle and explicit current/next semantics."""

from collections.abc import Mapping
import re

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .character_metadata import identifier, literal, text
from .columns import integer


OPERATIONS = frozenset({'inspect', 'create', 'alter', 'create_or_alter',
                        'recreate', 'set_current', 'comment', 'drop'})
MINIMUM = -(2 ** 63)
MAXIMUM = 2 ** 63 - 1
WARNING = (
    'Other attachments may consume values concurrently. Ordinary consumption '
    'of committed sequences is not reclaimed by row rollback. Pending '
    'sequence DDL can use transaction-local values. Current-value assignment '
    'and next-value restart are different native operations; commit the '
    'task before treating restarted values as globally published.')


def number(value, label, *, increment=False):
    # signed_long_integer is '-' NUMBER32BIT: the positive magnitude
    # must fit NUMBER32BIT, so SQL cannot express INT32_MIN here.
    minimum, maximum = (-(2 ** 31) + 1, 2 ** 31 - 1) if increment else (
        MINIMUM, MAXIMUM)
    if isinstance(value, str):
        if not re.fullmatch(r'[+-]?[0-9]+', value):
            raise RelationalClientError(f'{label} must be an integer')
        digits = value.lstrip('+-').lstrip('0') or '0'
        if len(digits) > 19:
            raise RelationalClientError(
                f'{label} exceeds the native integer range')
        value = ('-' if value.startswith('-') else '') + digits
    result = integer(value, minimum, maximum, label)
    if increment and result == '0':
        raise RelationalClientError('Sequence increment must not be zero')
    return result


def compile_operation(operation, draft, target=None):
    if (not isinstance(operation, str) or
            operation not in OPERATIONS - {'inspect'} or
            not isinstance(draft, Mapping)):
        raise RelationalClientError('Unknown Firebird sequence task')
    allowed = {
        'create': {'name', 'start', 'increment', 'description'},
        'create_or_alter': {'name', 'start', 'increment', 'restart_initial'},
        'alter': {'restart', 'restart_initial', 'increment', 'description',
                  'clear_description'},
        'recreate': {'start', 'increment', 'description', 'confirmation'},
        'set_current': {'current', 'confirmation'},
        'comment': {'description'}, 'drop': {'confirmation'},
    }[operation]
    if set(draft) - allowed:
        raise RelationalClientError('Unknown Firebird sequence form fields')
    if operation in {'create', 'create_or_alter'}:
        name = draft.get('name')
    else:
        if (not isinstance(target, Mapping) or
                target.get('resource_kind') != 'sequence'):
            raise RelationalClientError('An inspected sequence is required')
        name = target.get('display_name')
    quoted = identifier(name)
    if operation in {'drop', 'recreate', 'set_current'} and (
            draft.get('confirmation') != name):
        raise RelationalClientError('Confirm the exact sequence name')
    if operation == 'drop':
        return ['DROP SEQUENCE ' + quoted]
    if operation == 'set_current':
        return ['SET GENERATOR ' + quoted + ' TO ' +
                number(draft.get('current'), 'Current value')]
    if operation == 'comment':
        value = text(draft.get('description', ''), 'Sequence comment')
        return ['COMMENT ON SEQUENCE ' + quoted + ' IS ' + (
            literal(value) if value else 'NULL')]
    restart_initial = draft.get('restart_initial', False)
    if not isinstance(restart_initial, bool):
        raise RelationalClientError('Restart choice must be a boolean')
    key = 'restart' if operation == 'alter' else 'start'
    specified = draft.get(key) not in (None, '')
    if specified and restart_initial:
        raise RelationalClientError(
            'Choose an explicit restart or original start')
    clauses = []
    if restart_initial:
        clauses.append('RESTART')
    elif specified:
        clauses.append(('RESTART WITH ' if operation == 'alter' else
                        'START WITH ') + number(draft[key], 'Start value'))
    if draft.get('increment') not in (None, ''):
        clauses.append('INCREMENT BY ' + number(
            draft['increment'], 'Increment', increment=True))
    prefix = {'create': 'CREATE', 'alter': 'ALTER',
              'create_or_alter': 'CREATE OR ALTER',
              'recreate': 'RECREATE'}[operation]
    statements = ([prefix + ' SEQUENCE ' + quoted +
                   (' ' + ' '.join(clauses) if clauses else '')]
                  if clauses or operation in {'create', 'recreate'} else [])
    if operation == 'create_or_alter' and not clauses:
        raise RelationalClientError(
            'CREATE OR ALTER requires a start, restart or increment clause')
    clear = draft.get('clear_description', False)
    if not isinstance(clear, bool):
        raise RelationalClientError('Remove comment choice must be a boolean')
    if clear and draft.get('description') not in (None, ''):
        raise RelationalClientError('Choose a comment or remove it, not both')
    if clear or 'description' in draft:
        statements.extend(compile_operation('comment', {
            'description': '' if clear else draft['description']}, {
                'resource_kind': 'sequence', 'display_name': name}))
    if not statements:
        raise RelationalClientError('Sequence alteration has no changes')
    return statements


def form(operation, field):
    if (not isinstance(operation, str) or
            operation not in OPERATIONS - {'inspect'}):
        raise RelationalClientError('Unknown Firebird sequence form')
    fields = []
    if operation in {'create', 'create_or_alter'}:
        fields.append(field('name', 'Sequence name', 'text', True))
    if operation in {'create', 'create_or_alter', 'recreate'}:
        fields.append({**field(
            'start', 'Start value', 'text', False,
            'Signed 64-bit integer. Creation defaults to 1, including '
            'negative increments. For an existing sequence, CREATE OR ALTER '
            'restarts the next value; the stored initial value is unchanged.'),
            **({'initial_value_path': ['initial_value'],
                'submit_unchanged': True}
               if operation == 'recreate' else {})})
    if operation == 'alter':
        fields.append(field('restart', 'Next generated value', 'text', False,
                            '64-bit integer. Empty preserves position.'))
    if operation in {'alter', 'create_or_alter'}:
        fields.append(field('restart_initial', 'Restart at original start',
                            'boolean', False, default=False))
    if operation in {'create', 'alter', 'create_or_alter', 'recreate'}:
        fields.append({**field(
            'increment', 'Increment by', 'number', False,
            'Nonzero integer from -2147483647 to 2147483647. '
            'Creation defaults to 1.'),
            'minimum': -(2 ** 31) + 1, 'maximum': 2 ** 31 - 1,
            'submit_unchanged': operation == 'recreate',
            **({'initial_value_path': ['increment']}
               if operation in {'alter', 'recreate'} else {})})
    if operation == 'set_current':
        fields.append({**field(
            'current', 'Current generator value', 'text', True,
            'SET GENERATOR sets the current value, not the next value. '
            'Signed 64-bit integer. ' + WARNING),
            'initial_value_path': ['state', 'current_value']})
    if operation in {'create', 'alter', 'recreate', 'comment'}:
        fields.append({**field('description', 'Comment', 'multiline', False,
                               'Empty removes the comment.'),
                       'initial_value_path': ['description'],
                       'submit_unchanged': operation in {
                           'comment', 'recreate'}})
    if operation == 'alter':
        fields.append(field('clear_description', 'Remove comment', 'boolean',
                            False, default=False))
    if operation in {'drop', 'recreate', 'set_current'}:
        fields.append(field('confirmation', 'Confirm sequence name',
                            'text', True, (
                                'RECREATE replaces the sequence and its '
                                'grants. Native dependencies may prevent it.'
                                if operation == 'recreate' else
                                'Remove only this sequence. Native '
                                'dependencies may prevent removal.'
                                if operation == 'drop' else
                                'Confirm the exact sequence whose current '
                                'value will be assigned.')))
    titles = {'create': 'Create sequence', 'alter': 'Alter sequence',
              'create_or_alter': 'Create or alter sequence',
              'recreate': 'Recreate sequence', 'drop': 'Drop sequence',
              'set_current': 'Set current generator value',
              'comment': 'Edit sequence comment'}
    return {'form_id': 'firebird.sequence.' + operation,
            'title': titles[operation], 'fields': fields}
