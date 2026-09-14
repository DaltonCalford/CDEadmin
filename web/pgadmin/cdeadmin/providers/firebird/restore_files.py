"""Native gbak database-file allocations, distinct from backup volumes."""
from collections.abc import Mapping

from ...sdk.relational import RelationalClientError


MAX_FILE_PAGES = 2147483647  # Services API res_length signed integer.
MAX_FILE_START = 4294967295  # restore.epp add_files MAX_ULONG check.
MAX_DATABASE_FILES = 65536  # Primary sequence 0; USHORT file header sequence.


def _filename(value):
    if (not isinstance(value, str) or not value.strip() or
            any(char in value for char in ('\x00', '\r', '\n'))):
        raise RelationalClientError(
            'Each database file needs a non-empty server filename '
            'without control characters')
    return value


def _pages(value):
    # burp.cpp get_size returns zero as false, turning it into a filename.
    if (isinstance(value, bool) or not isinstance(value, int) or
            not 1 <= value <= MAX_FILE_PAGES):
        raise RelationalClientError(
            'Each non-final database file requires a page allocation '
            'from 1 through 2147483647')
    return value


def logical_restore_files(options):
    """Return N filenames and N-1 page allocations without changing input.

    The primary filename is fixed. Secondary visual records move together
    with their capacities. Legacy parallel arrays remain accepted, but cannot
    be combined with visual records. These are server paths, not local paths;
    exact duplicates are rejected, but remote alias equivalence is unknown.
    """
    if not isinstance(options, Mapping):
        raise RelationalClientError('Firebird restore options need an object')
    files = [_filename(options.get('restore_database'))]
    enabled = options.get('multiple_database_files', False)
    if not isinstance(enabled, bool):
        raise RelationalClientError(
            'Multiple database files must be enabled or disabled')
    records = options.get('database_file_volumes')
    primary_pages = options.get('primary_file_pages')
    additional = options.get('additional_database_files')
    legacy_pages = options.get('database_file_pages')
    if enabled:
        if additional not in (None, []) or legacy_pages not in (None, []):
            raise RelationalClientError(
                'Choose paired database files or legacy arrays, not both')
        if (not isinstance(records, list) or
                not 1 <= len(records) < MAX_DATABASE_FILES):
            raise RelationalClientError(
                'Multiple-file restore requires 1 through 65535 '
                'secondary database files')
        pages = [_pages(primary_pages)]
        for index, record in enumerate(records):
            if not isinstance(record, Mapping) or set(record) - {
                    'filename', 'pages'}:
                raise RelationalClientError(
                    'Secondary database file is invalid')
            files.append(_filename(record.get('filename')))
            value = record.get('pages')
            if index == len(records) - 1:
                if value is not None and value != '':
                    raise RelationalClientError(
                        'Leave the final database file page allocation empty')
            else:
                pages.append(_pages(value))
    else:
        if records not in (None, []) or primary_pages not in (None, ''):
            raise RelationalClientError(
                'Paired database files require multiple-file restore')
        additional = [] if additional is None else additional
        legacy_pages = [] if legacy_pages is None else legacy_pages
        if (not isinstance(additional, list) or
                not isinstance(legacy_pages, list) or
                len(additional) != len(legacy_pages) or
                len(additional) >= MAX_DATABASE_FILES):
            raise RelationalClientError(
                'Supply one page allocation per non-final database file')
        files.extend(_filename(item) for item in additional)
        pages = [_pages(item) for item in legacy_pages]
    if len(set(files)) != len(files):
        raise RelationalClientError('Database file names must differ')
    if pages:
        start = max(255, pages[0]) + 1
        for allocation in pages[1:]:
            start += allocation
            if start > MAX_FILE_START:
                raise RelationalClientError(
                    'Secondary database file starts exceed the native '
                    '32-bit page address range')
    return files, pages


def start_logical_restore(server, options, flags, module, callback):
    """Start exactly once, preserving UTF-8 plugin names and file pairings.

    Driver 1.10.11 uses the default ASCII encoding for restore encryption
    options. Build the native action-first SPB without patching that driver.
    Native completion, output limits and release remain caller-owned.
    """
    files, pages = logical_restore_files(options)
    additional = options.get('additional_backup_files')
    additional = [] if additional is None else additional
    if not isinstance(additional, list):
        raise RelationalClientError('Backup filenames require an ordered list')
    backups = [_filename(options.get('backup_file'))]
    backups.extend(_filename(item) for item in additional)
    if len(set(backups)) != len(backups):
        raise RelationalClientError('Backup volume filenames must differ')
    core = module.core
    server._reset_output()
    with module.get_api().util.get_xpb_builder(
            core.XpbKind.SPB_START) as spb:
        spb.insert_tag(core.ServerAction.RESTORE)
        for filename in backups:
            spb.insert_string(core.SrvRestoreOption.FILE, filename,
                              encoding=server.encoding)
        for index, filename in enumerate(files):
            spb.insert_string(core.SPBItem.DBNAME, filename,
                              encoding=server.encoding)
            if index < len(pages):
                spb.insert_int(core.SrvRestoreOption.LENGTH, pages[index])
        if options.get('role'):
            spb.insert_string(core.SPBItem.SQL_ROLE_NAME, options['role'],
                              encoding=server.encoding)
        for field, tag in (
            ('page_size', 'PAGE_SIZE'), ('page_buffers', 'BUFFERS'),
            ('parallel_workers', 'PARALLEL_WORKERS'),
        ):
            if options.get(field) is not None and options[field] != '':
                value = options[field]
                if field == 'page_size' and isinstance(value, str):
                    value = int(value)
                spb.insert_int(core.SrvRestoreOption[tag], value)
        access = module.DbAccessMode[options.get('access_mode', 'READ_WRITE')]
        spb.insert_bytes(core.SrvRestoreOption.ACCESS_MODE, bytes([access]))
        for field, tag in (
            ('skip_data', 'SKIP_DATA'), ('include_data', 'INCLUDE_DATA'),
            ('key_holder', 'KEYHOLDER'), ('key_name', 'KEYNAME'),
            ('crypt_plugin', 'CRYPT'), ('statistics', 'STAT'),
        ):
            if options.get(field):
                spb.insert_string(core.SrvRestoreOption[tag], options[field],
                                  encoding=server.encoding)
        if options.get('replica_mode'):
            spb.insert_int(core.SrvRestoreOption.REPLICA_MODE,
                           module.ReplicaMode[options['replica_mode']].value)
        spb.insert_int(core.SPBItem.OPTIONS, flags)
        if options.get('verbose', True):
            spb.insert_tag(core.SPBItem.VERBOSE)
        if options.get('verbose_interval') is not None:
            spb.insert_int(core.SPBItem.VERBINT, options['verbose_interval'])
        server._svc.start(spb.get_buffer())
    for line in server:
        callback(line)
