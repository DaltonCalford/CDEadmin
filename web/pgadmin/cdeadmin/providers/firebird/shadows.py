"""Firebird shadow definitions, numbered identity and native file flags."""

from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .character_metadata import literal, text
from .columns import integer
from pgadmin.cdeadmin.navigator import resource_native


OPERATIONS = frozenset({'inspect', 'create', 'drop'})
MAX_NUMBER = 32767
MAX_PAGES = 2147483647
DELETE_WARNING = (
    'Firebird 5.0.4 trims trailing spaces when deleting shadow files. '
    'Use PRESERVE FILE for such paths; native DELETE FILE may remove a '
    'different file. Physical removal requires a server administrator.')


def verify_delete_filenames(paths):
    if any(isinstance(path, str) and path.endswith(' ') for path in paths):
        raise RelationalClientError(DELETE_WARNING)


def verify_drop(cursor, number, preserve):
    """Recheck authoritative filenames inside the execution transaction."""
    if not preserve:
        cursor.execute('SELECT RDB$FILE_NAME FROM RDB$FILES '
                       'WHERE RDB$SHADOW_NUMBER = ?', (number,))
        verify_delete_filenames([row[0] for row in cursor.fetchall()])


def numeric(value, label, minimum=0, maximum=MAX_PAGES):
    try:
        return integer(value, minimum, maximum, label)
    except (ValueError, OverflowError) as error:
        raise RelationalClientError(label + ' is outside the native range') \
            from error


def filename(value):
    value = text(value, 'Server filename')
    if not value.strip() or any(char in value for char in ('\r', '\n')):
        raise RelationalClientError(
            'Provide a non-empty server filename without line breaks')
    return value


def optional_pages(value, label):
    return None if value in (None, '') else numeric(value, label)


def compile_operation(operation, draft, target=None):
    if (not isinstance(operation, str) or operation not in {'create', 'drop'}
            or not isinstance(draft, Mapping)):
        raise RelationalClientError('Unknown Firebird shadow task')
    allowed = ({'number', 'mode', 'conditional', 'filename', 'length',
                'secondary_files'} if operation == 'create' else
               {'confirmation', 'preserve_files'})
    if set(draft) - allowed:
        raise RelationalClientError('Unknown Firebird shadow form fields')
    if operation == 'drop':
        if (not isinstance(target, Mapping) or
                target.get('resource_kind') != 'shadow'):
            raise RelationalClientError('An inspected shadow is required')
        number = numeric(target.get('display_name'), 'Shadow number', 1,
                         MAX_NUMBER)
        if draft.get('confirmation') != number:
            raise RelationalClientError('Confirm the exact shadow number')
        preserve = draft.get('preserve_files', True)
        if not isinstance(preserve, bool):
            raise RelationalClientError('File preservation must be a boolean')
        if not preserve:
            verify_delete_filenames([
                item.get('filename') for item in
                resource_native(target).get('files', [])
                if isinstance(item, Mapping)])
        return ['DROP SHADOW ' + number + (
            ' PRESERVE FILE' if preserve else ' DELETE FILE')]
    number = numeric(draft.get('number'), 'Shadow number', 1, MAX_NUMBER)
    mode = draft.get('mode', 'AUTO')
    if not isinstance(mode, str) or mode not in {'AUTO', 'MANUAL'}:
        raise RelationalClientError('Choose AUTO or MANUAL shadow mode')
    conditional = draft.get('conditional', False)
    if not isinstance(conditional, bool):
        raise RelationalClientError('Conditional mode must be a boolean')
    first = filename(draft.get('filename'))
    length = optional_pages(draft.get('length'), 'First file length')
    result = ('CREATE SHADOW ' + number + ' ' + mode +
              (' CONDITIONAL' if conditional else '') + ' ' + literal(first))
    if length is not None:
        result += ' LENGTH ' + length + ' PAGES'
    records = draft.get('secondary_files', [])
    if not isinstance(records, list):
        raise RelationalClientError(
            'Secondary shadow files need an ordered list')
    names = {first}
    preceding_length = length
    for record in records:
        if not isinstance(record, Mapping) or set(record) - {
                'filename', 'start', 'length'}:
            raise RelationalClientError('Invalid secondary shadow file fields')
        name = filename(record.get('filename'))
        if name in names:
            raise RelationalClientError('Shadow file names must differ')
        names.add(name)
        start = optional_pages(record.get('start'), 'File starting page')
        length = optional_pages(record.get('length'), 'File length')
        if preceding_length in (None, '0') and start in (None, '0'):
            raise RelationalClientError(
                'A secondary file needs a starting page when its '
                'predecessor has no length')
        result += ' FILE ' + literal(name)
        if start is not None:
            result += ' STARTING AT PAGE ' + start
        if length is not None:
            result += ' LENGTH ' + length + ' PAGES'
        preceding_length = length
    return [result]


def file_flags(value, *, shadow):
    """Decode flags.h in context: bit 32 has two distinct meanings."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RelationalClientError('Invalid native file flags')
    if shadow:
        return {'shadow': bool(value & 1), 'inactive': bool(value & 2),
                'manual': bool(value & 4), 'conditional': bool(value & 16),
                'preserve_file': bool(value & 32)}
    return {'difference_file': bool(value & 32),
            'backup_active': bool(value & 64)}


def metadata(number, rows):
    """Map committed RDB$FILES rows without treating shadows as tablespaces."""
    number = int(numeric(number, 'Shadow number', 1, MAX_NUMBER))
    if not isinstance(rows, (list, tuple)) or not rows:
        raise RelationalClientError('Shadow file catalog is empty')
    files = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 5:
            raise RelationalClientError(
                'Shadow file catalog row is incomplete')
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0
               for item in row[1:]):
            raise RelationalClientError(
                'Shadow file catalog values are invalid')
        files.append({'filename': text(row[0], 'Native server filename'),
                      'sequence': row[1],
                      'start': row[2], 'length': row[3], 'flags_raw': row[4],
                      'flags': file_flags(row[4], shadow=True)})
    files.sort(key=lambda item: item['sequence'])
    if [item['sequence'] for item in files] != list(range(len(files))):
        raise RelationalClientError('Shadow file sequence is incomplete')
    first = files[0]
    draft = {'number': number, 'filename': first['filename'],
             'length': first['length'],
             'mode': 'MANUAL' if first['flags']['manual'] else 'AUTO',
             'conditional': first['flags']['conditional'],
             'secondary_files': [{key: item[key] for key in (
                 'filename', 'start', 'length')} for item in files[1:]]}
    value = {'shadow_number': number, 'files': files, 'mode': draft['mode'],
             'conditional': draft['conditional'],
             'inactive': first['flags']['inactive'],
             'registered': first['flags']['shadow'],
             'required_database_privilege': 'ALTER DATABASE',
             'creation_values': draft,
             'catalog_authority': 'RDB$FILES',
             'recreation_limitations': [
                 'Inactive state is a runtime observation, '
                 'not a CREATE option.',
                 'Recreation requires available server filenames; it does not '
                 'overwrite preserved files.']}
    if any(item['filename'].endswith(' ') for item in files):
        value['catalog_warnings'] = [DELETE_WARNING]
    try:
        value['ddl'] = compile_operation('create', draft)[0] + ';'
    except RelationalClientError as error:
        # Preserve native catalog observations even when a recorded layout
        # cannot be expressed by the accepted SQL page-count grammar.
        value['ddl'] = None
        value['ddl_unavailable'] = str(error)
    return value


def form(operation, field):
    if operation == 'drop':
        return {'form_id': 'firebird.shadow.drop', 'title': 'Drop shadow',
                'fields': [
                    field('confirmation', 'Confirm shadow number', 'text',
                          True, 'Remove this shadow definition from the '
                          'selected database.'),
                    field('preserve_files', 'Preserve shadow files', 'boolean',
                          True, 'PRESERVE FILE leaves the physical files. '
                          'Disable only to request native DELETE FILE. ' +
                          DELETE_WARNING, True),
                ]}
    if operation != 'create':
        raise RelationalClientError('Unknown Firebird shadow form')

    def pages(key, title):
        return {
            **field(key, title, 'number', False,
                    'Pages, not bytes. Empty leaves the native default.'),
            'minimum': 0, 'maximum': MAX_PAGES,
        }
    return {'form_id': 'firebird.shadow.create', 'title': 'Create shadow',
            'fields': [
                {**field('number', 'Shadow number', 'number', True,
                         'A number from 1 through 32767, unique within '
                         'this database.', 1),
                 'minimum': 1, 'maximum': MAX_NUMBER},
                field('mode', 'Failure handling', 'select', True,
                      'AUTO removes an unavailable shadow. MANUAL requires '
                      'administrator intervention when a shadow '
                      'is unavailable.',
                      'AUTO', options=('AUTO', 'MANUAL')),
                field('conditional', 'Conditional shadow', 'boolean', False,
                      'Native conditional shadow activation is controlled by '
                      'Firebird; it is not a scheduled backup.', False),
                field('filename', 'First shadow filename', 'text', True,
                      'A path on the Firebird server, '
                      'not on the browser host.'),
                pages('length', 'First shadow file length'),
                {**field('secondary_files', 'Secondary shadow files', 'json',
                         False, 'Ordered native files. A file needs '
                         'a starting '
                         'page when its predecessor has no length.', []),
                 'json_type': 'array',
                 'array_editor': {'item_kind': 'object', 'fields': [
                     field('filename', 'Shadow filename', 'text', True),
                     pages('start', 'Starting page'),
                     pages('length', 'File length'),
                 ]}},
            ]}
