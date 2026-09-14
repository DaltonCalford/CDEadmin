"""Numeric levels must fit Firebird's signed incremental backup header."""
from ...sdk.relational import RelationalClientError


MAX_BACKUP_LEVEL = 32767


def normalize_backup_level(value):
    if value is None:
        return 0
    if (isinstance(value, bool) or not isinstance(value, int) or
            not 0 <= value <= MAX_BACKUP_LEVEL):
        raise RelationalClientError(
            'Firebird numeric backup level must be an integer from 0 to 32767')
    return value
