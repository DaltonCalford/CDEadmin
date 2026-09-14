"""Describe requested native restore/fixup policy, never observed finality."""
from collections.abc import Mapping

from ...sdk.relational import RelationalClientError


def physical_restore_policy(operation, options):
    """Decode exact Firebird 5 flags without implying exclusive file access.

    nbackup's SEQUENCE switch preserves both the database GUID and replication
    counter. IN_PLACE applies increments to an existing offline file and leaves
    it read-only. A default new restore takes access mode from the backup;
    fixup otherwise keeps the copied file's access mode.
    """
    if operation not in ('restore_physical', 'fixup_database'):
        raise RelationalClientError('Firebird restore policy task is invalid')
    if not isinstance(options, Mapping):
        raise RelationalClientError('Firebird restore options need an object')
    field = ('restore_flags' if operation == 'restore_physical' else
             'fixup_flags')
    flags = options.get(field)
    if flags is None:
        flags = []
    allowed = ({'IN_PLACE', 'SEQUENCE'} if operation == 'restore_physical'
               else {'SEQUENCE'})
    if not isinstance(flags, list) or any(
            not isinstance(flag, str) or flag not in allowed
            for flag in flags):
        raise RelationalClientError('Firebird restore flags are invalid')
    mode = ('FIXUP' if operation == 'fixup_database' else
            'IN_PLACE' if 'IN_PLACE' in flags else 'NEW_DATABASE')
    return {
        'mode': mode,
        'replication_identity': 'PRESERVE' if 'SEQUENCE' in flags else 'RESET',
        'offline_required': mode != 'NEW_DATABASE',
        'result_access': ('READ_ONLY' if mode == 'IN_PLACE' else
                          'UNCHANGED' if mode == 'FIXUP' else 'FROM_BACKUP'),
    }
