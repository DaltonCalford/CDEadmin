"""Exact gfix action/modifier selection without recovery guarantees."""

from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .service_connection import effective_service_role


# Native aliceswi.h: MEND implies VALIDATE and FULL. The public driver
# presets below retain their existing meanings for saved task drafts.
ACTIONS = {
    'VALIDATE_DB': ('VALIDATE_DB',),
    'MEND_DB': ('MEND_DB',),
    'CORRUPTION_CHECK': (
        'VALIDATE_DB', 'CHECK_DB', 'FULL', 'IGNORE_CHECKSUM'),
    'REPAIR': ('MEND_DB', 'FULL', 'IGNORE_CHECKSUM'),
    'KILL_SHADOWS': ('KILL_SHADOWS',),
    'ICU': ('ICU',),
    'UPGRADE_DB': ('UPGRADE_DB',),
}
MODIFIERS = ('FULL', 'CHECK_DB', 'IGNORE_CHECKSUM')


def flags(options):
    if not isinstance(options, Mapping):
        raise RelationalClientError('Firebird repair options are invalid')
    action = options.get('repair_action')
    if not isinstance(action, str) or action not in ACTIONS:
        raise RelationalClientError('Invalid Firebird repair action')
    modifiers = options.get('repair_modifiers', [])
    if (not isinstance(modifiers, list) or
            any(not isinstance(item, str) or item not in MODIFIERS
                for item in modifiers) or
            len(set(modifiers)) != len(modifiers)):
        raise RelationalClientError('Invalid Firebird repair modifiers')
    selected = list(ACTIONS[action])
    for modifier in MODIFIERS:
        if modifier in modifiers and modifier not in selected:
            selected.append(modifier)
    if (any(item in selected for item in ('FULL', 'CHECK_DB')) and
            not any(item in selected for item in ('VALIDATE_DB', 'MEND_DB'))):
        raise RelationalClientError(
            'Full and no-update modifiers require validation or mend')
    if action == 'UPGRADE_DB' and modifiers:
        raise RelationalClientError(
            'Firebird ODS upgrade does not accept validation modifiers')
    if ('IGNORE_CHECKSUM' in selected and
            not any(item in selected for item in ('VALIDATE_DB', 'MEND_DB'))):
        raise RelationalClientError(
            action + ' does not apply checksum-ignore; '
            'it only affects validation or mend')
    return selected


def selection(database, options, default_role=None):
    selected = flags(options)
    if not isinstance(database, str) or not database.strip():
        raise RelationalClientError(
            'Firebird repair requires an explicit database target')
    return {
        'database': database,
        'action': options['repair_action'],
        'native_flags': selected,
        'sql_role': effective_service_role(options.get('role'), default_role),
        'full_validation': 'FULL' in selected or 'MEND_DB' in selected,
        'no_update': 'CHECK_DB' in selected,
        'ignore_checksums': 'IGNORE_CHECKSUM' in selected,
        'mend_requested': 'MEND_DB' in selected,
    }


def warnings(options):
    selected = flags(options)
    result = [
        'Run maintenance against the explicitly selected database only. '
        'Keep a recoverable backup before any operation that can modify it. '
        'A successful native response does not prove that damaged data '
        'was recovered or that the database is safe for production. '
        'A native error is not proof that the database was unchanged. '
        'Inspect the outcome independently and do not automatically replay '
        'the maintenance task.']
    if 'IGNORE_CHECKSUM' in selected:
        result.append('Checksum errors will be ignored by explicit request.')
    if 'MEND_DB' in selected:
        result.append(
            'Mend prepares a corrupt database for backup and can discard '
            'damaged records. It is not a complete repair or recovery. '
            'Preserve the original and verify a subsequent backup/restore.')
    if 'CHECK_DB' in selected:
        result.append('No-update validation is requested.')
    elif 'VALIDATE_DB' in selected:
        result.append(
            'Validation may update database structures; select no-update '
            'validation when changes are not intended.')
    return result
