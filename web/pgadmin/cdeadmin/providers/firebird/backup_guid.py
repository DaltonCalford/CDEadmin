"""Safe presentation of Firebird nbackup GUID identities."""
import re

from ...sdk.relational import RelationalClientError


_GUID = re.compile(
    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
    r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')


def normalize_backup_guid(value):
    """Emit the braced GUID format required by native nbackup dispatch.

    nbackup tests the leading brace; other text is passed to atoi as a backup
    level. Accept the same hyphenated UUID with optional enclosing braces,
    then always send its unambiguous native presentation. Do not echo input.
    """
    if value is None or value == '':
        return None
    if isinstance(value, str):
        body = value.strip()
        if body.startswith('{') and body.endswith('}'):
            body = body[1:-1]
        if _GUID.fullmatch(body):
            return '{' + body.upper() + '}'
    raise RelationalClientError(
        'Firebird backup GUID must be a hyphenated UUID, with optional braces')
