"""Validate ordered gbak volume capacities before allocating native files."""
from collections.abc import Mapping

from ...sdk.relational import RelationalClientError


# Firebird 5 burp MIN_SPLIT_SIZE; Services API bkp_length is a signed int.
MIN_VOLUME_BYTES = 2048
MAX_VOLUME_BYTES = 2147483647
# hdr_split_sequence/total are four-character decimal fields (burp.h).
MAX_BACKUP_VOLUMES = 9999


def logical_backup_volumes(options):
    """Return files and N-1 byte capacities; the final volume is unbounded.

    The visual list keeps each filename paired with its capacity when moved.
    Paths remain server-native; do not resolve Windows paths on the web host.
    Alias/symlink equivalence cannot be established by this validation.
    """
    if not isinstance(options, Mapping):
        raise RelationalClientError('Firebird backup options need an object')
    split = options.get('split_backup', False)
    if not isinstance(split, bool):
        raise RelationalClientError('Split backup must be enabled or disabled')
    volumes = options.get('backup_volumes')
    if not split:
        if volumes not in (None, []):
            raise RelationalClientError(
                'Backup volumes require split backup to be enabled')
        rows = [{'filename': options.get('backup_file')}]
    else:
        if options.get('backup_file') not in (None, ''):
            raise RelationalClientError(
                'Choose a single backup file or split volumes, not both')
        if (not isinstance(volumes, list) or
                not 2 <= len(volumes) <= MAX_BACKUP_VOLUMES):
            raise RelationalClientError(
                'Split backup requires from 2 through 9999 ordered volumes')
        rows = volumes
    files, sizes = [], []
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) - {
                'filename', 'size_bytes'}:
            raise RelationalClientError('Firebird backup volume is invalid')
        name = row.get('filename')
        if (not isinstance(name, str) or not name.strip() or
                any(char in name for char in ('\x00', '\r', '\n'))):
            raise RelationalClientError(
                'Each backup volume needs a non-empty server filename '
                'without control characters')
        if name in seen:
            raise RelationalClientError('Backup volume filenames must differ')
        size = row.get('size_bytes')
        if index == len(rows) - 1:
            if size is not None and size != '':
                raise RelationalClientError(
                    'Leave the final backup volume capacity empty')
        elif (isinstance(size, bool) or not isinstance(size, int) or
              not MIN_VOLUME_BYTES <= size <= MAX_VOLUME_BYTES):
            raise RelationalClientError(
                'Each non-final backup volume requires a byte capacity '
                'from 2048 through 2147483647')
        else:
            sizes.append(size)
        files.append(name)
        seen.add(name)
    return files, sizes


def start_logical_backup(server, database, options, flags, module, callback):
    """Action-first Firebird 5 SPB, correcting driver 1.10.11's count check.

    Do not use that driver's zip_longest path: its reversed assertion rejects
    N files/N-1 sizes and can encode an extra filename 'None'.
    That driver also omits the service encoding for optional filter/encryption
    strings. Encode every string using the attached service's encoding for
    both single-file and split backups. Completion/release remain the caller's
    responsibility, as for backup().
    """
    files, sizes = logical_backup_volumes(options)
    core = module.core
    server._reset_output()
    with module.get_api().util.get_xpb_builder(
            core.XpbKind.SPB_START) as spb:
        spb.insert_tag(core.ServerAction.BACKUP)
        spb.insert_string(core.SPBItem.DBNAME, str(database),
                          encoding=server.encoding)
        for index, filename in enumerate(files):
            spb.insert_string(core.SrvBackupOption.FILE, filename,
                              encoding=server.encoding)
            if index < len(sizes):
                spb.insert_int(core.SrvBackupOption.LENGTH, sizes[index])
        if options.get('role'):
            spb.insert_string(core.SPBItem.SQL_ROLE_NAME, options['role'],
                              encoding=server.encoding)
        for field, tag in (
            ('skip_data', 'SKIP_DATA'), ('include_data', 'INCLUDE_DATA'),
            ('key_holder', 'KEYHOLDER'), ('key_name', 'KEYNAME'),
            ('crypt_plugin', 'CRYPT'), ('statistics', 'STAT'),
        ):
            if options.get(field):
                spb.insert_string(core.SrvBackupOption[tag], options[field],
                                  encoding=server.encoding)
        if options.get('parallel_workers') is not None:
            spb.insert_int(core.SrvBackupOption.PARALLEL_WORKERS,
                           options['parallel_workers'])
        spb.insert_int(core.SPBItem.OPTIONS, flags)
        if options.get('verbose', True):
            spb.insert_tag(core.SPBItem.VERBOSE)
        if options.get('verbose_interval') is not None:
            spb.insert_int(core.SPBItem.VERBINT,
                           options['verbose_interval'])
        server._svc.start(spb.get_buffer())
    for line in server:
        callback(line)
