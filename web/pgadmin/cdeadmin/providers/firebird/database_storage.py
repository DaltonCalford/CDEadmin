"""Native database-file and difference-file tasks; no generic tablespaces."""

from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .character_metadata import text, literal
from .shadows import optional_pages


OPERATIONS = frozenset({
    'add_files', 'add_difference_file', 'drop_difference_file',
    'begin_backup', 'end_backup',
})
WARNINGS = {
    'add_files': (
        'This permanently extends the selected database storage. Firebird '
        'resolves page positions against existing allocation; there is no '
        'native DROP FILE or rename-file operation. Other open attachments '
        'can prevent file extension; coordinate maintenance first. '
        'This task does not disconnect other users. Firebird 5.0.4 can fail '
        'with an SCN-page I/O error when new file starting pages leave large '
        'allocation gaps after a physical backup-mode cycle. Review native '
        'allocation and avoid speculative page positions; a failed mutation '
        'is never automatically retried.'),
    'add_difference_file': (
        'This configures a server-side difference filename. It does not '
        'start backup mode or copy database contents. Firebird enforces '
        'the required normal backup state.'),
    'drop_difference_file': (
        'This removes the configured difference-file definition, not an '
        'arbitrary server file. Firebird enforces its backup-state rules.'),
    'begin_backup': (
        'This freezes main-file writes and redirects changes to the '
        'difference file. It does not copy a backup. Coordinate the external '
        'backup and finish with End physical backup mode; monitor free space. '
        'Firebird creates or truncates the difference file: any previous '
        'contents at that path are overwritten.'),
    'end_backup': (
        'This asks Firebird to merge pending difference-file changes and '
        'leave physical backup mode. It does not restore an external backup.'),
}


def server_filename(value):
    value = text(value, 'Server filename')
    if not value:
        raise RelationalClientError('A server filename is required')
    return value


def verify_difference_path(cursor, operation, filename=None):
    """Reject known database-file collisions using execution-time metadata.

    This is not filesystem authorization or a symlink resolver. Firebird's
    server filesystem remains authoritative; the overwrite acknowledgment is
    still mandatory for paths not identifiable through database metadata.
    """
    if operation not in {'add_difference_file', 'begin_backup'}:
        raise RelationalClientError('Invalid difference-file safety check')
    cursor.execute('SELECT MON$DATABASE_NAME FROM MON$DATABASE')
    row = cursor.fetchone()
    if not row or not isinstance(row[0], str) or not row[0]:
        raise RelationalClientError('Native database filename is unavailable')
    primary = row[0]
    cursor.execute('SELECT RDB$FILE_NAME, RDB$SHADOW_NUMBER, '
                   'RDB$FILE_FLAGS FROM RDB$FILES')
    files = cursor.fetchall()
    protected = {primary}
    difference = []
    for name, shadow, flags in files:
        if not shadow and (flags or 0) & 32:
            difference.append(name)
        elif name is not None:
            protected.add(name)
    if operation == 'begin_backup':
        if len(difference) > 1:
            raise RelationalClientError('Ambiguous native difference metadata')
        filename = difference[0] if difference else None
        filename = primary + '.delta' if filename is None else filename
    filename = server_filename(filename)
    if filename.endswith(' '):
        raise RelationalClientError(
            'The difference-file path has unsafe trailing spaces')
    if filename in protected:
        raise RelationalClientError(
            'The difference-file path identifies a known database or shadow '
            'file. Select a dedicated difference-file path.')
    return filename


def compile_operation(operation, draft, database):
    if (not isinstance(operation, str) or operation not in OPERATIONS or
            not isinstance(draft, Mapping)):
        raise RelationalClientError('Unknown Firebird database storage task')
    database = server_filename(database)
    if draft.get('confirmation') != database:
        raise RelationalClientError('Confirm the exact database path or alias')
    allowed = {'confirmation'} | (
        {'files'} if operation == 'add_files' else
        {'filename'} if operation == 'add_difference_file' else
        {'confirm_difference_overwrite'} if operation == 'begin_backup'
        else set())
    if set(draft) - allowed:
        raise RelationalClientError('Unknown database storage form fields')
    if operation == 'add_difference_file':
        filename = server_filename(draft.get('filename'))
        if filename == database:
            raise RelationalClientError(
                'The difference file must not be the selected database')
        if filename.endswith(' '):
            raise RelationalClientError(
                'Firebird 5.0.4 trims trailing spaces when configuring '
                'the physical difference-file path. Choose an exact path '
                'without trailing spaces.')
        return ['ALTER DATABASE ADD DIFFERENCE FILE ' + literal(filename)]
    if operation != 'add_files':
        if operation == 'begin_backup' and draft.get(
                'confirm_difference_overwrite') is not True:
            raise RelationalClientError(
                'Confirm that the difference-file path may be overwritten')
        return ['ALTER DATABASE ' + {
            'drop_difference_file': 'DROP DIFFERENCE FILE',
            'begin_backup': 'BEGIN BACKUP',
            'end_backup': 'END BACKUP',
        }[operation]]
    files = draft.get('files')
    if not isinstance(files, list) or not files:
        raise RelationalClientError('Add at least one database file')
    clauses = []
    names = set()
    for record in files:
        if not isinstance(record, Mapping) or set(record) - {
                'filename', 'start', 'length'}:
            raise RelationalClientError('Invalid database file fields')
        filename = server_filename(record.get('filename'))
        if filename in names:
            raise RelationalClientError('Database file names must differ')
        names.add(filename)
        clause = 'FILE ' + literal(filename)
        start = optional_pages(record.get('start'), 'Starting page')
        length = optional_pages(record.get('length'), 'File length')
        if start is not None:
            clause += ' STARTING AT PAGE ' + start
        if length is not None:
            clause += ' LENGTH ' + length + ' PAGES'
        clauses.append(clause)
    return ['ALTER DATABASE ADD ' + ' '.join(clauses)]


def form(operation, field):
    if operation not in OPERATIONS:
        raise RelationalClientError('Unknown Firebird database storage form')
    titles = {
        'add_files': 'Add database files',
        'add_difference_file': 'Define backup difference file',
        'drop_difference_file': 'Remove backup difference-file definition',
        'begin_backup': 'Begin physical backup mode',
        'end_backup': 'End physical backup mode',
    }
    fields = [field(
        'confirmation', 'Confirm database path or alias', 'text', True,
        'The exact selected database target. ' + WARNINGS[operation])]
    if operation == 'add_files':
        fields.append({
            **field('files', 'Additional database files', 'json', True,
                    'Ordered files on the engine host. Page positions are '
                    'resolved by Firebird against the existing allocation. '
                    'There is no native DROP FILE or rename-file task.', []),
            'json_type': 'array',
            'array_editor': {'item_kind': 'object', 'fields': [
                field('filename', 'Server filename', 'multiline', True,
                      'The path is literal, including whitespace.'),
                *[{**field(key, label, 'number', False,
                           'Pages, not bytes. '
                           'Empty leaves the native default.'),
                   'minimum': 0, 'maximum': 2147483647}
                  for key, label in (('start', 'Starting page'),
                                     ('length', 'File length'))],
            ]},
        })
    elif operation == 'add_difference_file':
        fields.append(field(
            'filename', 'Difference filename', 'multiline', True,
            'A server path, not a browser path. Firebird rejects a second '
            'definition until the existing definition is removed.'))
    elif operation == 'begin_backup':
        fields.append(field(
            'confirm_difference_overwrite',
            'I confirm the difference-file path may be overwritten',
            'boolean', True,
            'Use only a dedicated difference-file path. This is not a backup '
            'destination and must not identify another database or user file.',
            False))
    return {'form_id': 'firebird.database.' + operation,
            'title': titles[operation], 'fields': fields}
