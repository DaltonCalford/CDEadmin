"""Operation-specific nbackup I/O policy, with native defaults preserved."""
from ...sdk.relational import RelationalClientError


def normalize_physical_io(operation, options):
    """Return a fresh draft with the driver's exact bool/None direct value.

    Keep old backup boolean requests usable. New forms use an explicit mode;
    accepting both fields would conceal which policy the user requested.
    Firebird 5.0.4 restore does not use the parsed direct-I/O flag. Legacy
    false/None merely requested no override effect and can safely be omitted.
    """
    value = dict(options)
    legacy = value.get('direct_io')
    if legacy is not None and not isinstance(legacy, bool):
        raise RelationalClientError(
            'Firebird direct I/O must be a boolean or native default')
    mode = value.get('direct_io_mode', 'NATIVE')
    if not isinstance(mode, str) or mode not in {'NATIVE', 'ON', 'OFF'}:
        raise RelationalClientError(
            'Firebird backup I/O mode must be NATIVE, ON, or OFF')
    if 'direct_io_mode' in value and 'direct_io' in value:
        raise RelationalClientError(
            'Choose one Firebird I/O policy field, not both')
    if operation == 'restore_physical':
        if legacy is True or mode != 'NATIVE':
            raise RelationalClientError(
                'Firebird physical restore does not implement direct I/O')
        direct = None
    elif operation == 'backup_physical':
        direct = ({'NATIVE': None, 'ON': True, 'OFF': False}[mode]
                  if 'direct_io_mode' in value else legacy)
    else:
        raise RelationalClientError('Firebird physical I/O task is invalid')
    value.pop('direct_io_mode', None)
    value['direct_io'] = direct
    return value
