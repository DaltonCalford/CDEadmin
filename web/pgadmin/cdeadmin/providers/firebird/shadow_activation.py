"""Explicit shadow recovery target and native header verification.

Firebird's activation API is not a generic reconnect: MET_activate_shadow
rewrites RDB$FILES and clears the active-shadow header bit on its target.
Never substitute the currently selected primary database for the shadow path.
"""

from collections.abc import Mapping
import re

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .character_metadata import text
from .service_connection import validate_service_role


WARNING = (
    'This promotes the first shadow file into an independent database in '
    'place. Stop or isolate the original database before recovery; do not '
    'operate both copies as the same database. The target must be an active '
    'shadow, not the original database or a secondary shadow file. '
    'Native header verification is performed immediately before activation, '
    'but cannot prevent another server administrator replacing a file. '
    'Coordinate exclusive control of the recovery files. Firebird 5.0.4 may '
    'activate a shadow before rejecting insufficient utility privileges. '
    'An authorization error is not proof that the shadow is unchanged: '
    'inspect its native header before any further recovery attempt.')


def validate(draft, original_database=None):
    if not isinstance(draft, Mapping) or set(draft) - {
            'shadow_filename', 'confirmation', 'original_isolated', 'role'}:
        raise RelationalClientError('Unknown shadow activation form fields')
    filename = text(draft.get('shadow_filename'), 'Shadow filename')
    if (not filename.strip() or filename.endswith(' ') or
            any(char in filename for char in '\r\n')):
        raise RelationalClientError(
            'Enter an exact first-shadow filename without line breaks '
            'or trailing spaces')
    if original_database and filename == original_database:
        raise RelationalClientError(
            'The activation target must not be the original database')
    if draft.get('confirmation') != filename:
        raise RelationalClientError('Confirm the exact shadow filename')
    if draft.get('original_isolated') is not True:
        raise RelationalClientError(
            'Confirm that the original database is stopped or isolated')
    role = draft.get('role')
    validate_service_role(role)
    if role is not None:
        role = text(role, 'SQL role')
        if len(role) > 63:
            raise RelationalClientError('SQL role exceeds 63 characters')
    return {'shadow_filename': filename, 'role': role or None}


def verify_header(lines, *, truncated=False):
    """Recognize the exact 5.0.4 gstat primary-header attribute output.

    Header output is fixed literal text in gstat/ppg.cpp, not a localized
    client description. Unknown, truncated or ambiguous output fails closed.
    """
    if (truncated or not isinstance(lines, list) or
            not all(isinstance(line, str) for line in lines)):
        raise RelationalClientError('Native shadow header is unavailable')
    text_lines = '\n'.join(lines).splitlines()
    starts = [index for index, line in enumerate(text_lines)
              if line.strip() == 'Database header page information:']
    if len(starts) != 1:
        raise RelationalClientError('Native shadow header is ambiguous')
    header = []
    ended = False
    for line in text_lines[starts[0] + 1:]:
        if line.strip() == 'Variable header data:':
            ended = True
            break
        if line.strip() == 'Database overflow header page information:':
            break
        header.append(line)
    if not ended:
        raise RelationalClientError('Native shadow header is incomplete')
    sequences = [match.group(1) for line in header if (
        match := re.fullmatch(r'\s*Sequence number\s+(\d+)\s*', line))]
    attributes = [match.group(1) for line in header if (
        match := re.fullmatch(r'\s*Attributes\s+(.+?)\s*', line))]
    if sequences != ['0']:
        raise RelationalClientError(
            'Activation requires the first shadow file (sequence zero)')
    if len(attributes) != 1 or 'active shadow' not in {
            token.strip() for token in attributes[0].split(',')}:
        raise RelationalClientError(
            'The native header does not identify an active shadow')
    return {'first_file_verified': True, 'active_shadow_verified': True}


def form(field):
    return {'form_id': 'firebird_activate_shadow',
            'title': 'Activate a Firebird database shadow', 'fields': [
                field('shadow_filename', 'First shadow filename', 'text',
                      True, WARNING),
                field('confirmation', 'Confirm shadow filename', 'text', True,
                      'Repeat the exact recovery target, not the original '
                      'database filename.'),
                field('original_isolated',
                      'The original database is stopped or isolated',
                      'boolean', True, WARNING, False),
                field('role', 'SQL role', 'text', False,
                      'Optional native service role. Whitespace in role names '
                      'cannot be safely transported by Firebird 5.0.4 '
                      'Services API utility dispatch.'),
            ]}
